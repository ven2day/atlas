"""
Turning Dhan's timestamps into honest IST instants.

Dhan documents the chart ``timestamp`` field only as "Epoch timestamp" (seconds), and
``last_trade_time`` as ``DD/MM/YYYY HH:MM:SS`` without naming a zone. Both are resolved here,
explicitly, and anything that does not fit is rejected rather than guessed:

* **Daily candles** are stamped at a session boundary, so converting the epoch as a true UTC
  instant into IST yields the session's calendar date under either plausible convention
  (midnight IST and midnight UTC both land on the same IST date).
* **Intraday candles** are disambiguated against the NSE equity session window: of the two
  readings (true UTC instant, or an IST wall clock stored as if it were UTC) at most one puts
  every bar of a request inside 09:00-16:00 IST, and that reading wins. If neither does, the
  bars are refused - a wrong clock would silently mislabel every candle.
* **``last_trade_time``** is read as IST (Dhan is an NSE broker; the zone is not documented) and
  only accepted when it is a plausible wall-clock moment: year >= 2000 and not in the future.
  Dhan's own examples use ``01/01/1980 00:00:00`` for an instrument with no trades, which this
  rejects, so a quote is never stamped with a time it did not happen at.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from src.utils.market_time import IST

# A generous window around the NSE equity session (09:15-15:30 IST): pre-open ticks and the
# closing auction sit just outside the normal hours, so the test is about plausibility, not
# about deciding whether the market is open (the calendar owns that).
SESSION_EARLIEST = time(9, 0)
SESSION_LATEST = time(16, 0)

TRADE_TIME_FORMAT = "%d/%m/%Y %H:%M:%S"
PLAUSIBLE_TRADE_YEAR = 2000
FUTURE_TOLERANCE = timedelta(minutes=2)


class DhanTimestampError(ValueError):
    """A Dhan timestamp that cannot be placed inside an NSE session under any reading."""


def _in_session(moment: datetime) -> bool:
    wall = moment.time()
    return SESSION_EARLIEST <= wall <= SESSION_LATEST


def chart_instant(epoch_seconds: int, *, intraday: bool) -> datetime:
    """An aware IST instant for a chart ``timestamp`` (epoch seconds)."""
    as_utc = datetime.fromtimestamp(epoch_seconds, tz=UTC)
    if not intraday:
        return as_utc.astimezone(IST)
    as_instant = as_utc.astimezone(IST)  # reading A: a true UTC instant
    as_wall = as_utc.replace(tzinfo=IST)  # reading B: an IST wall clock stored as UTC
    a_ok, b_ok = _in_session(as_instant), _in_session(as_wall)
    if a_ok and not b_ok:
        return as_instant
    if b_ok and not a_ok:
        return as_wall
    if a_ok and b_ok:
        return as_instant  # both plausible: the documented reading (a true instant) wins
    raise DhanTimestampError(
        f"epoch {epoch_seconds} is outside the NSE session window under either reading"
    )


def chart_session_date(epoch_seconds: int) -> date:
    """The IST calendar date of a daily candle's timestamp."""
    return chart_instant(epoch_seconds, intraday=False).date()


def parse_last_trade_time(text: str | None, *, now: datetime) -> datetime | None:
    """``last_trade_time`` as an aware IST instant, or None when it is not a real trade time."""
    if not text:
        return None
    try:
        naive = datetime.strptime(text.strip(), TRADE_TIME_FORMAT)
    except ValueError:
        return None
    if naive.year < PLAUSIBLE_TRADE_YEAR:
        return None  # Dhan's placeholder for "never traded"
    moment = naive.replace(tzinfo=IST)
    if moment > now + FUTURE_TOLERANCE:
        return None
    return moment
