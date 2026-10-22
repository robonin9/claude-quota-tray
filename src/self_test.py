"""
``python src/main.py --self-test`` — smoke test without a token or network.

Opens every window and the tray icon against fake usage data in a throwaway
data folder, checks that only one Tk root was ever created, then exits:
0 = passed, 1 = something failed, 2 = hung (watchdog). Used by CI on Windows
and macOS, and by testers who can run the app where we can't.

Tk owns the main thread here (``ui_host.run_forever``) and the checks run on
a worker thread — the layout macOS requires.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import traceback
from types import ModuleType

TIMEOUT_SECONDS = 30
SCREENSHOT_ENV = "CQT_SELFTEST_SCREENSHOT"


def prepare_data_dir() -> None:
    """Point CQT_DATA_DIR at a temp folder unless the caller chose one.

    main.py calls this before importing anything else: some modules read
    settings at import time, and that must not create or rewrite the user's
    real ~/.claude-quota-tray files.
    """
    import settings as user_settings  # import-light: no file access

    if not os.environ.get(user_settings.DATA_DIR_ENV, "").strip():
        os.environ[user_settings.DATA_DIR_ENV] = tempfile.mkdtemp(prefix="cqt-selftest-")


def run(app: ModuleType) -> int:
    """``app`` is the running main module (it may be ``__main__``)."""
    import settings as user_settings

    prepare_data_dir()
    user_settings._cache = None
    data_dir = user_settings.data_dir()

    watchdog = threading.Timer(TIMEOUT_SECONDS, _hung)
    watchdog.daemon = True
    watchdog.start()

    import tkinter
    tk_inits: list[str] = []
    orig_init = tkinter.Tk.__init__

    def counting_init(self, *a, **kw):
        tk_inits.append(threading.current_thread().name)
        orig_init(self, *a, **kw)

    tkinter.Tk.__init__ = counting_init

    import ui_host
    result: dict = {"problems": [], "lines": []}
    worker = threading.Thread(
        target=_checks, args=(app, ui_host, tk_inits, result), name="selftest",
        daemon=True,
    )
    worker.start()
    ui_host.run_forever()
    worker.join(timeout=5)
    watchdog.cancel()
    tkinter.Tk.__init__ = orig_init

    problems = result["problems"]
    lines = result["lines"]
    lines.append(f"data dir: {data_dir}")
    lines.append("RESULT: " + ("PASS" if not problems else f"FAIL ({len(problems)})"))
    report = "\n".join(lines)
    print(report)
    for p in problems:
        print(p, file=sys.stderr)
    try:
        with open(user_settings.error_log_path(), "a", encoding="utf-8") as f:
            f.write("\n=== self-test ===\n" + report + "\n")
            for p in problems:
                f.write(p + "\n")
    except OSError:
        pass
    return 0 if not problems else 1


def _hung() -> None:
    sys.stderr.write(f"self-test: no result after {TIMEOUT_SECONDS}s — hung\n")
    sys.stderr.flush()
    os._exit(2)


def _checks(app, ui_host, tk_inits, result) -> None:
    lines, problems = result["lines"], result["problems"]

    def step(name, fn):
        try:
            detail = fn()
            lines.append(f"[ OK ] {name}" + (f": {detail}" if detail else ""))
            return True
        except Exception:
            lines.append(f"[FAIL] {name}")
            problems.append(f"--- {name} ---\n{traceback.format_exc()}")
            return False

    try:
        if not ui_host._ready.wait(timeout=10) or not ui_host.is_alive():
            lines.append("[FAIL] Tk did not start")
            problems.append("Tk did not start (no display?)")
            return
        lines.append(f"python {sys.version.split()[0]} on {sys.platform}")
        step("fake usage data", lambda: _fake_state(app))
        icon_box: dict = {}
        step("tray icon", lambda: _tray(app, ui_host, icon_box))
        step("tray menu callables", lambda: _walk_menu(app.build_menu()))
        step("open windows", lambda: _open_windows(app))
        _settle(ui_host)
        step("windows on screen", lambda: _count_windows(ui_host))
        step("screenshot", _screenshot)
        step("single Tk root", lambda: _one_tk(tk_inits))
        step("close windows", lambda: _close_windows(ui_host))
        step("stop tray icon", lambda: _stop_tray(icon_box))
    except Exception:
        problems.append(traceback.format_exc())
    finally:
        ui_host.shutdown()


def _fake_state(app) -> str:
    import history
    from api_client import UsageSnapshot

    now = time.time()
    snap = UsageSnapshot(
        session_pct=42, session_reset_seconds=3600, session_status=None,
        weekly_pct=17, weekly_reset_seconds=5 * 86400, weekly_status=None,
        ok=True, fetched_at=now,
    )
    acct = {"id": "selftest", "name": "Self-test", "mode": "auto", "path": None}
    app.state.active_account = acct
    app.state.snapshot = snap
    app.state.last_poll_at = now
    for minutes_ago, pct in ((50, 30), (25, 36), (0, 42)):
        history.record(acct["id"], UsageSnapshot(
            session_pct=pct, session_reset_seconds=3600, session_status=None,
            weekly_pct=17, weekly_reset_seconds=5 * 86400, weekly_status=None,
            ok=True, fetched_at=now - minutes_ago * 60,
        ))
    app.state.burn = history.burn_rate(60, acct["id"])
    return f"session {snap.session_pct}%, weekly {snap.weekly_pct}%"


def _tray(app, ui_host, box) -> str:
    from icon_renderer import render_icon

    ready = threading.Event()

    def make():
        icon = app.TrayIcon(
            "claude-quota-selftest", icon=render_icon(42), title="self-test",
            menu=app.build_menu(),
        )
        icon.run_detached(setup=lambda _icon: ready.set())
        return icon

    def show(icon):
        icon.visible = True
        app._apply_tray_update(icon)

    # AppKit objects must be created and changed on the main thread, which
    # Tk owns on macOS; on Windows pystray runs its own message-loop thread.
    on_main = sys.platform == "darwin"
    icon = ui_host.call_sync(make, timeout=10) if on_main else make()
    box["icon"] = icon
    if not ready.wait(timeout=10):
        raise RuntimeError("tray icon never became ready")
    if on_main:
        ui_host.call_sync(show, icon, timeout=10)
    else:
        show(icon)
    return type(icon).__name__


def _walk_menu(menu) -> str:
    return f"{_count_menu(menu)} items"


def _count_menu(menu) -> int:
    """Evaluate every text/checked/visible callable, as a menu rebuild would."""
    count = 0
    for item in menu.items:
        _ = item.text, item.checked, item.visible, item.enabled
        count += 1
        if item.submenu:
            count += _count_menu(item.submenu)
    return count


def _open_windows(app) -> str:
    import desktop_widget
    import history_window
    import settings_dialogs
    import status_window

    acct = app.state.active_account
    if not status_window.show(acct["name"], app._current_data):
        raise RuntimeError("status window did not open")
    history_window.show(acct["id"], acct["name"], get_data=app._current_data)
    settings_dialogs.open_accounts(lambda: None)
    settings_dialogs.open_thresholds(lambda: None)
    settings_dialogs.open_connect_claude(lambda: None)
    opened = ["status", "history", "accounts", "thresholds", "connect"]
    if desktop_widget.is_supported():
        desktop_widget.show(app._current_data, lambda: None)
        opened.append("desktop widget")
    return ", ".join(opened)


def _settle(ui_host) -> None:
    for _ in range(5):
        ui_host.call_sync(lambda: ui_host.root().update(), timeout=10)
        time.sleep(0.3)


def _count_windows(ui_host) -> str:
    import tkinter

    def count():
        return sum(isinstance(w, tkinter.Toplevel) and w.winfo_viewable()
                   for w in ui_host.root().winfo_children())

    n = ui_host.call_sync(count, timeout=10)
    if n < 5:
        raise RuntimeError(f"only {n} windows are visible")
    return f"{n} visible"


def _screenshot() -> str:
    out = os.environ.get(SCREENSHOT_ENV, "").strip()
    if not out:
        return "skipped (set CQT_SELFTEST_SCREENSHOT=<dir>)"
    try:
        from PIL import ImageGrab
        os.makedirs(out, exist_ok=True)
        path = os.path.join(out, f"selftest-{sys.platform}.png")
        ImageGrab.grab().save(path)
        return path
    except Exception as e:  # best effort: CI runners may refuse screen capture
        return f"not captured ({e})"


def _one_tk(tk_inits) -> str:
    if len(tk_inits) != 1:
        raise RuntimeError(f"tk.Tk() created {len(tk_inits)} times: {tk_inits}")
    return f"created once on {tk_inits[0]!r}"


def _close_windows(ui_host) -> str:
    def close():
        for w in list(ui_host.root().winfo_children()):
            w.destroy()
        return len(ui_host.root().winfo_children())

    left = ui_host.call_sync(close, timeout=10)
    if left:
        raise RuntimeError(f"{left} windows left open")
    return None


def _stop_tray(box) -> str:
    icon = box.get("icon")
    if icon is None:
        return "no icon"
    icon.stop()
    return None
