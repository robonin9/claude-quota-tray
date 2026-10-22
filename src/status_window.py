"""
Compact status popup shown on left-click of the tray icon.

Just the two progress bars and the burn-rate summary — no chart.
Auto-refreshes every 5 seconds while open.
"""

from __future__ import annotations

import time
import tkinter as tk
import traceback
from pathlib import Path
from typing import Callable, Optional

import ui_host
from bar_widget import (
    BG, BTN_BG, BTN_BG_ACTIVE, MUTED, TEXT,
    apply_bar, build_bar, format_burn, refresh_color_constants, ui_font,
)
from i18n import t
from ui_theme import colors


# Only touched on the UI thread.
_open_window: Optional[tk.Toplevel] = None

SnapshotFetcher = Callable[[], dict]


def _log_error(where: str) -> None:
    try:
        log = Path.home() / ".claude-quota-tray" / "error.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a", encoding="utf-8") as f:
            f.write(
                f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] status_window:{where}\n"
            )
            f.write(traceback.format_exc())
    except Exception:
        pass


def show(account_name: str, get_data: SnapshotFetcher,
         on_open_history: Optional[Callable[[], None]] = None) -> bool:
    """
    Open (or focus) the compact status popup.
    Returns True if the window is up, False on failure so the caller can
    fall back to another window.
    """
    try:
        return bool(ui_host.call_sync(
            _open_or_focus, account_name, get_data, on_open_history,
            timeout=3.0,
        ))
    except Exception:
        _log_error("show")
        return False


def _bring_to_front(win: tk.Toplevel) -> None:
    try:
        win.deiconify()
        win.lift()
        win.focus_force()
        win.attributes("-topmost", True)
        ui_host.after(300, lambda: _safe_set_topmost(win, False))
    except tk.TclError:
        pass


def _forget(win: tk.Toplevel) -> None:
    global _open_window
    if _open_window is win:
        _open_window = None


def _open_or_focus(account_name: str, get_data: SnapshotFetcher,
                   on_open_history: Optional[Callable[[], None]] = None) -> bool:
    global _open_window
    if _open_window is not None and _open_window.winfo_exists():
        _bring_to_front(_open_window)
        return True
    _open_window = None
    root: Optional[tk.Toplevel] = None
    try:
        root = tk.Toplevel(ui_host.root())
        root.title(t('window.status_title', name=account_name))
        root.configure(bg=BG)
        root.minsize(360, 240)

        w, h = 420, 300
        try:
            sw = root.winfo_screenwidth()
            sh = root.winfo_screenheight()
            x = max(0, sw - w - 32)
            y = max(0, sh - h - 80)
        except tk.TclError:
            x, y = 100, 100
        root.geometry(f"{w}x{h}+{x}+{y}")

        try:
            root.attributes("-topmost", True)
            ui_host.after(400, lambda: _safe_set_topmost(root, False))
        except tk.TclError:
            pass

        _build_widgets(root, account_name, get_data, on_open_history=on_open_history)
        _open_window = root
        ui_host.on_destroy(root, lambda: _forget(root))
        return True
    except Exception:
        _log_error("init")
        try:
            if root is not None:
                root.destroy()
        except Exception:
            pass
        return False


