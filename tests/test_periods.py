"""Period arithmetic: the end date, the open period, the readings a period spans, consumption
across a meter reset, forecasts, tariffs, charges and the reclose window."""

from datetime import date
from types import SimpleNamespace

import pytest

from common.flows import period_figures
from common.periods import (
    apartment_meters,
    charge,
    consumption,
    figures,
    forecast,
    is_closing_candidate,
    open_period,
    period_end,
    period_readings,
    tariff_on,
    within_reclose_window,
)

PERIOD = {"start": "2026-11-01", "months": 2, "closed_on": None, "last_reminded_on": None}


def reading(day, value, seq="1", opens=None, closes=None, resets=False):
    return {
        "date": day,
        "seq": seq,
        "value": value,
        "image_key": "k",
        "recorded_by": "a@b.c",
        "opens_period": opens,
        "closes_period": closes,
        "resets": resets,
    }


OPENING = reading("2026-10-29", 392691, opens="2026-11-01")
MIDDLE = reading("2026-12-05", 392900)
CLOSING = reading("2027-01-02", 393050, closes="2026-11-01")


def test_period_end():
    assert period_end(PERIOD) == date(2026, 12, 31)


def test_open_period_returns_the_unclosed_one():
    closed = {**PERIOD, "start": "2026-09-01", "closed_on": "2026-10-29"}
    assert open_period([closed, PERIOD]) == PERIOD


def test_open_period_none_when_all_closed():
    assert open_period([{**PERIOD, "closed_on": "2027-01-02"}]) is None


def test_open_period_rejects_two_open():
    with pytest.raises(ValueError):
        open_period([PERIOD, {**PERIOD, "start": "2027-01-01"}])


def test_period_readings_open_period_runs_to_last_reading():
    before = reading("2026-09-01", 100)
    assert period_readings([before, OPENING, MIDDLE], PERIOD) == [OPENING, MIDDLE]


def test_period_readings_stops_at_closing_reading():
    after = reading("2027-02-01", 400000, opens="2027-01-01")
    assert period_readings([OPENING, MIDDLE, CLOSING, after], PERIOD) == [OPENING, MIDDLE, CLOSING]


def test_consumption_none_without_opening_reading():
    assert consumption([MIDDLE], PERIOD) is None


def test_consumption_open_period_and_forecast():
    c = consumption([OPENING, MIDDLE], PERIOD)
    assert (c.opening, c.last, c.units, c.final) == (OPENING, MIDDLE, 209, False)
    assert forecast(c, PERIOD) == pytest.approx(209 / 37 * 61)


def test_consumption_closed_period_is_final():
    c = consumption([OPENING, MIDDLE, CLOSING], PERIOD)
    assert (c.units, c.final) == (359, True)


def test_forecast_none_with_only_the_opening_reading():
    assert forecast(consumption([OPENING], PERIOD), PERIOD) is None


def test_forecast_none_for_second_reading_on_the_opening_day():
    same_day = reading("2026-10-29", 392700, seq="2")
    c = consumption([OPENING, same_day], PERIOD)
    assert c.last is same_day
    assert forecast(c, PERIOD) is None


def test_period_readings_rejects_a_closing_reading_without_an_opening_one():
    with pytest.raises(ValueError):
        period_readings([MIDDLE, CLOSING], PERIOD)


def test_period_readings_rejects_closing_before_opening():
    with pytest.raises(ValueError):
        period_readings([CLOSING, OPENING], PERIOD)


def test_consumption_sums_stretches_across_a_reset():
    readings = [
        reading("2026-10-29", 100, "1", opens="2026-11-01"),
        reading("2026-11-10", 250, "1"),
        reading("2026-11-20", 10, "1", resets=True),
        reading("2026-12-01", 40, "1"),
    ]
    assert consumption(readings, PERIOD).units == 180


def test_tariff_on_picks_latest_effective():
    tariffs = [
        {"effective_date": "2026-01-01", "electricity_per_kwh": 0.5, "water_per_m3": 8.0},
        {"effective_date": "2026-07-01", "electricity_per_kwh": 0.6, "water_per_m3": 9.0},
    ]
    assert tariff_on(tariffs, date(2026, 12, 1)) == tariffs[1]
    assert tariff_on(tariffs, date(2026, 7, 1)) == tariffs[1]
    assert tariff_on(tariffs, date(2026, 6, 30)) == tariffs[0]
    assert tariff_on(tariffs, date(2025, 12, 31)) is None


def test_charge_rounds_to_two_decimals():
    assert charge(359, 0.6123) == 219.82


def test_is_closing_candidate():
    assert not is_closing_candidate(PERIOD, date(2026, 12, 30), 0)
    assert is_closing_candidate(PERIOD, date(2026, 12, 31), 0)
    assert not is_closing_candidate(PERIOD, date(2026, 12, 23), 7)
    assert is_closing_candidate(PERIOD, date(2026, 12, 24), 7)


def test_within_reclose_window():
    closing = date(2027, 1, 2)
    assert within_reclose_window(closing, date(2027, 1, 9), 7)
    assert not within_reclose_window(closing, date(2027, 1, 10), 7)


def test_apartment_meters_excludes_retired_and_other_apartments():
    live = {"apartment_id": "a1", "retired_at": None}
    meters = [
        live,
        {"apartment_id": "a1", "retired_at": "2026-05-01"},
        {"apartment_id": "a2", "retired_at": None},
    ]
    assert apartment_meters(meters, "a1") == [live]


def test_period_figures_match_pure_figures(store):
    tariffs = [{"effective_date": "2026-01-01", "electricity_per_kwh": 0.6, "water_per_m3": 9.5}]
    for tariff in tariffs:
        store.put_tariff("acc1", tariff)
    engine = SimpleNamespace(store=store, clock=lambda: date(2026, 12, 5))
    user = SimpleNamespace(account="acc1")
    readings = [OPENING, MIDDLE]
    assert figures("water", readings, PERIOD, tariffs, date(2026, 12, 5)) is not None
    for items in (readings, [OPENING, MIDDLE, CLOSING], []):
        assert period_figures(engine, user, "water", items, PERIOD) == figures(
            "water", items, PERIOD, tariffs, date(2026, 12, 5)
        )
