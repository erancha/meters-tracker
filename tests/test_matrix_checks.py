"""The correction matrix's billing check, on hand-made states: a stretch between two consecutive
readings of a meter is reported when no period bills it, unless a vacate lies between them, and
when more than one period does."""

from test_correction_matrix import STATES, unbilled_stretches

from common.dates import next_day


def _vacant():
    """May-June closed on 2026-06-30 and vacated, W and E read on 05-01, 05-20 and 06-30."""
    return STATES["vacant"].build(0)


def _row(state, serial, day):
    return next(r for r in state.readings[serial] if r["date"] == day)


def test_a_vacated_state_bills_every_stretch():
    assert unbilled_stretches(_vacant()) == []


def test_a_stretch_lost_before_a_vacate_is_reported():
    state = _vacant()
    _row(state, "W", "2026-06-30")["closes_period"] = None
    _row(state, "W", "2026-05-20")["closes_period"] = "2026-05-01"
    assert unbilled_stretches(state) == ["W 2026-05-20->2026-06-30 billed 0x"]


def test_a_stretch_across_the_vacate_bills_to_nobody():
    state = _vacant()
    later = {**_row(state, "W", "2026-06-30"), "date": "2026-09-05", "seq": "2026-09-05T08:00:00"}
    later.update(value=99, closes_period=None, opens_period="2026-09-05")
    state.readings["W"].append(later)
    period = {**state.periods["a1"][0], "start": "2026-09-05", "closed_on": None}
    state.periods["a1"].append(period)
    assert next_day("2026-06-30") != period["start"]
    assert unbilled_stretches(state) == []


def test_a_stretch_billed_in_two_periods_is_reported():
    state = STATES["closed_next"].build(0)
    _row(state, "W", "2026-07-20")["closes_period"] = "2026-05-01"
    _row(state, "W", "2026-06-30")["closes_period"] = None
    assert "W 2026-06-30->2026-07-20 billed 2x" in unbilled_stretches(state)
