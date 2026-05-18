"""
Snapshot history persisted to a small SQLite database.

Used for:
  - the 'Show history' chart window
  - burn-rate / ETA calculation
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

from settings import SETTINGS_DIR


DB_PATH = SETTINGS_DIR / "history.db"

_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS snapshots (
                ts REAL NOT NULL,
                account_id TEXT NOT NULL,
                session_pct INTEGER,
                weekly_pct INTEGER,
                session_reset INTEGER,
                weekly_reset INTEGER
            )
            """
        )
        _conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON snapshots(ts)"
        )
        _conn.commit()
    return _conn


def record(account_id: str, snapshot) -> None:
    """Persist a UsageSnapshot. Errors are swallowed (history is best-effort)."""
    if not getattr(snapshot, "ok", False):
        return
    if snapshot.session_pct is None and snapshot.weekly_pct is None:
        return
    try:
        with _lock:
            conn = _connect()
            conn.execute(
                "INSERT INTO snapshots VALUES (?, ?, ?, ?, ?, ?)",
                (
                    snapshot.fetched_at or time.time(),
                    account_id,
                    snapshot.session_pct,
                    snapshot.weekly_pct,
                    snapshot.session_reset_seconds,
                    snapshot.weekly_reset_seconds,
                ),
            )
            conn.commit()
    except sqlite3.Error:
        pass


def recent(hours: float = 24, account_id: Optional[str] = None) -> list[tuple]:
    """Return rows from the last N hours, ordered by timestamp."""
    cutoff = time.time() - hours * 3600
    try:
        with _lock:
            conn = _connect()
            if account_id:
                cur = conn.execute(
                    "SELECT ts, session_pct, weekly_pct FROM snapshots "
                    "WHERE ts >= ? AND account_id = ? ORDER BY ts",
                    (cutoff, account_id),
                )
            else:
                cur = conn.execute(
                    "SELECT ts, session_pct, weekly_pct FROM snapshots "
                    "WHERE ts >= ? ORDER BY ts",
                    (cutoff,),
                )
            return cur.fetchall()
    except sqlite3.Error:
        return []


def burn_rate(window_minutes: float = 60, account_id: Optional[str] = None) -> dict:
    """
    Compute usage growth rate (% per hour) over the recent window for both
    session and weekly limits. Returns {session: {rate, eta_seconds}, weekly: ...}.

    Rate is None if we lack at least two points or the value isn't growing.
    """
    rows = recent(window_minutes / 60.0, account_id)
    if len(rows) < 2:
        return {"session": _empty_rate(), "weekly": _empty_rate()}

    def _rate_for(idx_pct: int, current_pct: Optional[int]) -> dict:
        # Find first non-null pct and last non-null pct in the window.
        first = next(((r[0], r[idx_pct]) for r in rows if r[idx_pct] is not None), None)
        last = next(
            ((r[0], r[idx_pct]) for r in reversed(rows) if r[idx_pct] is not None),
            None,
        )
        if not first or not last or first[0] == last[0]:
            return _empty_rate()
        dt_hours = (last[0] - first[0]) / 3600.0
        if dt_hours <= 0:
            return _empty_rate()
        delta = last[1] - first[1]
        rate = delta / dt_hours  # pct/hour
        eta = None
        if rate > 0.01 and current_pct is not None and current_pct < 100:
            remaining = 100 - current_pct
            eta = int((remaining / rate) * 3600)
        return {"rate": rate, "eta_seconds": eta}

    # current_pct from latest non-null
    last_session = next((r[1] for r in reversed(rows) if r[1] is not None), None)
    last_weekly = next((r[2] for r in reversed(rows) if r[2] is not None), None)
    return {
        "session": _rate_for(1, last_session),
        "weekly": _rate_for(2, last_weekly),
    }


def _empty_rate() -> dict:
    return {"rate": None, "eta_seconds": None}


def prune(retention_days: int) -> None:
    """Delete rows older than retention_days."""
    if retention_days <= 0:
        return
    cutoff = time.time() - retention_days * 86400
    try:
        with _lock:
            conn = _connect()
            conn.execute("DELETE FROM snapshots WHERE ts < ?", (cutoff,))
            conn.commit()
    except sqlite3.Error:
        pass
