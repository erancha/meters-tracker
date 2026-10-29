"""Date arithmetic and formatting in the app's time zone."""

from datetime import date, datetime

from common.dates import TZ, add_months, fmt, today


def test_add_months_clamps_day():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)


def test_add_months_two():
    assert add_months(date(2026, 11, 1), 2) == date(2027, 1, 1)


def test_fmt():
    assert fmt(date(2026, 10, 29)) == "29/10/2026"


def test_today_is_jerusalem():
    assert today() == datetime.now(TZ).date()
