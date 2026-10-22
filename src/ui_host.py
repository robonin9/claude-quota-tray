"""
The app's single Tk root and the one thread allowed to touch Tk.

Every window is a ``tk.Toplevel`` of this hidden root. Other threads never call
Tk directly (not even ``.after()``); they hand work over with ``call()`` /
``call_sync()`` and the UI thread drains that queue on a timer.

On Windows the UI thread is a background thread started by ``start()``.
``run_forever()`` runs the same loop on the calling thread instead, for
platforms where Tk must own the main thread.
"""

from __future__ import annotations

import gc
import queue
import sys
import threading
import tkinter as tk
import traceback
from typing import Any, Callable, Optional

_DRAIN_MS = 100

_queue: "queue.Queue[tuple]" = queue.Queue()
_lock = threading.Lock()
_ready = threading.Event()
_root: Optional[tk.Tk] = None
_ui_thread: Optional[threading.Thread] = None
_started = False
_stopping = False


class _SyncResult:
    __slots__ = ("event", "value", "error")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.value: Any = None
        self.error: Optional[BaseException] = None


def _log_exception(where: str) -> None:
    try:
        sys.stderr.write(f"\n[ui_host] {where}\n")
        traceback.print_exc(file=sys.stderr)
    except Exception:
        pass


def start(timeout: float = 5.0) -> bool:
    """Start the UI thread (idempotent). Returns True once Tk is up."""
    global _started
    with _lock:
        if not _started:
            _started = True
            threading.Thread(target=_run, name="cqt-ui", daemon=True).start()
    _ready.wait(timeout=timeout)
    return is_alive()


def run_forever() -> None:
    """Run the Tk loop on the calling thread until ``shutdown()``."""
    global _started
    with _lock:
        if _started:
            raise RuntimeError("ui_host is already running")
        _started = True
    _run()


def _run() -> None:
    if not _bootstrap():
        return
    try:
        _root.mainloop()
    except Exception:
        _log_exception("mainloop")
    finally:
        _teardown()


def _bootstrap() -> bool:
    global _root, _ui_thread
    _ui_thread = threading.current_thread()
    try:
        root = tk.Tk()
        root.withdraw()
        root.report_callback_exception = _report_callback_exception
    except Exception:
        _log_exception("Tk init failed")
        _ready.set()
        return False
    _root = root
    root.after(_DRAIN_MS, _drain)
    _ready.set()
    return True


def _report_callback_exception(exc, val, tb) -> None:
    try:
        sys.stderr.write("\n[ui_host] Tk callback error\n")
        traceback.print_exception(exc, val, tb, file=sys.stderr)
    except Exception:
        pass


def _drain() -> None:
    while True:
        try:
            fn, args, kwargs, result = _queue.get_nowait()
        except queue.Empty:
            break
        try:
            value = fn(*args, **kwargs)
            if result is not None:
                result.value = value
        except BaseException as e:  # noqa: BLE001 — handed back to the caller
            if result is not None:
                result.error = e
            else:
                _log_exception(f"call {getattr(fn, '__name__', fn)!r}")
        finally:
            if result is not None:
                result.event.set()
    if not _stopping and _root is not None:
        try:
            _root.after(_DRAIN_MS, _drain)
        except tk.TclError:
            pass


def _teardown() -> None:
    global _root
    _root = None
    # Free Tk objects (StringVars in closures of closed windows, the
    # interpreter itself) here on the UI thread. If the last reference dies on
    # another thread, Tcl aborts the process with "Tcl_AsyncDelete: async
    # handler deleted by the wrong thread".
    tk._default_root = None
    gc.collect()
    # Release anyone still waiting on a call that will never run.
    while True:
        try:
            _fn, _a, _kw, result = _queue.get_nowait()
        except queue.Empty:
            break
        if result is not None:
            result.error = RuntimeError("ui_host stopped")
            result.event.set()


def is_alive() -> bool:
    return _root is not None and not _stopping


def is_ui_thread() -> bool:
    return _ui_thread is not None and threading.current_thread() is _ui_thread


def root() -> tk.Tk:
    """The hidden root window. Only valid on the UI thread."""
    if not is_ui_thread():
        raise RuntimeError("ui_host.root() called off the UI thread")
    if _root is None:
        raise RuntimeError("ui_host is not running")
    return _root


def after(ms: int, fn: Callable[[], Any]) -> Optional[str]:
    """Schedule ``fn`` on the root (UI thread only).

    Use this instead of ``toplevel.after()`` for timers that may outlive a
    window: callbacks registered on a destroyed Toplevel still fire on the
    shared interpreter and log "invalid command name". Callers must check
    ``winfo_exists()`` themselves.
    """
    r = root()
    try:
        return r.after(ms, fn)
    except tk.TclError:
        return None


def on_destroy(win: tk.Misc, fn: Callable[[], None]) -> None:
    """Run ``fn`` when ``win`` itself (not a child) is destroyed.

    Windows use this to drop module-level references to themselves however
    they close — a lingering reference keeps the Tcl interpreter alive past
    the UI thread, and freeing it from another thread aborts the process.
    """
    def _handler(event):
        if event.widget is win:
            fn()

    win.bind("<Destroy>", _handler, add="+")


def call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> bool:
    """Run ``fn`` on the UI thread later. Returns False if Tk is unavailable."""
    if not _started:
        start()
    if not is_alive():
        return False
    _queue.put((fn, args, kwargs, None))
    return True


def call_sync(fn: Callable[..., Any], *args: Any,
              timeout: float = 5.0, **kwargs: Any) -> Any:
    """Run ``fn`` on the UI thread and return its result (or re-raise).

    Called from the UI thread itself it runs ``fn`` immediately — queueing
    would deadlock, since this thread is the one that drains the queue.
    """
    if is_ui_thread():
        return fn(*args, **kwargs)
    if not _started:
        start()
    if not is_alive():
        raise RuntimeError("ui_host is not running")
    result = _SyncResult()
    _queue.put((fn, args, kwargs, result))
    if not result.event.wait(timeout=timeout):
        raise TimeoutError(f"ui_host.call_sync timed out after {timeout}s")
    if result.error is not None:
        raise result.error
    return result.value


def shutdown(timeout: float = 2.0) -> None:
    """Destroy every window and stop the UI loop."""
    if not is_alive():
        return

    def _stop() -> None:
        global _stopping
        _stopping = True
        try:
            _root.destroy()
        except tk.TclError:
            pass

    if is_ui_thread():
        _stop()
        return
    _queue.put((_stop, (), {}, None))
    t = _ui_thread
    if t is not None and t is not threading.main_thread():
        t.join(timeout=timeout)
