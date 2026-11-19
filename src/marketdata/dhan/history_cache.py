"""A durable cache for Dhan's daily historical candles.

``POST /charts/historical`` answers with **settled** daily bars for a closed window, so a
response keyed by (security id, segment, instrument, from, to, session date) cannot become
wrong later the same day - and yesterday's window is immutable history. Caching it means a
research run, the engine's pre-open load and a restarted session reuse the same valid data
instead of spending the account's shared 5-requests-per-second budget again (the real source of
the HTTP 429 storms this cache was added for).

Honesty rules:

* A cache hit is always reported with its ``fetched_at`` so callers can expose the age of the
  data they reused; nothing here presents cached candles as freshly retrieved.
* Only whole candle frames are cached, exactly as Dhan sent them (normalised); no derived or
  adjusted values live in this file.
* Entries are pruned past ``max_age_days`` so the file stays bounded; a pruned entry simply
  costs one more request next time.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from datetime import UTC, date, datetime
from pathlib import Path

from src.marketdata.dhan.client import CandleFrame

DEFAULT_MAX_AGE_DAYS = 60


class DailyCandleCache:
    """File-backed daily-candle cache shared by every process that opens the same path."""

    def __init__(self, path: Path, *, max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._max_age_days = max_age_days
        self._conn = sqlite3.connect(path, isolation_level=None, timeout=5.0,
                                     check_same_thread=False)  # fmt: skip
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS daily_candles ("
            " cache_key TEXT NOT NULL PRIMARY KEY,"
            " security_id TEXT NOT NULL,"
            " segment TEXT NOT NULL,"
            " instrument TEXT NOT NULL,"
            " from_date TEXT NOT NULL,"
            " to_date TEXT NOT NULL,"
            " session_date TEXT NOT NULL,"
            " fetched_at_utc TEXT NOT NULL,"
            " payload TEXT NOT NULL)"
        )
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def key(
        security_id: str,
        *,
        segment: str,
        instrument: str,
        frm: date,
        to: date,
        session_date: date,
    ) -> str:
        raw = f"dhan|{segment}|{instrument}|{security_id}|{frm}|{to}|{session_date}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def get(self, cache_key: str) -> tuple[CandleFrame, datetime] | None:
        """The cached frame and when it was fetched, or None. Never raises on a missing table."""
        with self._lock:
            row = self._conn.execute(
                "SELECT payload, fetched_at_utc FROM daily_candles WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        if row is None:
            return None
        try:
            body = json.loads(str(row[0]))
            fetched_at = datetime.fromisoformat(str(row[1]))
        except (ValueError, KeyError):
            return None  # an unreadable entry is a miss, not a fabricated frame
        return (
            CandleFrame(
                timestamps=tuple(body["timestamp"]),
                open=tuple(body["open"]),
                high=tuple(body["high"]),
                low=tuple(body["low"]),
                close=tuple(body["close"]),
                volume=tuple(body["volume"]),
            ),
            fetched_at,
        )

    def put(
        self,
        cache_key: str,
        frame: CandleFrame,
        *,
        security_id: str,
        segment: str,
        instrument: str,
        frm: date,
        to: date,
        session_date: date,
        fetched_at: datetime | None = None,
    ) -> None:
        stamp = (fetched_at or datetime.now(UTC)).isoformat(timespec="seconds")
        payload = json.dumps(
            {
                "timestamp": list(frame.timestamps),
                "open": list(frame.open),
                "high": list(frame.high),
                "low": list(frame.low),
                "close": list(frame.close),
                "volume": list(frame.volume),
            }
        )
        with self._lock:
            self._conn.execute(
                "INSERT INTO daily_candles (cache_key, security_id, segment, instrument,"
                " from_date, to_date, session_date, fetched_at_utc, payload)"
                " VALUES (?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(cache_key) DO NOTHING",
                (
                    cache_key,
                    security_id,
                    segment,
                    instrument,
                    frm.isoformat(),
                    to.isoformat(),
                    session_date.isoformat(),
                    stamp,
                    payload,
                ),  # fmt: skip
            )

    def prune(self, *, now: float | None = None) -> int:
        """Drop entries fetched more than ``max_age_days`` ago; returns how many were removed."""
        cutoff = datetime.fromtimestamp(
            (now if now is not None else time.time()) - self._max_age_days * 86400, UTC
        ).isoformat(timespec="seconds")
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM daily_candles WHERE fetched_at_utc < ?", (cutoff,)
            )
            return int(cursor.rowcount or 0)
