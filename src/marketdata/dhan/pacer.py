"""A cross-process minimum-interval gate for Dhan's documented per-second API caps.

Dhan's limits (5 data and 1 quote request per second) are **per account**, not per process or
per client instance: a live engine and a research CLI running at the same time share one budget,
and in-process limiters alone produced real HTTP 429s. This pacer coordinates every caller on
this machine through a tiny dedicated SQLite file (its own file, not the event-store database,
so pacing a request never contends with the engine's writes): each request claims a slot in an
``IMMEDIATE`` transaction that serializes writers across processes, and a caller that finds the
window full sleeps and retries - bounded by the caller's own retry policy, never by an unbounded
local queue.

The pacer is deliberately coarse: minimum interval between consecutive requests of one kind,
which for the documented caps (with the client's safety margin) stays strictly below the limit
even when several processes share it.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

BUSY_TIMEOUT_MS = 5000
_PRUNE_AFTER_S = 5.0


class PacerTimeoutError(RuntimeError):
    """The shared gate could not be acquired within the bound (another process holds it)."""


class SqlitePacer:
    """Minimum-interval pacing shared by every process that opens the same file."""

    def __init__(
        self,
        path: Path,
        *,
        now: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            path, isolation_level=None, timeout=BUSY_TIMEOUT_MS / 1000, check_same_thread=False
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS dhan_request_log ( kind TEXT NOT NULL, ts REAL NOT NULL)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_drl_kind_ts ON dhan_request_log (kind, ts)"
        )
        self._lock = threading.Lock()  # one connection, possibly several threads
        self._now = now
        self._sleep = sleep

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _acquire(self, kind: str, min_interval_s: float) -> float:
        """Try to claim a slot. Returns 0.0 on success, else the seconds to wait before retrying."""
        now = self._now()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    "DELETE FROM dhan_request_log WHERE ts < ?", (now - _PRUNE_AFTER_S,)
                )
                row = self._conn.execute(
                    "SELECT MAX(ts) FROM dhan_request_log WHERE kind = ?", (kind,)
                ).fetchone()
                last = float(row[0]) if row and row[0] is not None else None
                if last is not None and now - last < min_interval_s:
                    delay = min_interval_s - (now - last)
                    self._conn.execute("COMMIT")
                    return delay + 0.005  # a hair of slack so the retry is not itself too early
                self._conn.execute(
                    "INSERT INTO dhan_request_log (kind, ts) VALUES (?, ?)", (kind, now)
                )
                self._conn.execute("COMMIT")
            except BaseException:
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                raise
        return 0.0

    async def wait(self, kind: str, min_interval_s: float, *, max_wait_s: float = 30.0) -> None:
        """Wait until this process may issue one request of ``kind``, then claim the slot.

        Bounded: if other callers keep taking the slots for ``max_wait_s``, raise instead of
        queueing forever (the caller reports the shed load rather than stalling a session).
        """
        deadline = time.monotonic() + max_wait_s
        while True:
            delay = await asyncio.to_thread(self._acquire, kind, min_interval_s)
            if delay <= 0.0:
                return
            if time.monotonic() + delay > deadline:
                raise PacerTimeoutError(
                    f"the shared Dhan '{kind}' gate stayed busy for {max_wait_s:.0f}s "
                    f"(another process is using the account's budget)"
                )
            await self._sleep(delay)
