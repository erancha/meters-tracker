"""Billing-period arithmetic over stored readings; pure functions on the store's plain dicts.

Invariants:
- A period's opening reading is the one flagged opens_period == period start; its closing
  reading is the one flagged closes_period == period start. Readings arrive sorted by date, seq.
- A reading flagged resets (meter replaced or reset) ends one accumulation stretch at the
  reading before it and starts a new stretch at itself; units are the sum over stretches.
- At most one period is open (closed_on is None) at a time.

Core pieces:
- new_period: a period's row as it opens
- period_readings / flags_in_order / joining_opens: a meter's readings in a period, whether its
  flags there make sense, and what a joining reading opens
- consumption / charge: units in a period and their price
- figures: one meter's Consumption, price and amount for a period, from the tariffs and today's
  date it is handed
"""

from dataclasses import dataclass
from datetime import date, timedelta
from itertools import pairwise

from common.dates import add_months, parse_iso

PRICES = {"electricity": "electricity_per_kwh", "water": "water_per_m3"}


@dataclass(frozen=True)
class Consumption:
    opening: dict
    last: dict
    units: int
    final: bool


def new_period(start, months) -> dict:
    return {"start": start, "months": months, "closed_on": None, "last_reminded_on": None}


def period_end(period: dict) -> date:
    return add_months(parse_iso(period["start"]), period["months"]) - timedelta(days=1)


def open_period(periods: list) -> dict | None:
    open_ones = [p for p in periods if p["closed_on"] is None]
    if len(open_ones) > 1:
        raise ValueError(f"expected at most one open period, found {len(open_ones)}")
    return open_ones[0] if open_ones else None


def _flag_positions(readings: list, start: str) -> tuple[int | None, int | None]:
    """The indexes of the readings flagged as opening and closing the period starting on start."""
    first = next((i for i, r in enumerate(readings) if r["opens_period"] == start), None)
    last = next((i for i, r in enumerate(readings) if r["closes_period"] == start), None)
    return first, last


def flags_in_order(readings: list, period: dict) -> bool:
    """False when period_readings would refuse the meter's flags in the period: a closing reading
    with no opening reading, or sorted before it."""
    first, last = _flag_positions(readings, period["start"])
    return last is None or (first is not None and first <= last)


def period_readings(readings: list, period: dict) -> list:
    start = period["start"]
    first, last = _flag_positions(readings, start)
    if first is None:
        if last is not None:
            raise ValueError(f"period {start} has a closing reading but no opening reading")
        return []
    if last is None:
        return readings[first:]
    if last < first:
        raise ValueError(f"period {start} closes before it opens")
    return readings[first : last + 1]


def joining_opens(current: dict | None, readings: list) -> str | None:
    """What a reading joining a meter's readings opens: the open period, when there is one and the
    meter has no opening reading there."""
    if current is None or any(r["opens_period"] == current["start"] for r in readings):
        return None
    return current["start"]


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


def price_in(tariffs: list, meter_type: str, d: date) -> float | None:
    """The price of meter_type in the tariff in force on d; None when no tariff is in force or
    that tariff leaves the type unpriced."""
    tariff = tariff_on(tariffs, d)
    return None if tariff is None else tariff[PRICES[meter_type]]


@dataclass(frozen=True)
class Figures:
    """One meter's figures for a period: its consumption, final or so far, and the charge on it."""

    consumption: Consumption
    price: float | None
    amount: float | None


def figures(
    meter_type: str, readings: list, period: dict, tariffs: list, today: date
) -> Figures | None:
    """The meter's figures for the period, or None when it has no reading there. A final count is
    priced on its closing reading's date, a count so far on today."""
    c = consumption(readings, period)
    if c is None:
        return None
    price = price_in(tariffs, meter_type, parse_iso(c.last["date"]) if c.final else today)
    amount = None if price is None else charge(c.units, price)
    return Figures(c, price, amount)
