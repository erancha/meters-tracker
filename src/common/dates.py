"""Calendar helpers for the app's Jerusalem-local dates.

Every date the app shows or stores is a calendar date in Asia/Jerusalem, so "today" and
timestamps are taken in that zone rather than in the Lambda's UTC clock.
"""

import calendar
from datetime import date, datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Jerusalem")


def now() -> datetime:
    return datetime.now(TZ)


def today() -> date:
    return now().date()


def add_months(d: date, n: int) -> date:
    month_index = d.year * 12 + (d.month - 1) + n
    year, month = divmod(month_index, 12)
    month += 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def fmt(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def parse_iso(s: str) -> date:
    return date.fromisoformat(s)
