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


# Minimum growth (pct/hour) before we trust an ETA. Below this the value is
# essentially flat and any projected "time to full" would be meaningless.
_MIN_RATE_FOR_ETA = 0.5
# Never project an ETA further out than this — beyond it the number is noise,
# and the quota window will have reset long before then anyway.
_MAX_ETA_SECONDS = 14 * 86400


def burn_rate(window_minutes: float = 60, account_id: Optional[str] = None) -> dict:
    """
    Compute usage growth rate (% per hour) over the recent window for both
    session and weekly limits. Returns {session: {rate, eta_seconds}, weekly: ...}.

    Rate is None unless we have at least two points that are actually growing.
    A quota reset (the percentage dropping) is detected and only the samples
    *after* the most recent reset are used, so the rate isn't dragged negative
    or the ETA hallucinated when a window rolls over mid-sample.
    """
    rows = recent(window_minutes / 60.0, account_id)
    if len(rows) < 2:
        return {"session": _empty_rate(), "weekly": _empty_rate()}

    def _rate_for(idx_pct: int) -> dict:
        # Non-null (ts, pct) points in chronological order.
        points = [(r[0], r[idx_pct]) for r in rows if r[idx_pct] is not None]
        if len(points) < 2:
            return _empty_rate()

        # Drop everything up to and including the last reset (a drop in pct):
        # only the current monotonic-ish run reflects the active window.
        start = 0
        for i in range(1, len(points)):
            if points[i][1] < points[i - 1][1]:
                start = i
        segment = points[start:]
        if len(segment) < 2:
            return _empty_rate()

        first_ts, first_pct = segment[0]
        last_ts, current_pct = segment[-1]
        dt_hours = (last_ts - first_ts) / 3600.0
        if dt_hours <= 0:
            return _empty_rate()

        rate = (current_pct - first_pct) / dt_hours  # pct/hour
        if rate <= 0:
            # Flat or recovering — report no growth, no ETA.
            return {"rate": max(0.0, rate), "eta_seconds": None}

        eta = None
        if rate >= _MIN_RATE_FOR_ETA and current_pct < 100:
            remaining = 100 - current_pct
            projected = int((remaining / rate) * 3600)
            if projected <= _MAX_ETA_SECONDS:
                eta = projected
        return {"rate": rate, "eta_seconds": eta}

    return {
        "session": _rate_for(1),
        "weekly": _rate_for(2),
    }


def _empty_rate() -> dict:
    return {"rate": None, "eta_seconds": None}


def export_csv(path: str | Path, hours: float = 24,
               account_id: Optional[str] = None) -> int:
    """Write recent snapshots to CSV. Returns row count."""
    rows = recent(hours, account_id)
    p = Path(path)
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["timestamp_iso", "unix_ts", "session_pct", "weekly_pct"])
        for ts, session_pct, weekly_pct in rows:
            from datetime import datetime
            iso = datetime.fromtimestamp(ts).isoformat(sep=" ", timespec="seconds")
            w.writerow([iso, ts, session_pct, weekly_pct])
    return len(rows)


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