def _build_widgets(root: tk.Toplevel, account_name: str,
                   get_data: SnapshotFetcher,
                   on_open_history: Optional[Callable[[], None]] = None) -> None:
    refresh_color_constants()
    c = colors()
    root.configure(bg=c["BG"])
    header = tk.Frame(root, bg=c["BG"])
    header.pack(fill="x", padx=14, pady=(12, 0))
    title_box = tk.Frame(header, bg=c["BG"])
    title_box.pack(side="left")
    tk.Label(
        title_box, text=account_name,
        font=ui_font(11, "bold"),
        fg=c["TEXT"], bg=c["BG"],
    ).pack(side="left")
    plan_lbl = tk.Label(
        title_box, text="", font=ui_font(9, "bold"),
        fg=c["ACCENT"], bg=c["BG"],
    )
    plan_lbl.pack(side="left", padx=(8, 0))
    burn_lbl = tk.Label(
        header, text="", font=ui_font(9),
        fg=c["MUTED"], bg=c["BG"], justify="right",
    )
    burn_lbl.pack(side="right")

    panel = tk.Frame(root, bg=c["BG"])
    panel.pack(fill="x", padx=14, pady=(8, 4))

    session_bar = build_bar(panel, t('bar.session_label'))
    session_bar["frame"].pack(fill="x", pady=(0, 8))
    weekly_bar = build_bar(panel, t('bar.weekly_label'))
    weekly_bar["frame"].pack(fill="x")
    # Per-model weekly limits (Opus, Sonnet, …) — one bar each, created on
    # demand because which limits exist depends on the plan.
    scoped_bars: dict = {}

    footer = tk.Frame(root, bg=c["BG"])
    footer.pack(fill="x", padx=14, pady=(8, 12), side="bottom")

    health_lbl = tk.Label(
        root, text="", font=ui_font(8),
        fg=c["MUTED"], bg=c["BG"], anchor="w", justify="left",
    )
    health_lbl.pack(fill="x", padx=14, pady=(0, 4), side="bottom")

    def _refresh():
        try:
            data = get_data()
        except Exception:
            data = {}
        apply_bar(session_bar, data.get("session_pct"), data.get("session_reset"))
        apply_bar(weekly_bar, data.get("weekly_pct"), data.get("weekly_reset"))
        rows = data.get("scoped")
        if rows is None and data.get("opus_pct") is not None:
            rows = [{"key": "opus", "label": t('bar.opus_label'),
                     "pct": data.get("opus_pct"), "reset": data.get("opus_reset")}]
        rows = rows or []
        live = {r["key"] for r in rows}
        for key in list(scoped_bars):
            if key not in live:
                scoped_bars.pop(key)["frame"].destroy()
        grew = False
        for r in rows:
            bar = scoped_bars.get(r["key"])
            if bar is None:
                bar = build_bar(panel, r["label"])
                bar["frame"].pack(fill="x", pady=(8, 0))
                scoped_bars[r["key"]] = bar
                grew = True
            apply_bar(bar, r.get("pct"), r.get("reset"))
        if grew:
            # Make room for the new bars instead of clipping them.
            root.update_idletasks()
            need = root.winfo_reqheight()
            if need > root.winfo_height():
                root.geometry(f"{root.winfo_width()}x{need}")
        burn_lbl.configure(text=format_burn(data.get("burn") or {}))
        plan = data.get("plan")
        plan_lbl.configure(text=("· " + plan) if plan else "")
        health_lbl.configure(text=_health_text(data))

    def _btn(text, cmd, side="right", padx=0):
        tk.Button(
            footer, text=text, command=cmd,
            bg=c["BTN_BG"], fg=c["TEXT"], relief="flat",
            activebackground=c["BTN_BG_ACTIVE"], activeforeground=c["TEXT"],
            padx=14, pady=4, cursor="hand2",
        ).pack(side=side, padx=padx)

    _btn(t("common.close"), root.destroy)
    _btn(t("common.refresh"), _refresh, padx=(0, 8))
    if on_open_history:
        _btn(t("menu.open_history"), on_open_history, padx=(0, 8))

    def _refresh_if_open():
        if root.winfo_exists():
            _refresh()

    ui_host.after(50, _refresh_if_open)

    def _auto():
        if not root.winfo_exists():
            return
        try:
            _refresh()
        except tk.TclError:
            return
        ui_host.after(5_000, _auto)

    ui_host.after(5_000, _auto)

    def on_close():
        try:
            root.destroy()
        except tk.TclError:
            pass

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.bind("<Escape>", lambda _e: on_close())


def _health_text(data: dict) -> str:
    """One-line auth health: which source is active + token expiry."""
    import time as _time
    from api_client import format_reset

    bits = []
    src = data.get("token_source")
    if src:
        # Keep only the provider prefix (e.g. "claude-desktop:" → "claude-desktop").
        short = str(src).split(":", 1)[0]
        bits.append(f"{t('health.source')}: {short}")
    exp = data.get("token_expires_at")
    if exp is not None:
        left = exp - _time.time()
        note = t('health.expired') if left <= 0 else t('health.expires_in',
                                                        time=format_reset(int(left)))
        bits.append(f"{t('health.expires')} {note}")
    return "   ·   ".join(bits)


def _safe_set_topmost(root: tk.Tk, value: bool) -> None:
    try:
        root.attributes("-topmost", value)
    except tk.TclError:
        pass
