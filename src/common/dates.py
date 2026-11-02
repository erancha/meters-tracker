"""Calendar helpers for the app's Jerusalem-local dates.

Every date the app shows or stores is a calendar date in Asia/Jerusalem, so "today" and
timestamps are taken in that zone rather than in the Lambda's UTC clock.
"""

import calendar
from datetime import date, datetime, timedelta
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


def next_day(d: str) -> str:
    return (parse_iso(d) + timedelta(days=1)).isoformat()


def fmt(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def fmt_day_month(d: date) -> str:
    return d.strftime("%d/%m")


def first_of_next_month(d: date) -> date:
    return add_months(d.replace(day=1), 1)


def parse_iso(s: str) -> date:
    return date.fromisoformat(s)
