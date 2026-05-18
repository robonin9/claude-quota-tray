"""
Full history window: 2 progress bars on top + 24-hour usage chart.
Uses shared widgets from bar_widget.py.
"""

from __future__ import annotations

import threading
import time
import tkinter as tk
from datetime import datetime
from typing import Callable, Optional

import history
from bar_widget import (
    BG, BTN_BG, BTN_BG_ACTIVE, MUTED, TEXT,
    apply_bar, build_bar, format_burn, ui_font,
)
from i18n import t


_window_lock = threading.Lock()
_open_window: Optional[tk.Tk] = None

SnapshotFetcher = Callable[[], dict]


def show(account_id: str, account_name: str,
         get_data: Optional[SnapshotFetcher] = None,
         burn: Optional[dict] = None) -> None:
    with _window_lock:
        global _open_window
        if _open_window is not None:
            try:
                _open_window.lift()
                _open_window.focus_force()
                return
            except tk.TclError:
                _open_window = None

    if get_data is None:
        b = burn or {"session": {}, "weekly": {}}
        get_data = lambda: {
            "session_pct": None, "weekly_pct": None,
            "session_reset": None, "weekly_reset": None,
            "burn": b,
        }

    t = threading.Thread(
        target=_run_window,
        args=(account_id, account_name, get_data),
        daemon=True,
    )
    t.start()


def _run_window(account_id: str, account_name: str,
                get_data: SnapshotFetcher) -> None:
    global _open_window

    root = tk.Tk()
    root.title(t('window.history_title', name=account_name))
    root.geometry("760x560")
    root.minsize(560, 420)
    root.configure(bg=BG)

    with _window_lock:
        _open_window = root

    header = tk.Frame(root, bg=BG)
    header.pack(fill="x", padx=18, pady=(16, 0))
    title_box = tk.Frame(header, bg=BG)
    title_box.pack(side="left")
    tk.Label(
        title_box, text=t('window.current_usage'),
        font=ui_font(13, "bold"),
        fg=TEXT, bg=BG,
    ).pack(side="left")
    plan_lbl = tk.Label(
        title_box, text="", font=ui_font(10, "bold"),
        fg="#a3b8ff", bg=BG,
    )
    plan_lbl.pack(side="left", padx=(10, 0))
    burn_lbl = tk.Label(
        header, text="", font=ui_font(10),
        fg=MUTED, bg=BG, justify="right",
    )
    burn_lbl.pack(side="right")

    bars_panel = tk.Frame(root, bg=BG)
    bars_panel.pack(fill="x", padx=18, pady=(10, 6))

    session_bar = build_bar(bars_panel, t('bar.session_label'))
    session_bar["frame"].pack(fill="x", pady=(0, 10))
    weekly_bar = build_bar(bars_panel, t('bar.weekly_label'))
    weekly_bar["frame"].pack(fill="x")

    tk.Label(
        root, text=t('window.last_24h'),
        font=ui_font(11, "bold"),
        fg=TEXT, bg=BG,
    ).pack(anchor="w", padx=18, pady=(14, 2))

    canvas = tk.Canvas(root, bg=BG, highlightthickness=0)
    canvas.pack(fill="both", expand=True, padx=18, pady=(2, 8))

    footer = tk.Frame(root, bg=BG)
    footer.pack(fill="x", padx=18, pady=(0, 14))
    _legend_swatch(footer, "#4ade80", t('bar.session_short'))
    _legend_swatch(footer, "#60a5fa", t('bar.weekly_short'))

    def _refresh_all():
        data = get_data()
        apply_bar(session_bar, data.get("session_pct"), data.get("session_reset"))
        apply_bar(weekly_bar, data.get("weekly_pct"), data.get("weekly_reset"))
        burn_lbl.configure(text=format_burn(data.get("burn") or {}))
        plan = data.get("plan")
        plan_lbl.configure(text=("· " + plan) if plan else "")
        _redraw_chart(canvas, account_id)

    refresh_btn = tk.Button(
        footer, text=t('common.refresh'),
        command=_refresh_all,
        bg=BTN_BG, fg=TEXT, relief="flat",
        activebackground=BTN_BG_ACTIVE, activeforeground=TEXT,
        padx=14, pady=4, cursor="hand2",
    )
    refresh_btn.pack(side="right")

    canvas.bind("<Configure>", lambda _e: _redraw_chart(canvas, account_id))
    root.after(50, _refresh_all)

    def _auto():
        try:
            _refresh_all()
        except tk.TclError:
            return
        root.after(30_000, _auto)

    root.after(30_000, _auto)

    def on_close():
        global _open_window
        with _window_lock:
            _open_window = None
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


def _legend_swatch(parent: tk.Frame, color: str, label: str) -> None:
    box = tk.Frame(parent, bg=BG)
    box.pack(side="left", padx=(0, 18))
    tk.Frame(box, width=14, height=14, bg=color).pack(side="left", padx=(0, 6))
    tk.Label(box, text=label, fg=MUTED, bg=BG,
             font=ui_font(9)).pack(side="left")


def _redraw_chart(canvas: tk.Canvas, account_id: str) -> None:
    canvas.delete("all")
    w = canvas.winfo_width()
    h = canvas.winfo_height()
    if w < 50 or h < 50:
        return

    rows = history.recent(24, account_id)
    pad_l, pad_r, pad_t, pad_b = 44, 12, 8, 24
    plot_w = w - pad_l - pad_r
    plot_h = h - pad_t - pad_b

    for pct in (0, 25, 50, 75, 100):
        y = pad_t + plot_h - (pct / 100.0) * plot_h
        canvas.create_line(pad_l, y, pad_l + plot_w, y,
                           fill="#2d2d2d", width=1)
        canvas.create_text(pad_l - 6, y, text=f"{pct}%",
                           anchor="e", fill=MUTED,
                           font=ui_font(8))

    now = time.time()
    start = now - 24 * 3600
    for hours_ago in (24, 18, 12, 6, 0):
        ts = now - hours_ago * 3600
        x = pad_l + ((ts - start) / max(now - start, 1)) * plot_w
        label = datetime.fromtimestamp(ts).strftime("%H:%M")
        canvas.create_text(x, pad_t + plot_h + 12, text=label,
                           fill=MUTED, font=ui_font(8))

    if not rows:
        canvas.create_text(
            pad_l + plot_w / 2, pad_t + plot_h / 2,
            text=t('window.no_history'),
            fill=MUTED, font=ui_font(10),
        )
        return

    def to_xy(ts: float, pct: int):
        x = pad_l + ((ts - start) / max(now - start, 1)) * plot_w
        y = pad_t + plot_h - (pct / 100.0) * plot_h
        return x, y

    _plot_series(canvas, rows, 1, "#4ade80", to_xy)
    _plot_series(canvas, rows, 2, "#60a5fa", to_xy)


def _plot_series(canvas: tk.Canvas, rows: list, idx: int, color: str, to_xy) -> None:
    pts = [to_xy(r[0], r[idx]) for r in rows if r[idx] is not None]
    if len(pts) < 2:
        for x, y in pts:
            canvas.create_oval(x - 3, y - 3, x + 3, y + 3, fill=color, outline="")
        return
    flat: list[float] = []
    for x, y in pts:
        flat.extend([x, y])
    canvas.create_line(*flat, fill=color, width=2, smooth=True)
    last_x, last_y = pts[-1]
    canvas.create_oval(last_x - 4, last_y - 4, last_x + 4, last_y + 4,
                       fill=color, outline="")
