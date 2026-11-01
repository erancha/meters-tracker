"""Billing-period arithmetic over stored readings; pure functions on the store's plain dicts.

Invariants:
- A period's opening reading is the one flagged opens_period == period start; its closing
  reading is the one flagged closes_period == period start. Readings arrive sorted by date, seq.
- A reading flagged resets (meter replaced or reset) ends one accumulation stretch at the
  reading before it and starts a new stretch at itself; units are the sum over stretches.
- At most one period is open (closed_on is None) at a time.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from itertools import pairwise

from common.dates import add_months, parse_iso


@dataclass(frozen=True)
class Consumption:
    opening: dict
    last: dict
    units: int
    final: bool


def period_end(period: dict) -> date:
    return add_months(parse_iso(period["start"]), period["months"]) - timedelta(days=1)


def open_period(periods: list) -> dict | None:
    open_ones = [p for p in periods if p["closed_on"] is None]
    if len(open_ones) > 1:
        raise ValueError(f"expected at most one open period, found {len(open_ones)}")
    return open_ones[0] if open_ones else None


def period_readings(readings: list, period: dict) -> list:
    start = period["start"]
    first = next((i for i, r in enumerate(readings) if r["opens_period"] == start), None)
    last = next((i for i, r in enumerate(readings) if r["closes_period"] == start), None)
    if first is None:
        if last is not None:
            raise ValueError(f"period {start} has a closing reading but no opening reading")
        return []
    if last is None:
        return readings[first:]
    if last < first:
        raise ValueError(f"period {start} closes before it opens")
    return readings[first : last + 1]


def consumption(readings: list, period: dict) -> Consumption | None:
    in_period = period_readings(readings, period)
    if not in_period:
        return None
    units = 0
    stretch_start = in_period[0]
    for previous, current in pairwise(in_period):
        if current["resets"]:
            units += previous["value"] - stretch_start["value"]
            stretch_start = current
    units += in_period[-1]["value"] - stretch_start["value"]
    return Consumption(
        in_period[0], in_period[-1], units, in_period[-1]["closes_period"] == period["start"]
    )


def forecast(c: Consumption, period: dict) -> float | None:
    elapsed_days = (parse_iso(c.last["date"]) - parse_iso(c.opening["date"])).days
    if elapsed_days == 0:
        return None
    period_days = (period_end(period) - parse_iso(period["start"])).days + 1
    return c.units / elapsed_days * period_days


def tariff_on(tariffs: list, d: date) -> dict | None:
    effective = [t for t in tariffs if parse_iso(t["effective_date"]) <= d]
    return effective[-1] if effective else None


def charge(units: float, price: float) -> float:
    return round(units * price, 2)


def is_closing_candidate(period: dict, reading_date: date, days_before_end: int) -> bool:
    """True for a reading that may close the period: one dated on or after its last day, or
    within days_before_end days before it."""
    return reading_date >= period_end(period) - timedelta(days=days_before_end)


def within_reclose_window(closing_date: date, reading_date: date, days: int) -> bool:
    return (reading_date - closing_date).days <= days


def apartment_meters(meters: list, apartment_id: str) -> list:
    return [m for m in meters if m["apartment_id"] == apartment_id and m["retired_at"] is None]
