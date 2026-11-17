"""Calendar-aware boundaries for close-to-close research measurements."""

from collections.abc import Sequence
from datetime import date, datetime

from src.domain.calendar import CalendarCoverageError, get_calendar


def first_close_after(dates: Sequence[date], known_at: datetime) -> int | None:
    """First observable closing-price anchor after information became available.

    This is a reaction study, not an executable entry-price model. An after-close
    announcement cannot use that day's earlier close. Fail closed outside calendar coverage.
    """
    if known_at.tzinfo is None:
        raise ValueError("known_at must be timezone-aware")
    calendar = get_calendar()
    for index, day in enumerate(dates):
        try:
            session = calendar.session(day)
        except CalendarCoverageError:
            continue
        if session is not None and session.close > known_at:
            return index
    return None
