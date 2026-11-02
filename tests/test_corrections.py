"""Planning a correction with no I/O: which operations reach which record on a copy, what the plan
reports as changed, which figures it compares, and what it warns about or refuses."""

from datetime import date

import pytest

from common import texts
from common.access import DELEGATE, OWNER
from common.account import Account, diff, records
from common.corrections import _starts_of_copy, check, plan
from common.dates import next_day
from common.fields import FIELDS, InvalidValue, UnknownField, UnknownTarget
from common.model import Operation
from common.periods import consumption

TODAY = date(2026, 10, 29)
FIRST, SECOND = "2026-05-01", "2026-07-01"
LATE = "2026-05-20#s2"
SHARED = "2026-07-01#s3"
LAST = "2026-08-15#s4"


HELPER, OTHER, FRESH = "helper@x.com", "other@x.com", "fresh@x.com"


def _delegate(address):
    return {"email": address, "account": "owner@x.com", "role": DELEGATE}


def _reading(day, seq, value, opens=None, closes=None):
    return {
        "date": day,
        "seq": seq,
        "value": value,
        "image_key": "k",
        "recorded_by": "u",
        "opens_period": opens,
        "closes_period": closes,
        "resets": False,
    }


def _account() -> Account:
    """One water and one electricity meter of apartment a1, over a closed period whose closing
    reading also opens the open one."""
    readings = [
        _reading("2026-05-01", "s1", 1000, opens=FIRST),
        _reading("2026-05-20", "s2", 1040),
        _reading("2026-07-01", "s3", 1100, opens=SECOND, closes=FIRST),
        _reading("2026-08-15", "s4", 1150),
    ]
    electricity = [_reading("2026-07-01", "e1", 500, opens=SECOND)]
    return Account(
        delegates=[
            _delegate(HELPER),
            _delegate(OTHER),
        ],
        properties=[{"id": "p1", "name": "הגפן 7"}, {"id": "p2", "name": "לאונר 18"}],
        apartments=[
            {
                "id": "a1",
                "property_id": "p1",
                "name": "עורף",
                "period_start": FIRST,
                "period_months": 2,
            },
            {
                "id": "a2",
                "property_id": "p1",
                "name": "קדמית",
                "period_start": FIRST,
                "period_months": 2,
            },
            {
                "id": "a3",
                "property_id": "p2",
                "name": "גג",
                "period_start": FIRST,
                "period_months": 2,
            },
        ],
        meters=[
            {
                "serial": "E1",
                "apartment_id": "a1",
                "type": "electricity",
                "display_choice": 0,
                "label_hint": None,
                "retired_at": None,
                "former_serials": [],
                "replaces": None,
            },
            {
                "serial": "W1",
                "apartment_id": "a1",
                "type": "water",
                "display_choice": None,
                "label_hint": None,
                "retired_at": None,
                "former_serials": [],
                "replaces": None,
            },
        ],
        readings={"E1": electricity, "W1": readings},
        periods={
            "a1": [
                {"start": FIRST, "months": 2, "closed_on": "2026-07-01", "last_reminded_on": None},
                {"start": SECOND, "months": 2, "closed_on": None, "last_reminded_on": None},
            ],
            "a2": [],
            "a3": [],
        },
        tariffs=[
            {
                "effective_date": "2026-01-01",
                "electricity_per_kwh": 0.5,
                "water_per_m3": 10,
                "source_image_key": None,
            }
        ],
    )


def _set(target, field, value) -> Operation:
    return Operation(action="set", target=target, field=field, value=value)


def _delete(target) -> Operation:
    return Operation(action="delete", target=target, field=None, value=None)


def _plan(*operations, before=None, role=OWNER):
    return plan(_account() if before is None else before, list(operations), TODAY, role)


def test_a_value_change_is_made_on_the_copy_and_reported():
    before = _account()
    planned = _plan(_set(f"reading|W1|{LATE}", "value", "1050"), before=before)
    assert before == _account()
    assert planned.after.readings["W1"][1]["value"] == 1050
    [change] = planned.changes
    assert (change.old, change.new) == ("1040", "1050")
    assert change.field_label == FIELDS[("reading", "value")].label
    assert "W1" in change.record_label and "20/05/2026" in change.record_label
    assert planned.refusals == [] and planned.warnings == []


def test_a_value_inside_a_period_changes_no_figure():
    assert _plan(_set(f"reading|W1|{LATE}", "value", "1050")).figures == []


def test_a_shared_row_changes_the_figures_of_both_periods():
    [closed, opened] = _plan(_set(f"reading|W1|{SHARED}", "value", "1110")).figures
    assert (closed.start, closed.serial) == (FIRST, "W1")
    assert (closed.before.consumption.units, closed.after.consumption.units) == (100, 110)
    assert (closed.before.amount, closed.after.amount) == (1000.0, 1100.0)
    assert closed.apartment == "עורף" and closed.property == "הגפן 7"
    assert opened.start == SECOND
    assert (opened.before.consumption.units, opened.after.consumption.units) == (50, 40)


def test_a_tariff_price_names_every_period_of_that_meter_type_and_no_other():
    planned = _plan(_set("tariff|2026-01-01", "water_price", "12"))
    assert [(f.serial, f.start) for f in planned.figures] == [("W1", FIRST), ("W1", SECOND)]
    assert planned.after.tariffs[0]["water_per_m3"] == 12
    assert planned.figures[0].after.amount == 1200.0


def test_a_period_length_changes_no_figure():
    planned = _plan(_set(f"period|a1|{SECOND}", "months", "3"))
    assert planned.after.periods["a1"][1]["months"] == 3
    assert planned.figures == []


def test_a_reset_flag_restarts_the_stretch_and_shows_as_yes_and_no():
    planned = _plan(_set(f"reading|W1|{LAST}", "resets", "true"))
    assert planned.after.readings["W1"][3]["resets"] is True
    [change] = planned.changes
    assert (change.old, change.new) == ("לא", "כן")
    [changed] = planned.figures
    assert changed.after.consumption.units == 0


@pytest.mark.parametrize(
    ("target", "field", "raw", "read", "expected"),
    [
        ("property|p1", "name", "הגפן 9", lambda a: a.properties[0]["name"], "הגפן 9"),
        ("apartment|a1", "name", "עורף 2", lambda a: a.apartments[0]["name"], "עורף 2"),
    ],
)
def test_a_plain_field_with_no_figure_sets_its_attribute(target, field, raw, read, expected):
    planned = _plan(_set(target, field, raw))
    assert read(planned.after) == expected
    assert planned.figures == []


def test_apartment_months_set_the_open_period_too():
    planned = _plan(_set("apartment|a1", "months", "6"))
    assert planned.after.apartments[0]["period_months"] == 6
    assert [p["months"] for p in planned.after.periods["a1"]] == [2, 6]
    assert planned.figures == []


def test_apartment_months_with_no_open_period_set_only_the_apartment():
    planned = _plan(_set("apartment|a2", "months", "6"))
    assert planned.after.apartments[1]["period_months"] == 6
    assert len(planned.changes) == 1 and planned.figures == []


def test_apartment_months_compare_with_the_open_periods_length():
    before = _account()
    before.periods["a1"][1]["months"] = 3
    planned = _plan(_set("apartment|a1", "months", "2"), before=before)
    [change] = planned.changes
    assert (change.old, change.new) == ("3", "2")
    assert planned.after.periods["a1"][1]["months"] == 2


def test_two_operations_apply_in_turn():
    planned = _plan(
        _set("property|p1", "name", "הגפן 9"), _set(f"reading|W1|{SHARED}", "value", "1110")
    )
    assert len(planned.changes) == 2
    assert planned.after.properties[0]["name"] == "הגפן 9"


def test_a_value_equal_to_the_stored_one_is_no_change():
    planned = _plan(_set(f"reading|W1|{LATE}", "value", "1040"))
    assert planned.changes == []


def test_display_is_stored_as_the_choice_and_the_past_readings_are_listed():
    before = _account()
    before.readings["E1"].append(_reading("2026-08-01", "e2", 640))
    planned = _plan(_set("meter|E1", "display", "bottom"), before=before)
    assert planned.after.meters[0]["display_choice"] == 1
    [change] = planned.changes
    assert (change.old, change.new) == (texts.POSITION_NAMES["top"], texts.POSITION_NAMES["bottom"])
    lines = "\n".join(planned.warnings)
    assert "500 (01/07/2026)" in lines and "640 (01/08/2026)" in lines
    assert planned.refusals == []


def test_display_is_refused_on_a_single_counter_meter():
    planned = _plan(_set("meter|W1", "display", "top"))
    assert len(planned.refusals) == 1 and "W1" in planned.refusals[0]


def test_a_value_out_of_order_with_its_neighbors_is_a_warning_not_a_refusal():
    planned = _plan(_set(f"reading|W1|{LATE}", "value", "1200"))
    assert planned.refusals == []
    [warning] = planned.warnings
    assert "1,200" in warning and "1,100" in warning


def test_a_pair_already_out_of_order_is_not_warned_about_again():
    before = _account()
    before.readings["W1"][1]["value"] = 1200
    planned = _plan(_set("property|p1", "name", "הגפן 9"), before=before)
    assert planned.warnings == []


def test_a_reset_reading_may_be_lower_than_the_one_before():
    before = _account()
    before.readings["W1"][3]["resets"] = True
    planned = _plan(_set(f"reading|W1|{LAST}", "value", "3"), before=before)
    assert planned.warnings == []


def test_clearing_a_reset_warns_when_the_value_is_lower():
    before = _account()
    before.readings["W1"][3].update(resets=True, value=3)
    planned = _plan(_set(f"reading|W1|{LAST}", "resets", "false"), before=before)
    assert len(planned.warnings) == 1


def test_a_duplicate_property_name_is_refused():
    planned = _plan(_set("property|p1", "name", "לאונר 18"))
    assert planned.refusals == [texts.duplicate_property("לאונר 18")]


def test_a_duplicate_apartment_name_in_one_property_is_refused():
    planned = _plan(_set("apartment|a1", "name", "קדמית"))
    assert planned.refusals == [texts.duplicate_apartment("קדמית", "הגפן 7")]


def test_an_apartment_name_may_repeat_in_another_property():
    assert _plan(_set("apartment|a1", "name", "גג")).refusals == []


def test_a_duplicate_already_on_record_does_not_block_another_change():
    before = _account()
    before.properties[1]["name"] = "הגפן 7"
    assert _plan(_set("apartment|a1", "months", "3"), before=before).refusals == []


@pytest.mark.parametrize(
    "target",
    [
        "reading|W1|2026-01-01#nope",
        "meter|W9",
        "period|a1|2020-01-01",
        "tariff|2020-01-01",
        # An id of another record kind.
        "property|a1",
        "apartment|p1",
        "photo|2026-10-01",
        "card|2026-10-01",
        "nonsense|x",
        "reading|W1",
    ],
)
def test_a_target_outside_the_account_raises_unknown_target(target):
    with pytest.raises(UnknownTarget):
        _plan(_set(target, "value", "1"))


@pytest.mark.parametrize(
    ("target", "field"),
    [
        ("property|p1", "color"),
        ("property|p1", None),
    ],
)
def test_a_field_with_no_correction_yet_raises_unknown_field(target, field):
    with pytest.raises(UnknownField):
        _plan(_set(target, field, "1"))


def test_a_delete_of_an_unknown_target_raises():
    with pytest.raises(UnknownTarget):
        _plan(_delete("property|nope"))


@pytest.mark.parametrize(
    ("target", "field", "raw", "current"),
    [
        (f"reading|W1|{LATE}", "value", "abc", "1040"),
        (f"reading|W1|{LATE}", "value", None, "1040"),
        (f"reading|W1|{LATE}", "resets", "maybe", "לא"),
        ("meter|E1", "display", "left", texts.POSITION_NAMES["top"]),
        ("meter|W1", "display", "left", texts.EMPTY_VALUE),
        ("tariff|2026-01-01", "water_price", "0", "10"),
    ],
)
def test_an_invalid_value_raises_with_the_value_on_record(target, field, raw, current):
    with pytest.raises(InvalidValue) as raised:
        _plan(_set(target, field, raw))
    assert raised.value.current == current


def _meter(serial, apartment_id, meter_type="water", retired_at=None):
    return {
        "serial": serial,
        "apartment_id": apartment_id,
        "type": meter_type,
        "display_choice": None,
        "label_hint": None,
        "retired_at": retired_at,
        "former_serials": [],
        "replaces": None,
    }


def _with_meter(meter, readings) -> Account:
    state = _account()
    state.meters.append(meter)
    state.readings[meter["serial"]] = readings
    return state


def _period(start, closed_on=None):
    return {"start": start, "months": 2, "closed_on": closed_on, "last_reminded_on": None}


def _flags(state, serial) -> list[tuple]:
    return [(r["date"], r["opens_period"], r["closes_period"]) for r in state.readings[serial]]


def test_serial_rename_keeps_readings_and_records_former_serial():
    before = _account()
    planned = _plan(_set("meter|W1", "serial", "W2"), before=before)
    assert "W1" not in planned.after.readings
    assert _flags(planned.after, "W2") == _flags(before, "W1")
    assert records(planned.after, "meter")["W2"]["former_serials"] == ["W1"]
    [change] = planned.changes
    assert (change.old, change.new) == ("W1", "W2")
    assert planned.figures == [] and planned.refusals == [] and planned.warnings == []
    writes = diff(before, planned.after)
    assert [record["serial"] for _, record in writes.deletes["meters"]] == ["W1"]
    assert len(writes.deletes["readings"]) == len(writes.puts["readings"]) == 4


def test_a_serial_renamed_back_is_not_among_its_former_serials():
    before = _account()
    records(before, "meter")["W1"]["former_serials"] = ["W2"]
    planned = _plan(_set("meter|W1", "serial", "W2"), before=before)
    assert records(planned.after, "meter")["W2"]["former_serials"] == ["W1"]


def test_serial_onto_existing_meter_merges_readings():
    other = _meter("W2", "a1", retired_at="2026-08-15")
    other["former_serials"] = ["W0"]
    before = _with_meter(other, [_reading("2026-06-01", "s5", 1070)])
    planned = _plan(_set("meter|W1", "serial", "W2"), before=before)
    assert planned.refusals == []
    assert "W1" not in records(planned.after, "meter")
    assert "W1" not in planned.after.readings
    assert [r["date"] for r in planned.after.readings["W2"]] == [
        "2026-05-01",
        "2026-05-20",
        "2026-06-01",
        "2026-07-01",
        "2026-08-15",
    ]
    merged = records(planned.after, "meter")["W2"]
    assert merged["former_serials"] == ["W0", "W1"]
    assert merged["retired_at"] is None


def test_a_merge_keeps_each_period_flag_on_the_outermost_reading():
    spurious = _reading("2026-08-20", "s5", 1160, opens=SECOND)
    before = _with_meter(_meter("W2", "a1"), [spurious])
    planned = _plan(_set("meter|W2", "serial", "W1"), before=before)
    assert [r for r in _flags(planned.after, "W1") if r[1] == SECOND] == [
        ("2026-07-01", SECOND, FIRST)
    ]


def test_a_merged_meters_own_former_serials_pass_to_the_kept_meter():
    before = _with_meter(_meter("W2", "a1"), [])
    records(before, "meter")["W1"]["former_serials"] = ["W0"]
    planned = _plan(_set("meter|W1", "serial", "W2"), before=before)
    assert planned.refusals == []
    assert records(planned.after, "meter")["W2"]["former_serials"] == ["W0", "W1"]


def test_a_merge_compares_the_kept_meters_figures():
    before = _with_meter(
        _meter("W2", "a1", retired_at="2026-08-20"),
        [_reading("2026-08-20", "s5", 1160)],
    )
    planned = _plan(_set("meter|W2", "serial", "W1"), before=before)
    assert [(f.serial, f.start) for f in planned.figures] == [("W1", SECOND)]
    [changed] = planned.figures
    assert (changed.before.consumption.units, changed.after.consumption.units) == (50, 60)


def test_serial_onto_existing_meter_refused_on_same_day_clash():
    before = _with_meter(_meter("W2", "a1"), [_reading("2026-05-20", "s5", 1070)])
    planned = _plan(_set("meter|W1", "serial", "W2"), before=before)
    assert planned.refusals == [texts.same_day_clash("W2", date(2026, 5, 20))]


@pytest.mark.parametrize(
    ("meter", "readings"),
    [
        (_meter("W2", "a1", "electricity"), []),
        (_meter("W2", "a2"), []),
    ],
    ids=["other type", "other apartment"],
)
def test_serial_onto_a_meter_of_another_type_or_apartment_is_refused(meter, readings):
    before = _with_meter(meter, readings)
    planned = _plan(_set("meter|W1", "serial", "W2"), before=before)
    assert texts.serial_taken("W2") in planned.refusals


def test_an_operation_on_a_renamed_meters_reading_applies_whatever_the_order():
    planned = _plan(_set("meter|W1", "serial", "W2"), _set(f"reading|W1|{LAST}", "value", "1160"))
    assert planned.refusals == []
    assert planned.after.readings["W2"][3]["value"] == 1160


def test_reassign_rebuilds_flags_from_target_periods():
    before = _account()
    before.periods["a2"] = [_period("2026-04-15", "2026-06-15"), _period("2026-06-16")]
    planned = _plan(_set("meter|W1", "apartment", "a2"), before=before)
    assert planned.refusals == []
    assert records(planned.after, "meter")["W1"]["apartment_id"] == "a2"
    assert _flags(planned.after, "W1") == [
        ("2026-05-01", "2026-04-15", None),
        ("2026-05-20", "2026-05-21", "2026-04-15"),
        ("2026-07-01", None, None),
        ("2026-08-15", None, None),
    ]
    closed, following = planned.after.periods["a2"]
    assert (closed["closed_on"], following["start"]) == ("2026-05-20", "2026-05-21")
    [change] = planned.changes
    assert (change.old, change.new) == ("עורף", "קדמית")
    assert {(f.apartment, f.start) for f in planned.figures} == {
        ("עורף", FIRST),
        ("עורף", SECOND),
        ("קדמית", "2026-04-15"),
        ("קדמית", "2026-05-21"),
    }


def test_reassign_leaves_no_opening_across_a_gap_after_a_vacate():
    before = _account()
    before.periods["a2"] = [_period("2026-04-15", "2026-06-15"), _period("2026-08-01")]
    planned = _plan(_set("meter|W1", "apartment", "a2"), before=before)
    assert _flags(planned.after, "W1") == [
        ("2026-05-01", "2026-04-15", None),
        ("2026-05-20", None, "2026-04-15"),
        ("2026-07-01", None, None),
        ("2026-08-15", "2026-08-01", None),
    ]


def test_reassign_copies_periods_to_a_target_with_none():
    before = _account()
    planned = _plan(_set("meter|W1", "apartment", "a3"), before=before)
    assert [(p["start"], p["closed_on"]) for p in planned.after.periods["a3"]] == [
        (FIRST, "2026-07-01"),
        (SECOND, None),
    ]
    assert planned.after.periods["a1"] == before.periods["a1"]
    assert _flags(planned.after, "W1")[2] == ("2026-07-01", SECOND, FIRST)


def test_reassign_is_refused_on_readings_before_target_periods_the_copies_would_overlap():
    before = _account()
    before.periods["a2"] = [_period("2026-08-01")]
    planned = _plan(_set("meter|W1", "apartment", "a2"), before=before)
    days = [date(2026, 5, 1), date(2026, 5, 20), date(2026, 7, 1)]
    assert planned.refusals == [texts.reassign_uncovered("קדמית", days)]


def test_reassign_copies_periods_that_all_end_before_the_targets_first():
    before = _account()
    before.periods["a1"][1]["closed_on"] = "2026-07-20"
    before.periods["a2"] = [_period("2026-08-01")]
    planned = _plan(_set("meter|W1", "apartment", "a2"), before=before)
    assert planned.refusals == []
    assert len(planned.after.periods["a2"]) == 3


def test_reassign_of_a_meter_outside_every_period_needs_no_cover():
    before = _with_meter(_meter("W2", "a2"), [_reading("2026-01-01", "x", 1)])
    planned = _plan(_set("meter|W2", "apartment", "a3"), before=before)
    assert planned.refusals == []


def test_reassign_refused_on_a_live_meter_of_that_type():
    before = _with_meter(_meter("W2", "a2"), [])
    planned = _plan(_set("meter|W1", "apartment", "a2"), before=before)
    assert planned.refusals == [texts.live_meter_exists("water", "קדמית")]


def test_reassign_to_an_unknown_apartment_is_an_invalid_value():
    with pytest.raises(InvalidValue) as raised:
        _plan(_set("meter|W1", "apartment", "a9"))
    assert raised.value.current == "עורף"


def test_meter_type_refused_on_a_live_meter_of_that_type():
    planned = _plan(_set("meter|W1", "type", "electricity"))
    assert planned.refusals == [texts.live_meter_exists("electricity", "עורף")]


def test_meter_type_reprices_its_periods_and_a_single_counter_keeps_no_display():
    before = _account()
    records(before, "meter")["W1"]["retired_at"] = "2026-08-15"
    planned = _plan(_set("meter|E1", "type", "water"), before=before)
    assert planned.refusals == []
    meter = records(planned.after, "meter")["E1"]
    assert (meter["type"], meter["display_choice"]) == ("water", None)


def test_meter_type_shows_each_figure_in_its_own_unit():
    before = _account()
    records(before, "meter")["E1"]["retired_at"] = "2026-08-15"
    closed = _plan(_set("meter|W1", "type", "electricity"), before=before).figures[0]
    assert (closed.before_type, closed.meter_type) == ("water", "electricity")
    assert (closed.before.amount, closed.after.amount) == (1000.0, 50.0)


def test_reading_date_moves_closed_on_and_next_start():
    planned = _plan(_set(f"reading|W1|{SHARED}", "date", "2026-06-25"))
    closed, following = planned.after.periods["a1"]
    assert (closed["closed_on"], following["start"]) == ("2026-06-25", "2026-06-26")
    assert _flags(planned.after, "W1")[2] == ("2026-06-25", "2026-06-26", FIRST)
    assert planned.after.readings["E1"][0]["opens_period"] == "2026-06-26"
    assert planned.refusals == []
    assert len(planned.changes) == 1
    assert texts.opening_moved("electricity", "E1", 500, date(2026, 7, 1), date(2026, 6, 26)) in (
        planned.warnings
    )


def test_a_reading_date_keeps_the_meters_readings_in_order():
    planned = _plan(_set(f"reading|W1|{LATE}", "date", "2026-07-20"))
    assert [r["seq"] for r in planned.after.readings["W1"]] == ["s1", "s3", "s2", "s4"]
    assert planned.after.periods == _account().periods


def test_reading_date_same_day_clash_refused():
    planned = _plan(_set(f"reading|W1|{LATE}", "date", "2026-07-01"))
    assert planned.refusals == [texts.same_day_clash("W1", date(2026, 7, 1))]


def test_a_reading_date_after_today_is_an_invalid_value():
    with pytest.raises(InvalidValue):
        _plan(_set(f"reading|W1|{LATE}", "date", "2026-10-30"))


def test_a_closing_reading_sorted_before_its_opening_is_refused():
    planned = _plan(_set("reading|W1|2026-05-01#s1", "date", "2026-07-20"))
    assert planned.refusals == [texts.closing_before_opening("W1", date(2026, 5, 1))]


def test_reading_moved_to_another_meter():
    before = _with_meter(
        _meter("W2", "a2"), [_reading("2026-04-01", "s5", 7), _reading("2026-06-01", "s6", 9)]
    )
    before.periods["a2"] = [_period("2026-01-01")]
    planned = _plan(_set(f"reading|W1|{LATE}", "serial", "W2"), before=before)
    assert planned.refusals == []
    assert [r["seq"] for r in planned.after.readings["W1"]] == ["s1", "s3", "s4"]
    assert [r["seq"] for r in planned.after.readings["W2"]] == ["s5", "s2", "s6"]
    [change] = planned.changes
    assert (change.old, change.new) == ("W1", "W2")


def test_a_moved_reading_opens_the_open_period_of_a_target_with_no_opening_there():
    before = _with_meter(_meter("W2", "a2"), [])
    before.periods["a2"] = [_period("2026-05-10")]
    planned = _plan(_set(f"reading|W1|{LATE}", "serial", "W2"), before=before)
    assert _flags(planned.after, "W2") == [("2026-05-20", "2026-05-10", None)]


def test_a_moved_reading_joins_a_target_that_has_its_opening_with_no_flag():
    planned = _plan(_set(f"reading|W1|{LAST}", "serial", "E1"))
    assert _flags(planned.after, "E1") == [
        ("2026-07-01", SECOND, None),
        ("2026-08-15", None, None),
    ]


def test_a_moved_opening_reading_passes_its_flag_to_the_next_reading():
    before = _with_meter(_meter("W2", "a2"), [])
    planned = _plan(_set("reading|W1|2026-05-01#s1", "serial", "W2"), before=before)
    assert _flags(planned.after, "W1")[0] == ("2026-05-20", FIRST, None)
    assert _flags(planned.after, "W2") == [("2026-05-01", None, None)]


def test_a_moved_closing_reading_passes_its_flags_and_realigns_the_periods():
    before = _with_meter(_meter("W2", "a2"), [])
    before.periods["a2"] = [_period("2026-01-01")]
    planned = _plan(_set(f"reading|W1|{SHARED}", "serial", "W2"), before=before)
    assert planned.refusals == []
    assert _flags(planned.after, "W1") == [
        ("2026-05-01", FIRST, None),
        ("2026-05-20", "2026-05-21", FIRST),
        ("2026-08-15", None, None),
    ]
    closed, following = planned.after.periods["a1"]
    assert (closed["closed_on"], following["start"]) == ("2026-05-20", "2026-05-21")


def test_a_moved_closing_reading_with_none_before_it_reopens_the_period():
    before = _with_meter(_meter("W2", "a2"), [])
    del before.readings["W1"][1]
    before.periods["a2"] = [_period("2026-01-01")]
    planned = _plan(_set(f"reading|W1|{SHARED}", "serial", "W2"), before=before)
    assert planned.refusals == []
    [reopened] = planned.after.periods["a1"]
    assert (reopened["start"], reopened["closed_on"]) == (FIRST, None)
    assert _flags(planned.after, "W1") == [
        ("2026-05-01", FIRST, None),
        ("2026-08-15", None, None),
    ]
    assert planned.after.readings["E1"][0]["opens_period"] == FIRST


def test_a_period_reopening_before_a_closed_period_is_refused():
    before = _with_meter(_meter("W2", "a2"), [])
    del before.readings["W1"][1]
    before.periods["a1"][1]["closed_on"] = "2026-09-01"
    planned = _plan(_set(f"reading|W1|{SHARED}", "serial", "W2"), before=before)
    assert planned.refusals == [texts.reopen_refused("עורף", date(2026, 5, 1))]


def test_a_reopen_with_a_closed_period_then_an_open_one_is_refused_not_raised():
    before = _account()
    third = "2026-08-16"
    del before.readings["W1"][1]
    before.readings["W1"][2].update(closes_period=SECOND, opens_period=third)
    before.periods["a1"][1]["closed_on"] = LAST.split("#")[0]
    before.periods["a1"].append(_period(third))
    planned = _plan(_set(f"reading|W1|{SHARED}", "serial", "E1"), before=before)
    assert planned.refusals == [texts.reopen_refused("עורף", date(2026, 5, 1))]


def test_a_reopen_beside_a_period_left_open_by_an_earlier_operation_is_refused_not_raised():
    """A closing reading chosen inside the open period moves the closed period after the open
    one; a shared row of that period moved away then reopens it beside the open one."""
    before = _account()
    third = "2026-08-16"
    before.readings["W1"][3].update(closes_period=SECOND, opens_period=third)
    before.readings["W1"].append(_reading("2026-09-01", "s5", 1200))
    before.readings["E1"] = [
        _reading("2026-05-01", "e0", 400, opens=FIRST),
        _reading("2026-07-01", "e1", 500, opens=SECOND, closes=FIRST),
        _reading("2026-08-15", "e2", 600, opens=third, closes=SECOND),
    ]
    before.periods["a1"][1]["closed_on"] = LAST.split("#")[0]
    before.periods["a1"].append(_period(third))
    planned = _plan(
        _set(f"period|a1|{FIRST}", "closing", "W1|2026-09-01#s5"),
        _set("reading|E1|2026-08-15#e2", "serial", "W1"),
        before=before,
    )
    assert planned.refusals == [texts.two_open_periods("עורף")]
    assert "הודעה" in planned.refusals[0]


@pytest.mark.parametrize(
    "follow_up",
    [_set("apartment|a1", "months", "3"), _set("reading|X|2026-09-03#x2", "serial", "W1")],
)
def test_a_vacate_undone_beside_a_period_left_open_is_refused_not_raised(follow_up):
    """A closing reading chosen inside the open period moves the closed period after it past the
    open one, where it reads as a vacate; undoing that vacate would open a second period."""
    before = _account()
    third = "2026-08-16"
    before.readings["W1"][3].update(closes_period=SECOND, opens_period=third)
    before.readings["W1"].append(_reading("2026-09-01", "s5", 1200))
    before.readings["E1"] = [
        _reading("2026-05-01", "e0", 400, opens=FIRST),
        _reading("2026-07-01", "e1", 500, opens=SECOND, closes=FIRST),
        _reading("2026-08-15", "e2", 600, opens=third, closes=SECOND),
    ]
    before.periods["a1"][1]["closed_on"] = LAST.split("#")[0]
    before.periods["a1"].append(_period(third))
    before.meters.append(_meter("X", "a2"))
    before.readings["X"] = [
        _reading("2026-01-01", "x1", 1, opens="2026-01-01"),
        _reading("2026-09-03", "x2", 9),
    ]
    before.periods["a2"] = [_period("2026-01-01")]
    planned = _plan(
        _set(f"period|a1|{FIRST}", "closing", "W1|2026-09-01#s5"),
        _set("period|a1|2026-09-02", "vacant", "false"),
        follow_up,
        before=before,
    )
    assert planned.refusals == [texts.two_open_periods("עורף")]


def test_a_moved_reading_that_strands_a_closing_reading_is_refused():
    before = _with_meter(_meter("W2", "a2"), [])
    del before.readings["W1"][1]
    planned = _plan(_set("reading|W1|2026-05-01#s1", "serial", "W2"), before=before)
    assert planned.refusals == [texts.period_loses_opening("W1", date(2026, 5, 1))]


def test_a_reading_moved_onto_a_day_the_meter_has_is_refused():
    before = _with_meter(_meter("W2", "a2"), [_reading("2026-05-20", "s5", 7)])
    planned = _plan(_set(f"reading|W1|{LATE}", "serial", "W2"), before=before)
    assert planned.refusals == [texts.same_day_clash("W2", date(2026, 5, 20))]


def test_a_reading_moved_to_an_unknown_meter_is_an_invalid_value():
    with pytest.raises(InvalidValue) as raised:
        _plan(_set(f"reading|W1|{LATE}", "serial", "W9"))
    assert raised.value.current == "W1"


def test_a_moved_period_start_points_both_flags_at_it():
    planned = _plan(_set(f"period|a1|{FIRST}", "start", "2026-04-01"))
    assert planned.refusals == []
    assert planned.after.periods["a1"][0]["start"] == "2026-04-01"
    flags = _flags(planned.after, "W1")
    assert flags[0][1] == "2026-04-01" and flags[2][2] == "2026-04-01"
    assert [f.start for f in planned.figures] == []


def test_a_moved_period_compares_with_its_own_figures_under_the_old_start():
    """Moving the start keeps the period's readings, so matched with itself it changes nothing;
    matched by its new start it would read as one period gone and another added."""
    planned = _plan(_set(f"period|a1|{SECOND}", "start", "2026-08-01"))
    assert planned.refusals == []
    assert planned.figures == []


def test_a_moved_period_start_lists_the_opening_readings_it_moves():
    planned = _plan(_set(f"period|a1|{SECOND}", "start", "2026-07-15"))
    assert texts.opening_moved("water", "W1", 1100, date(2026, 7, 1), date(2026, 7, 15)) in (
        planned.warnings
    )


def test_closed_period_start_refused_on_overlap():
    planned = _plan(_set(f"period|a1|{FIRST}", "start", "2026-07-05"))
    assert texts.periods_overlap("עורף", date(2026, 7, 5)) in planned.refusals


def test_a_closed_periods_start_is_refused_on_overlap_with_the_period_before():
    before = _account()
    before.periods["a1"].insert(0, _period("2026-03-01", "2026-04-20"))
    planned = _plan(_set(f"period|a1|{FIRST}", "start", "2026-04-10"), before=before)
    assert planned.refusals == [texts.periods_overlap("עורף", date(2026, 3, 1))]


def test_an_open_period_moved_into_the_closed_one_is_refused():
    planned = _plan(_set(f"period|a1|{SECOND}", "start", "2026-06-01"))
    assert planned.refusals == [texts.periods_overlap("עורף", date(2026, 5, 1))]


def test_a_period_moved_onto_its_neighbors_start_is_refused():
    planned = _plan(_set(f"period|a1|{SECOND}", "start", FIRST))
    assert planned.refusals == [texts.periods_overlap("עורף", date(2026, 5, 1))]


def _two_tariffs() -> Account:
    before = _account()
    before.tariffs.append({**before.tariffs[0], "effective_date": SECOND, "water_per_m3": 20})
    return before


def test_tariff_date_moves_and_names_changed_periods():
    planned = _plan(_set(f"tariff|{SECOND}", "effective_date", "2026-08-01"), before=_two_tariffs())
    assert [t["effective_date"] for t in planned.after.tariffs] == ["2026-01-01", "2026-08-01"]
    [changed] = planned.figures
    assert (changed.serial, changed.start) == ("W1", FIRST)
    assert (changed.before.amount, changed.after.amount) == (2000.0, 1000.0)


def test_a_tariff_moved_before_another_keeps_them_sorted_by_date():
    planned = _plan(_set(f"tariff|{SECOND}", "effective_date", "2025-12-01"), before=_two_tariffs())
    assert [t["effective_date"] for t in planned.after.tariffs] == ["2025-12-01", "2026-01-01"]


def test_a_tariff_moved_onto_another_tariffs_date_is_refused():
    planned = _plan(_set(f"tariff|{SECOND}", "effective_date", "2026-01-01"), before=_two_tariffs())
    assert planned.refusals == [texts.duplicate_tariff(date(2026, 1, 1))]


def test_apartment_moves_to_another_property():
    planned = _plan(_set("apartment|a2", "property", "p2"))
    assert planned.refusals == []
    assert records(planned.after, "apartment")["a2"]["property_id"] == "p2"
    [change] = planned.changes
    assert (change.old, change.new) == ("הגפן 7", "לאונר 18")


def test_an_apartment_moved_to_a_property_holding_its_name_is_refused():
    before = _account()
    records(before, "apartment")["a2"]["name"] = "גג"
    planned = _plan(_set("apartment|a2", "property", "p2"), before=before)
    assert planned.refusals == [texts.duplicate_apartment("גג", "לאונר 18")]


def test_an_apartment_moved_to_an_unknown_property_is_an_invalid_value():
    with pytest.raises(InvalidValue) as raised:
        _plan(_set("apartment|a2", "property", "p9"))
    assert raised.value.current == "הגפן 7"


def test_a_correction_over_one_transaction_is_refused_naming_its_size():
    before = _with_meter(
        _meter("W2", "a2"),
        [_reading(f"2026-0{1 + i % 9}-{1 + i // 9:02}", f"x{i}", i) for i in range(60)],
    )
    planned = _plan(_set("meter|W2", "serial", "W3"), before=before)
    assert planned.refusals == [texts.correction_too_large(122)]
    assert diff(before, planned.after).size() == 122


def test_a_retired_meter_may_move_to_an_apartment_with_a_live_meter_of_its_type():
    before = _with_meter(_meter("W2", "a2"), [])
    records(before, "meter")["W1"]["retired_at"] = "2026-08-15"
    before.periods["a2"] = [_period("2026-01-01")]
    assert _plan(_set("meter|W1", "apartment", "a2"), before=before).refusals == []


def test_a_retired_meter_and_its_live_successor_may_move_together():
    before = _with_meter(_meter("W2", "a1", retired_at="2026-05-01"), [])
    before.periods["a2"] = [_period("2026-01-01")]
    planned = _plan(
        _set("meter|W2", "apartment", "a2"), _set("meter|W1", "apartment", "a2"), before=before
    )
    assert planned.refusals == []


def test_a_retired_meter_may_take_the_type_of_a_live_meter():
    before = _account()
    records(before, "meter")["W1"]["retired_at"] = "2026-08-15"
    assert _plan(_set("meter|W1", "type", "electricity"), before=before).refusals == []


def test_reassign_with_a_single_reading_in_a_closed_period_carries_it_forward():
    rows = [
        _reading("2026-01-28", "m1", 100, opens="2026-01-01"),
        _reading("2026-02-28", "m2", 130),
        _reading("2026-03-20", "m3", 150),
    ]
    before = _with_meter(_meter("M", "a3"), rows)
    before.periods["a3"] = [_period("2026-01-01")]
    before.periods["a2"] = [_period("2026-01-10", "2026-01-31"), _period("2026-02-01")]
    planned = _plan(_set("meter|M", "apartment", "a2"), before=before)
    assert _flags(planned.after, "M") == [
        ("2026-01-28", "2026-02-01", None),
        ("2026-02-28", None, None),
        ("2026-03-20", None, None),
    ]
    closed, opened = planned.after.periods["a2"]
    assert consumption(planned.after.readings["M"], closed) is None
    assert consumption(planned.after.readings["M"], opened).units == 50


def test_reassign_carries_a_closing_reading_over_a_closed_period_with_nothing_later():
    rows = [
        _reading("2026-01-05", "m1", 100, opens="2026-01-01"),
        _reading("2026-01-20", "m2", 130),
        _reading("2026-03-10", "m3", 150),
    ]
    before = _with_meter(_meter("M", "a3"), rows)
    before.periods["a3"] = [_period("2026-01-01")]
    before.periods["a2"] = [
        _period("2026-01-01", "2026-01-31"),
        _period("2026-02-01", "2026-02-28"),
        _period("2026-03-01"),
    ]
    planned = _plan(_set("meter|M", "apartment", "a2"), before=before)
    assert _flags(planned.after, "M") == [
        ("2026-01-05", "2026-01-01", None),
        ("2026-01-20", "2026-03-01", "2026-01-01"),
        ("2026-03-10", None, None),
    ]


def _two_closers(moved_close, kept_close) -> Account:
    """W1 and E1 each close the first period, on the given days, and open the next one."""
    following = next_day(max(moved_close, kept_close))
    state = _account()
    state.readings["W1"] = [
        _reading("2026-05-01", "s1", 1000, opens=FIRST),
        _reading("2026-05-20", "s2", 1040),
        _reading(moved_close, "s3", 1100, opens=following, closes=FIRST),
    ]
    state.readings["E1"] = [
        _reading("2026-05-01", "e0", 400, opens=FIRST),
        _reading(kept_close, "e1", 500, opens=following, closes=FIRST),
    ]
    state.periods["a1"] = [
        _period(FIRST, max(moved_close, kept_close)),
        _period(following),
    ]
    return state


def test_a_reassign_realigns_the_period_its_closing_reading_leaves():
    planned = _plan(
        _set("meter|W1", "apartment", "a3"), before=_two_closers("2026-07-01", "2026-06-20")
    )
    assert planned.refusals == []
    closed, following = planned.after.periods["a1"]
    assert (closed["closed_on"], following["start"]) == ("2026-06-20", "2026-06-21")
    assert planned.after.readings["E1"][1]["opens_period"] == "2026-06-21"


def test_a_reassign_taking_the_only_closing_reading_leaves_closed_on():
    planned = _plan(_set("meter|W1", "apartment", "a3"))
    assert [p["closed_on"] for p in planned.after.periods["a1"]] == ["2026-07-01", None]


def test_a_copied_periods_closed_on_follows_the_rebuilt_closing_reading():
    planned = _plan(
        _set("meter|W1", "apartment", "a3"), before=_two_closers("2026-06-20", "2026-07-01")
    )
    closed, following = planned.after.periods["a3"]
    assert (closed["closed_on"], following["start"]) == ("2026-06-20", "2026-06-21")
    assert planned.after.periods["a1"][0]["closed_on"] == "2026-07-01"


def test_a_merge_keeps_the_next_periods_opening_on_a_closing_row():
    rows = [
        _reading("2026-06-25", "g1", 1050, opens=SECOND),
        _reading("2026-08-20", "g2", 1200),
    ]
    before = _with_meter(_meter("G", "a1"), rows)
    records(before, "meter")["W1"]["retired_at"] = "2026-06-25"
    planned = _plan(_set("meter|G", "serial", "W1"), before=before)
    flags = _flags(planned.after, "W1")
    assert ("2026-06-25", None, None) in flags
    assert ("2026-07-01", SECOND, FIRST) in flags


def test_a_merge_of_two_closing_rows_keeps_both_flags_on_the_later():
    rows = [
        _reading("2026-05-02", "g0", 1001, opens=FIRST),
        _reading("2026-07-05", "g1", 1120, opens=SECOND, closes=FIRST),
    ]
    before = _with_meter(_meter("G", "a1"), rows)
    planned = _plan(_set("meter|G", "serial", "W1"), before=before)
    flags = _flags(planned.after, "W1")
    assert ("2026-07-01", None, None) in flags
    assert ("2026-07-05", SECOND, FIRST) in flags
    assert ("2026-05-02", None, None) in flags


def test_a_merge_keeps_the_gone_meters_display_when_the_kept_meter_has_none():
    before = _with_meter(_meter("E2", "a1", "electricity"), [])
    planned = _plan(_set("meter|E1", "serial", "E2"), before=before)
    assert records(planned.after, "meter")["E2"]["display_choice"] == 0
    assert planned.warnings == []


def test_a_merge_of_two_displays_keeps_the_kept_meters_and_says_so():
    before = _with_meter({**_meter("E2", "a1", "electricity"), "display_choice": 1}, [])
    planned = _plan(_set("meter|E1", "serial", "E2"), before=before)
    assert records(planned.after, "meter")["E2"]["display_choice"] == 1
    assert planned.warnings == [texts.merged_display_differs("electricity", "E2", "bottom")]


def test_a_chain_of_serials_in_one_message_is_followed():
    planned = _plan(_set("meter|W1", "serial", "W2"), _set("meter|W2", "serial", "W3"))
    assert planned.refusals == [] and planned.figures == []
    assert records(planned.after, "meter")["W3"]["former_serials"] == ["W1", "W2"]


def test_a_shared_row_moved_away_gives_both_flags_to_the_reading_before_it():
    rows = [
        _reading("2026-01-01", "w1", 0, opens="2026-01-01"),
        _reading("2026-01-15", "w2", 10),
        _reading("2026-01-31", "w3", 20, opens="2026-02-01", closes="2026-01-01"),
        _reading("2026-02-20", "w4", 40),
        _reading("2026-03-01", "w5", 55),
    ]
    before = _with_meter(_meter("O", "a2"), [])
    before.readings["W1"] = rows
    before.readings["E1"] = []
    before.periods["a1"] = [_period("2026-01-01", "2026-01-31"), _period("2026-02-01")]
    before.periods["a2"] = [_period("2026-01-01")]
    planned = _plan(_set("reading|W1|2026-01-31#w3", "serial", "O"), before=before)
    assert planned.refusals == []
    assert _flags(planned.after, "W1")[1] == ("2026-01-15", "2026-01-16", "2026-01-01")
    closed, opened = planned.after.periods["a1"]
    assert closed["closed_on"] == "2026-01-15"
    readings = planned.after.readings["W1"]
    assert consumption(readings, closed).units == 10
    assert consumption(readings, opened).units == 45


def test_a_closing_row_that_opens_nothing_gives_only_its_closing():
    before = _with_meter(_meter("O", "a2"), [])
    before.readings["W1"][2]["opens_period"] = None
    before.periods["a1"].pop()
    planned = _plan(_set(f"reading|W1|{SHARED}", "serial", "O"), before=before)
    assert _flags(planned.after, "W1")[1] == ("2026-05-20", None, FIRST)


def _linked_alone() -> Account:
    """Meter N is all that apartment a4 and property p3 hold, in a period N's reading opened."""
    state = _with_meter(_meter("N", "a4"), [_reading("2026-08-20", "n1", 7, opens="2026-08-20")])
    state.properties.append({"id": "p3", "name": "הים 1"})
    state.apartments.append({**state.apartments[0], "id": "a4", "property_id": "p3", "name": "גן"})
    state.periods["a4"] = [_period("2026-08-20")]
    return state


def test_moving_a_meters_last_reading_undoes_its_link_and_the_replacement():
    old = _meter("E0", "a1", "electricity", retired_at="2026-07-01")
    rows = [
        _reading("2026-05-01", "e0", 400, opens=FIRST),
        _reading("2026-06-30", "e9", 450, closes=FIRST),
    ]
    before = _with_meter(old, rows)
    records(before, "meter")["E1"]["replaces"] = "E0"
    planned = _plan(_set("reading|E1|2026-07-01#e1", "serial", "E0"), before=before)
    assert planned.refusals == []
    meters = records(planned.after, "meter")
    assert "E1" not in meters and "E1" not in planned.after.readings
    assert meters["E0"]["retired_at"] is None
    assert [r["seq"] for r in planned.after.readings["E0"]] == ["e0", "e9", "e1"]
    assert texts.meter_removed("electricity", "E1") in planned.warnings
    assert texts.replacement_undone("electricity", "E0") in planned.warnings
    assert len(planned.after.periods["a1"]) == 2


def test_moving_a_meters_last_reading_removes_the_place_made_for_it():
    before = _linked_alone()
    planned = _plan(_set("reading|N|2026-08-20#n1", "serial", "W1"), before=before)
    assert planned.refusals == []
    assert "a4" not in records(planned.after, "apartment")
    assert "p3" not in records(planned.after, "property")
    assert "a4" not in planned.after.periods
    for line in (
        texts.period_removed("גן", date(2026, 8, 20)),
        texts.apartment_removed("גן"),
        texts.property_removed("הים 1"),
    ):
        assert line in planned.warnings
    writes = diff(before, planned.after)
    assert [record["id"] for _, record in writes.deletes["apartments"]] == ["a4"]


def test_a_period_another_meter_opens_stays_with_its_apartment():
    before = _linked_alone()
    before.meters.append(_meter("M", "a4", "electricity"))
    before.readings["M"] = [_reading("2026-08-20", "m1", 9, opens="2026-08-20")]
    planned = _plan(_set("reading|N|2026-08-20#n1", "serial", "W1"), before=before)
    assert len(planned.after.periods["a4"]) == 1
    assert "a4" in records(planned.after, "apartment")


def test_a_period_the_reading_did_not_open_stays():
    before = _linked_alone()
    before.readings["N"][0]["opens_period"] = None
    planned = _plan(_set("reading|N|2026-08-20#n1", "serial", "W1"), before=before)
    assert len(planned.after.periods["a4"]) == 1
    assert "N" not in records(planned.after, "meter")


def test_a_property_holding_another_apartment_stays():
    before = _linked_alone()
    before.apartments.append({**before.apartments[0], "id": "a5", "property_id": "p3"})
    before.periods["a5"] = []
    planned = _plan(_set("reading|N|2026-08-20#n1", "serial", "W1"), before=before)
    assert "p3" in records(planned.after, "property")


def test_choosing_another_closing_reading_moves_the_flags_and_realigns():
    planned = _plan(_set(f"period|a1|{FIRST}", "closing", "W1|2026-05-20#s2"))
    assert planned.refusals == []
    flags = _flags(planned.after, "W1")
    assert flags[1] == ("2026-05-20", "2026-05-21", FIRST)
    assert flags[2] == ("2026-07-01", None, None)
    closed, following = planned.after.periods["a1"]
    assert (closed["closed_on"], following["start"]) == ("2026-05-20", "2026-05-21")
    assert planned.after.readings["E1"][0]["opens_period"] == "2026-05-21"
    [change] = planned.changes
    assert "1,100" in change.old and "1,040" in change.new


def test_the_closing_reading_already_on_record_changes_nothing():
    assert _plan(_set(f"period|a1|{FIRST}", "closing", f"W1|{SHARED}")).changes == []


def test_choosing_a_closing_reading_for_a_meter_with_none_there_is_refused():
    planned = _plan(_set(f"period|a1|{SECOND}", "closing", f"W1|{LAST}"))
    assert planned.refusals == [texts.no_closing_reading("W1", date(2026, 7, 1))]


def test_choosing_the_opening_reading_as_the_closing_one_is_refused():
    planned = _plan(_set(f"period|a1|{FIRST}", "closing", "W1|2026-05-01#s1"))
    assert planned.refusals == [texts.closing_before_opening("W1", date(2026, 5, 1))]


def test_a_closing_reading_outside_the_account_is_an_invalid_value():
    with pytest.raises(InvalidValue):
        _plan(_set(f"period|a1|{FIRST}", "closing", "W1|2026-01-01#nope"))


def test_a_closing_reading_that_opens_another_period_is_refused_naming_it():
    before = _account()
    before.readings["W1"][2]["opens_period"] = None
    before.readings["W1"][3]["opens_period"] = SECOND
    planned = _plan(_set(f"period|a1|{FIRST}", "closing", f"W1|{LAST}"), before=before)
    taken = texts.closing_reading_taken(before.readings["W1"][3], date(2026, 7, 1))
    assert planned.refusals == [taken]


def test_a_closing_reading_that_closes_another_period_is_refused_naming_it():
    before = _account()
    before.readings["W1"][3].update(opens_period="2026-08-16", closes_period=SECOND)
    before.periods["a1"][1]["closed_on"] = "2026-08-15"
    before.periods["a1"].append(_period("2026-08-16"))
    planned = _plan(_set(f"period|a1|{FIRST}", "closing", f"W1|{LAST}"), before=before)
    taken = texts.closing_reading_taken(before.readings["W1"][3], date(2026, 7, 1))
    assert planned.refusals == [taken]


@pytest.mark.parametrize(
    "serials",
    [
        [("W1", "E1"), ("E1", "Z")],
        [("W1", "W2"), ("W2", "Z")],
        [("W2", "Z"), ("W1", "W2")],
        [("W1", "W2"), ("W2", "W1")],
    ],
    ids=["through another type", "through another apartment", "rotation", "swap"],
)
def test_renaming_onto_a_serial_the_same_message_renames_is_refused(serials):
    before = _with_meter(_meter("W2", "a2"), [])
    operations = [_set(f"meter|{old}", "serial", new) for old, new in serials]
    planned = _plan(*operations, before=before)
    assert planned.refusals == [texts.one_serial_at_a_time()]


def test_a_meter_renamed_to_its_own_serial_beside_a_merge_is_no_collision():
    before = _with_meter(_meter("W2", "a1"), [])
    planned = _plan(
        _set("meter|W1", "serial", "W2"), _set("meter|W2", "serial", " W2 "), before=before
    )
    assert planned.refusals == []


def test_an_edit_of_the_target_period_does_not_lift_the_uncovered_refusal():
    before = _account()
    before.periods["a2"] = [_period("2026-08-01")]
    planned = _plan(
        _set("meter|W1", "apartment", "a2"),
        _set("period|a2|2026-08-01", "months", "3"),
        before=before,
    )
    days = [date(2026, 5, 1), date(2026, 5, 20), date(2026, 7, 1)]
    assert planned.refusals == [texts.reassign_uncovered("קדמית", days)]


def _replaced_on(retired_at, replaces="E0") -> Account:
    """E1 linked as the replacement of E0, which holds a reading; E1 has one reading."""
    old = _meter("E0", "a1", "electricity", retired_at=retired_at)
    before = _with_meter(old, [_reading("2026-05-01", "e0", 400, opens=FIRST)])
    records(before, "meter")["E1"]["replaces"] = replaces
    return before


def test_the_link_finds_the_replaced_meter_whatever_the_readings_date_became():
    before = _replaced_on("2026-06-01")
    planned = _plan(_set("reading|E1|2026-07-01#e1", "serial", "W1"), before=before)
    assert records(planned.after, "meter")["E0"]["retired_at"] is None


def test_the_link_finds_a_replaced_meter_under_a_former_serial():
    before = _replaced_on("2026-06-01", replaces="E00")
    records(before, "meter")["E0"]["former_serials"] = ["E00"]
    planned = _plan(_set("reading|E1|2026-07-01#e1", "serial", "W1"), before=before)
    assert records(planned.after, "meter")["E0"]["retired_at"] is None


def test_a_meter_with_no_link_undoes_no_replacement_even_on_the_same_date():
    before = _replaced_on("2026-07-01", replaces=None)
    planned = _plan(_set("reading|E1|2026-07-01#e1", "serial", "W1"), before=before)
    assert records(planned.after, "meter")["E0"]["retired_at"] == "2026-07-01"


def test_a_merge_keeps_the_gone_meters_link_when_the_kept_meter_has_none():
    before = _with_meter(_meter("E2", "a1", "electricity"), [])
    records(before, "meter")["E1"]["replaces"] = "E0"
    planned = _plan(_set("meter|E1", "serial", "E2"), before=before)
    assert records(planned.after, "meter")["E2"]["replaces"] == "E0"


def test_deleting_an_ordinary_reading_removes_it_and_recomputes():
    planned = _plan(_delete(f"reading|W1|{LATE}"))
    assert planned.refusals == []
    assert [r["seq"] for r in planned.after.readings["W1"]] == ["s1", "s3", "s4"]
    [deleted] = planned.changes
    label = texts.reading_label("water", "W1", date(2026, 5, 20))
    assert deleted.line == texts.reading_removed(label, 1040)
    assert _flags(planned.after, "W1")[1] == ("2026-07-01", SECOND, FIRST)


def test_deleting_an_opening_reading_passes_its_flag_to_the_next_in_the_period():
    planned = _plan(_delete("reading|W1|2026-05-01#s1"))
    assert planned.refusals == []
    assert _flags(planned.after, "W1")[0] == ("2026-05-20", FIRST, None)
    period = planned.after.periods["a1"][0]
    assert consumption(planned.after.readings["W1"], period).units == 60
    assert any(f.start == FIRST and f.after.consumption.units == 60 for f in planned.figures)


def test_deleting_an_opening_reading_before_a_shared_row_is_refused():
    before = _account()
    del before.readings["W1"][1]
    planned = _plan(_delete("reading|W1|2026-05-01#s1"), before=before)
    assert planned.refusals == [texts.period_loses_opening("W1", date(2026, 5, 1))]


def test_deleting_an_opening_reading_and_the_shared_row_after_it_in_either_order():
    opening, shared = _delete("reading|W1|2026-05-01#s1"), _delete(f"reading|W1|{SHARED}")
    plans = []
    for operations in ((opening, shared), (shared, opening)):
        before = _account()
        del before.readings["W1"][1]
        plans.append(_plan(*operations, before=before))
    assert plans[0].refusals == plans[1].refusals == []
    assert plans[0].after == plans[1].after
    assert _flags(plans[0].after, "W1") == [("2026-08-15", FIRST, None)]


def test_deleting_a_period_s_only_reading_that_opens_and_closes_it_reopens_it():
    before = _vacated()
    del before.readings["W1"][:2]
    before.readings["W1"][0]["opens_period"] = FIRST
    planned = _plan(_delete(f"reading|W1|{SHARED}"), before=before)
    assert planned.refusals == []
    assert planned.after.periods["a1"][0]["closed_on"] is None


def test_deleting_a_retired_meters_last_reading_leaves_other_meters_alone():
    old = _meter("W0", "a1", retired_at="2026-07-01")
    before = _with_meter(old, [_reading("2026-05-01", "w0", 5)])
    records(before, "meter")["W1"]["replaces"] = "W0"
    planned = _plan(_delete("reading|W0|2026-05-01#w0"), before=before)
    assert planned.refusals == []
    assert "W0" not in records(planned.after, "meter")
    assert records(planned.after, "meter")["W1"]["retired_at"] is None


def _vacated() -> Account:
    """Apartment a1's only period is closed by a reading that opens nothing."""
    state = _account()
    state.readings["W1"][2]["opens_period"] = None
    del state.readings["W1"][3]
    state.readings["E1"] = [
        _reading("2026-05-01", "e0", 400, opens=FIRST),
        _reading("2026-07-01", "e1", 500, closes=FIRST),
    ]
    state.periods["a1"].pop()
    return state


def test_deleting_a_closing_reading_passes_its_flag_to_the_previous_reading():
    planned = _plan(_delete(f"reading|W1|{SHARED}"), before=_vacated())
    assert planned.refusals == []
    assert _flags(planned.after, "W1")[1] == ("2026-05-20", None, FIRST)
    assert planned.after.periods["a1"][0]["closed_on"] == "2026-07-01"


def test_deleting_the_closing_reading_on_the_latest_day_moves_closed_on():
    before = _vacated()
    before.readings["E1"][1]["date"] = "2026-06-01"
    planned = _plan(_delete(f"reading|W1|{SHARED}"), before=before)
    assert planned.after.periods["a1"][0]["closed_on"] == "2026-06-01"


def test_deleting_a_closing_reading_with_no_earlier_reading_reopens_the_period():
    before = _vacated()
    del before.readings["W1"][1]
    planned = _plan(_delete(f"reading|W1|{SHARED}"), before=before)
    assert planned.refusals == []
    assert planned.after.periods["a1"][0]["closed_on"] is None
    assert _flags(planned.after, "E1")[1] == ("2026-07-01", None, None)
    assert texts.period_reopened("עורף", date(2026, 5, 1)) in planned.warnings


def test_deleting_a_shared_row_gives_both_flags_to_the_previous_reading():
    planned = _plan(_delete(f"reading|W1|{SHARED}"))
    assert planned.refusals == []
    assert _flags(planned.after, "W1")[1] == ("2026-05-20", "2026-05-21", FIRST)
    closed, opened = planned.after.periods["a1"]
    assert (closed["closed_on"], opened["start"]) == ("2026-05-20", "2026-05-21")
    readings = planned.after.readings["W1"]
    assert consumption(readings, closed).units == 40
    assert consumption(readings, opened).units == 110


def test_deleting_a_shared_row_with_no_earlier_reading_reopens_and_merges_the_next_period():
    before = _account()
    del before.readings["W1"][1]
    planned = _plan(_delete(f"reading|W1|{SHARED}"), before=before)
    assert planned.refusals == []
    [period] = planned.after.periods["a1"]
    assert (period["start"], period["closed_on"]) == (FIRST, None)
    assert consumption(planned.after.readings["W1"], period).units == 150
    assert _flags(planned.after, "E1") == [("2026-07-01", FIRST, None)]
    assert texts.period_removed("עורף", date(2026, 7, 1)) in planned.warnings
    assert texts.period_reopened("עורף", date(2026, 5, 1)) in planned.warnings


def test_deleting_a_row_that_would_reopen_before_a_closed_period_is_refused():
    before = _account()
    del before.readings["W1"][1]
    before.periods["a1"][1]["closed_on"] = "2026-08-15"
    before.readings["W1"][-1]["closes_period"] = SECOND
    planned = _plan(_delete(f"reading|W1|{SHARED}"), before=before)
    assert planned.refusals == [texts.reopen_refused("עורף", date(2026, 5, 1))]


def test_deleting_a_meters_last_reading_undoes_the_link_and_the_replacement():
    old = _meter("E0", "a1", "electricity", retired_at="2026-07-01")
    before = _with_meter(old, [_reading("2026-05-01", "e0", 400, opens=FIRST)])
    records(before, "meter")["E1"]["replaces"] = "E0"
    planned = _plan(_delete("reading|E1|2026-07-01#e1"), before=before)
    assert planned.refusals == []
    meters = records(planned.after, "meter")
    assert "E1" not in meters and "E1" not in planned.after.readings
    assert meters["E0"]["retired_at"] is None
    assert texts.meter_removed("electricity", "E1") in planned.warnings
    assert texts.replacement_undone("electricity", "E0") in planned.warnings


def test_deleting_a_meters_last_reading_removes_the_place_made_for_it():
    before = _linked_alone()
    planned = _plan(_delete("reading|N|2026-08-20#n1"), before=before)
    assert planned.refusals == []
    assert "a4" not in records(planned.after, "apartment")
    assert "p3" not in records(planned.after, "property")
    for line in (
        texts.meter_removed("water", "N"),
        texts.period_removed("גן", date(2026, 8, 20)),
        texts.apartment_removed("גן"),
        texts.property_removed("הים 1"),
    ):
        assert line in planned.warnings


def test_deleting_a_meter_deletes_its_readings_by_the_reading_rule():
    planned = _plan(_delete("meter|W1"))
    assert planned.refusals == []
    assert "W1" not in records(planned.after, "meter")
    assert "W1" not in planned.after.readings
    [period] = planned.after.periods["a1"]
    assert (period["start"], period["closed_on"]) == (FIRST, None)
    assert _flags(planned.after, "E1") == [("2026-07-01", FIRST, None)]
    [deleted] = planned.changes
    assert deleted.line == texts.meter_removed("water", "W1")


def test_merging_two_properties_moves_the_apartment_then_deletes_the_emptied_property():
    planned = _plan(_delete("property|p2"), _set("apartment|a3", "property", "p1"))
    assert planned.refusals == []
    assert "p2" not in records(planned.after, "property")
    assert records(planned.after, "apartment")["a3"]["property_id"] == "p1"
    assert texts.property_removed("לאונר 18") in planned.warnings


def test_merging_two_apartments_reassigns_the_meters_then_deletes_the_emptied_apartment():
    before = _account()
    planned = _plan(
        _delete("apartment|a1"),
        _set("meter|E1", "apartment", "a2"),
        _set("meter|W1", "apartment", "a2"),
        before=before,
    )
    assert planned.refusals == []
    assert "a1" not in records(planned.after, "apartment")
    assert "a1" not in planned.after.periods
    assert [p["start"] for p in planned.after.periods["a2"]] == [FIRST, SECOND]
    assert {m["apartment_id"] for m in planned.after.meters} == {"a2"}
    assert texts.apartment_removed("עורף") in planned.warnings
    deleted = diff(before, planned.after).deletes["apartments"]
    assert [record["id"] for _, record in deleted] == ["a1"]


def test_a_merge_onto_a_live_meter_of_the_type_is_refused():
    before = _with_meter(_meter("W2", "a2"), [_reading("2026-05-01", "w", 5)])
    before.periods["a2"] = [_period(FIRST)]
    planned = _plan(
        _set("meter|W1", "apartment", "a2"),
        _set("meter|E1", "apartment", "a2"),
        _delete("apartment|a1"),
        before=before,
    )
    assert planned.refusals == [texts.live_meter_exists("water", "קדמית")]


def test_deleting_a_property_that_holds_apartments_is_refused_naming_them():
    planned = _plan(_delete("property|p1"))
    assert planned.refusals == [texts.property_holds_apartments("הגפן 7", ["עורף", "קדמית"])]


def test_deleting_an_apartment_that_holds_meters_is_refused_naming_them():
    planned = _plan(_delete("apartment|a1"))
    assert planned.refusals == [
        texts.apartment_holds_meters(
            "עורף",
            [texts.meter_name("electricity", "E1"), texts.meter_name("water", "W1")],
        )
    ]


def test_deleting_an_empty_apartment_keeps_its_property():
    before = _account()
    planned = _plan(_delete("apartment|a3"), before=before)
    assert planned.refusals == []
    assert "a3" not in records(planned.after, "apartment")
    assert "a3" not in planned.after.periods
    assert "p2" in records(planned.after, "property")
    [deleted] = planned.changes
    assert deleted.line == texts.apartment_removed("גג")


def test_deleting_an_apartment_left_with_periods_removes_them_and_says_so():
    before = _account()
    before.periods["a3"] = [_period(FIRST)]
    planned = _plan(_delete("apartment|a3"), before=before)
    assert planned.refusals == []
    assert texts.period_removed("גג", date(2026, 5, 1)) in planned.warnings
    assert "a3" not in planned.after.periods


def test_deleting_an_empty_property():
    before = _account()
    before.properties.append({"id": "p3", "name": "הים 1"})
    planned = _plan(_delete("property|p3"), before=before)
    assert planned.refusals == []
    assert "p3" not in records(planned.after, "property")


def test_deleting_a_period_is_refused_saying_what_to_do():
    planned = _plan(_delete(f"period|a1|{SECOND}"))
    assert planned.refusals == [texts.period_not_deletable("עורף", date(2026, 7, 1))]


def test_reopening_a_close_with_a_next_period_merges_it_back():
    planned = _plan(_set(f"period|a1|{FIRST}", "closed_on", None))
    assert planned.refusals == []
    [period] = planned.after.periods["a1"]
    assert (period["start"], period["closed_on"]) == (FIRST, None)
    assert _flags(planned.after, "W1")[2] == ("2026-07-01", None, None)
    assert _flags(planned.after, "E1") == [("2026-07-01", FIRST, None)]
    assert consumption(planned.after.readings["W1"], period).units == 150
    assert texts.period_removed("עורף", date(2026, 7, 1)) in planned.warnings
    [change] = planned.changes
    assert change.new == texts.EMPTY_VALUE


def test_reopening_a_period_before_a_closed_period_is_refused():
    before = _account()
    before.periods["a1"][1]["closed_on"] = "2026-08-15"
    before.readings["W1"][-1]["closes_period"] = SECOND
    planned = _plan(_set(f"period|a1|{FIRST}", "closed_on", ""), before=before)
    assert planned.refusals == [texts.reopen_refused("עורף", date(2026, 5, 1))]


def test_an_open_period_has_no_close_to_reopen():
    assert _plan(_set(f"period|a1|{SECOND}", "closed_on", None)).changes == []


def _closed_twice() -> Account:
    """Both of a1's periods are closed. E1 closes both; W1's last reading closes the first and
    nothing of W1's follows it, so W1 has no opening in the second."""
    before = _account()
    before.readings["W1"] = [
        _reading("2026-05-01", "s1", 1000, opens=FIRST),
        _reading("2026-05-20", "s2", 1040, closes=FIRST),
    ]
    before.readings["E1"] = [
        _reading("2026-05-01", "e0", 400, opens=FIRST),
        _reading("2026-07-01", "e1", 500, opens=SECOND, closes=FIRST),
        _reading("2026-08-15", "e2", 600, closes=SECOND),
    ]
    before.periods["a1"][1]["closed_on"] = "2026-08-15"
    return before


def test_a_reopen_opens_the_period_on_a_live_meters_latest_reading_when_it_has_none():
    planned = _plan(_set(f"period|a1|{SECOND}", "closed_on", None), before=_closed_twice())
    assert planned.refusals == []
    assert _flags(planned.after, "W1")[1] == ("2026-05-20", SECOND, FIRST)
    opened = texts.opening_moved("water", "W1", 1040, date(2026, 5, 20), date(2026, 7, 1))
    assert opened in planned.warnings
    [period] = [p for p in planned.after.periods["a1"] if p["start"] == SECOND]
    assert consumption(planned.after.readings["W1"], period).units == 0


def test_a_reopen_leaves_a_retired_meter_without_an_opening():
    before = _closed_twice()
    records(before, "meter")["W1"]["retired_at"] = "2026-06-01"
    planned = _plan(_set(f"period|a1|{SECOND}", "closed_on", None), before=before)
    assert planned.refusals == []
    assert _flags(planned.after, "W1")[1] == ("2026-05-20", None, FIRST)


def test_a_reopen_across_a_vacancy_gap_opens_nothing_on_the_reading_before_the_gap():
    """The first period closed on 2026-05-20 as a vacate; the next starts on 2026-07-10, so the
    units between are nobody's, as a rebuild would leave them too."""
    gap = "2026-07-10"
    before = _account()
    before.readings["W1"] = [
        _reading("2026-05-01", "s1", 1000, opens=FIRST),
        _reading("2026-05-20", "s2", 1040, closes=FIRST),
    ]
    before.readings["E1"] = [
        _reading("2026-05-01", "e0", 400, opens=FIRST),
        _reading("2026-05-20", "e1", 500, closes=FIRST),
        _reading(gap, "e2", 510, opens=gap),
        _reading("2026-08-15", "e3", 600, closes=gap),
    ]
    before.periods["a1"] = [_period(FIRST, "2026-05-20"), _period(gap, "2026-08-15")]
    planned = _plan(_set(f"period|a1|{gap}", "closed_on", None), before=before)
    assert planned.refusals == []
    assert _flags(planned.after, "W1") == [("2026-05-01", FIRST, None), ("2026-05-20", None, FIRST)]
    assert planned.warnings == [texts.period_reopened("עורף", date(2026, 7, 10))]


@pytest.mark.parametrize("reverse", [False, True])
def test_a_reassign_and_a_reopen_in_one_message_open_the_moved_meter_either_way(reverse):
    before = _closed_twice()
    records(before, "meter")["W1"]["retired_at"] = "2026-06-01"
    before.meters.append(_meter("W2", "a2"))
    before.readings["W2"] = [
        _reading("2026-05-01", "w1", 100, opens=FIRST),
        _reading("2026-05-20", "w2", 150),
    ]
    before.periods["a2"] = [_period(FIRST)]
    operations = [
        _set("meter|W2", "apartment", "a1"),
        _set(f"period|a1|{SECOND}", "closed_on", None),
    ]
    planned = _plan(*(reversed(operations) if reverse else operations), before=before)
    assert planned.refusals == []
    assert _flags(planned.after, "W2") == [
        ("2026-05-01", FIRST, None),
        ("2026-05-20", SECOND, FIRST),
    ]


def test_a_date_for_closed_on_is_refused_pointing_at_the_closing_reading():
    planned = _plan(_set(f"period|a1|{FIRST}", "closed_on", "2026-06-15"))
    assert planned.refusals == [texts.closed_on_follows_readings("עורף", date(2026, 5, 1))]


def test_the_closed_on_refusal_names_the_reopen():
    assert "לפתוח מחדש" in texts.closed_on_follows_readings("עורף", date(2026, 5, 1))


def test_a_future_closed_on_is_an_invalid_value():
    with pytest.raises(InvalidValue):
        _plan(_set(f"period|a1|{FIRST}", "closed_on", "2027-01-01"))


def test_undoing_a_vacate_opens_the_next_period_on_the_closing_readings():
    planned = _plan(_set(f"period|a1|{FIRST}", "vacant", "false"), before=_vacated())
    assert planned.refusals == []
    closed, opened = planned.after.periods["a1"]
    assert closed["closed_on"] == "2026-07-01"
    assert (opened["start"], opened["months"], opened["closed_on"]) == ("2026-07-02", 2, None)
    assert _flags(planned.after, "W1")[-1] == ("2026-07-01", "2026-07-02", FIRST)
    assert _flags(planned.after, "E1")[-1] == ("2026-07-01", "2026-07-02", FIRST)
    [change] = planned.changes
    assert (change.old, change.new) == ("כן", "לא")


def test_adding_a_vacate_removes_the_next_period():
    before = _account()
    del before.readings["W1"][3]
    before.readings["E1"] = [
        _reading("2026-05-01", "e0", 400, opens=FIRST),
        _reading("2026-07-01", "e1", 500, opens=SECOND, closes=FIRST),
    ]
    planned = _plan(_set(f"period|a1|{FIRST}", "vacant", "true"), before=before)
    assert planned.refusals == []
    assert [p["start"] for p in planned.after.periods["a1"]] == [FIRST]
    assert _flags(planned.after, "W1")[-1] == ("2026-07-01", None, FIRST)
    assert _flags(planned.after, "E1")[-1] == ("2026-07-01", None, FIRST)
    assert texts.period_removed("עורף", date(2026, 7, 1)) in planned.warnings


def test_adding_a_vacate_is_refused_while_the_next_period_holds_readings():
    planned = _plan(_set(f"period|a1|{FIRST}", "vacant", "true"))
    assert planned.refusals == [texts.vacate_next_holds_readings("עורף", date(2026, 7, 1))]


def test_adding_a_vacate_is_refused_before_a_closed_period():
    before = _account()
    before.periods["a1"][1]["closed_on"] = "2026-08-15"
    before.readings["W1"][-1]["closes_period"] = SECOND
    planned = _plan(_set(f"period|a1|{FIRST}", "vacant", "true"), before=before)
    assert planned.refusals == [texts.vacate_next_closed("עורף", date(2026, 7, 1))]


def test_vacating_an_open_period_is_refused_pointing_at_the_close():
    planned = _plan(_set(f"period|a1|{SECOND}", "vacant", "true"))
    assert planned.refusals == [texts.vacate_open_period("עורף")]


@pytest.mark.parametrize(
    ("period", "value"),
    [(FIRST, "false"), (SECOND, "false")],
)
def test_a_period_with_a_next_one_or_still_open_is_not_vacant(period, value):
    assert _plan(_set(f"period|a1|{period}", "vacant", value)).changes == []


def test_deleting_a_tariff_names_the_periods_whose_charge_changes():
    before = _account()
    before.tariffs.append({**before.tariffs[0], "effective_date": "2026-06-01", "water_per_m3": 20})
    planned = _plan(_delete("tariff|2026-06-01"), before=before)
    assert planned.refusals == []
    assert [t["effective_date"] for t in planned.after.tariffs] == ["2026-01-01"]
    [deleted] = planned.changes
    assert deleted.line == texts.tariff_removed(texts.tariff_label(date(2026, 6, 1)))
    changed = {(f.serial, f.start): (f.before.amount, f.after.amount) for f in planned.figures}
    assert changed[("W1", FIRST)] == (2000, 1000)


def test_a_meter_retired_by_hand_keeps_its_date():
    planned = _plan(_set("meter|W1", "retired_at", "2026-08-20"))
    assert planned.refusals == []
    assert records(planned.after, "meter")["W1"]["retired_at"] == "2026-08-20"
    [change] = planned.changes
    assert (change.old, change.new) == (texts.EMPTY_VALUE, "20/08/2026")


def test_clearing_retired_at_brings_the_meter_back():
    old = _meter("W0", "a2", retired_at="2026-07-01")
    before = _with_meter(old, [_reading("2026-05-01", "w0", 5)])
    planned = _plan(_set("meter|W0", "retired_at", None), before=before)
    assert planned.refusals == []
    assert records(planned.after, "meter")["W0"]["retired_at"] is None
    assert texts.replacement_undone("water", "W0") in planned.warnings


def test_clearing_retired_at_is_refused_while_a_live_meter_of_the_type_stands():
    old = _meter("W0", "a1", retired_at="2026-07-01")
    before = _with_meter(old, [_reading("2026-05-01", "w0", 5)])
    planned = _plan(_set("meter|W0", "retired_at", None), before=before)
    assert planned.refusals == [texts.live_meter_exists("water", "עורף")]


def test_a_replacement_undone_with_the_new_meter_retired_in_one_message():
    old = _meter("W0", "a1", retired_at="2026-07-01")
    before = _with_meter(old, [_reading("2026-05-01", "w0", 5)])
    planned = _plan(
        _set("meter|W0", "retired_at", None),
        _set("meter|W1", "retired_at", "2026-08-20"),
        before=before,
    )
    assert planned.refusals == []


def test_deletes_run_after_plain_sets_and_before_key_changes():
    planned = _plan(
        _set("meter|W1", "serial", "W2"),
        _delete(f"reading|W1|{LATE}"),
        _set(f"reading|W1|{LAST}", "value", "1160"),
    )
    assert planned.refusals == []
    assert [r["value"] for r in planned.after.readings["W2"]] == [1000, 1100, 1160]


def test_a_reassign_refuses_readings_after_the_targets_vacate():
    second = "2026-03-02"
    before = _account()
    before.meters = [_meter("W", "a1"), _meter("E", "a2", "electricity")]
    before.readings = {
        "W": [
            _reading("2026-01-01", "w1", 100, opens="2026-01-01"),
            _reading("2026-03-01", "w2", 200, opens=second, closes="2026-01-01"),
            _reading("2026-04-01", "w3", 260),
            _reading("2026-05-01", "w4", 300),
        ],
        "E": [
            _reading("2026-01-01", "e1", 10, opens="2026-01-01"),
            _reading("2026-02-01", "e2", 20, closes="2026-01-01"),
        ],
    }
    before.periods["a1"] = [_period("2026-01-01", "2026-03-01"), _period(second)]
    before.periods["a2"] = [_period("2026-01-01", "2026-02-01")]
    planned = _plan(_set("meter|W", "apartment", "a2"), _delete("apartment|a1"), before=before)
    days = [date(2026, 3, 1), date(2026, 4, 1), date(2026, 5, 1)]
    assert planned.refusals == [texts.reassign_uncovered("קדמית", days)]


def test_the_uncovered_refusal_says_what_to_do_for_both_ends():
    shown = texts.reassign_uncovered("קדמית", [date(2026, 3, 1)])
    assert "להקדים" in shown and "הפינוי" in shown


def test_undoing_a_vacate_is_refused_on_a_row_that_opens_and_closes_the_period():
    before = _vacated()
    del before.readings["W1"][:2]
    before.readings["W1"][0]["opens_period"] = FIRST
    planned = _plan(_set(f"period|a1|{FIRST}", "vacant", "false"), before=before)
    assert planned.refusals == [texts.closing_row_opens_its_period("עורף", date(2026, 5, 1))]


def test_a_closing_chosen_and_the_old_shared_row_deleted_in_one_message_is_refused():
    second, third = "2026-03-02", "2026-05-02"
    before = _account()
    before.meters = [_meter("W", "a1")]
    before.readings = {
        "W": [
            _reading("2026-01-01", "r1", 100, opens="2026-01-01"),
            _reading("2026-01-15", "r2", 120),
            _reading("2026-03-01", "r3", 200, opens=second, closes="2026-01-01"),
            _reading("2026-04-01", "r4", 250),
            _reading("2026-05-01", "r5", 300, opens=third, closes=second),
            _reading("2026-05-20", "r6", 320),
        ]
    }
    before.periods["a1"] = [
        _period("2026-01-01", "2026-03-01"),
        _period(second, "2026-05-01"),
        _period(third),
    ]
    planned = _plan(
        _set(f"period|a1|{second}", "closing", "W|2026-01-15#r2"),
        _delete("reading|W|2026-03-01#r3"),
        before=before,
    )
    assert planned.refusals == [texts.closing_before_opening("W", date(2026, 3, 2))]


def test_deleting_a_reset_reading_passes_the_reset_to_the_next_reading():
    before = _account()
    before.readings["W1"][1].update(value=5, resets=True)
    before.readings["W1"].insert(2, _reading("2026-06-01", "s25", 9))
    before.readings["W1"][3]["value"] = 70
    before.readings["W1"][4]["value"] = 120
    planned = _plan(_delete(f"reading|W1|{LATE}"), before=before)
    readings = planned.after.readings["W1"]
    assert readings[1]["resets"] is True
    assert consumption(readings, planned.after.periods["a1"][0]).units == 61


def test_a_reset_reading_moved_away_leaves_the_next_reading_as_it_was():
    before = _with_meter(_meter("W2", "a2"), [])
    before.readings["W1"][1]["resets"] = True
    planned = _plan(_set(f"reading|W1|{LATE}", "serial", "W2"), before=before)
    assert [r["resets"] for r in planned.after.readings["W1"]] == [False, False, False]


def _two_meters_closing_three_periods() -> Account:
    days = ["2026-01-01", "2026-03-01", "2026-05-01", "2026-07-01"]
    before = _account()
    before.readings = {
        serial: [
            _reading(days[0], f"{serial}0", base, opens=days[0]),
            _reading(days[1], f"{serial}1", base + 100, opens=days[1], closes=days[0]),
            _reading(days[2], f"{serial}2", base + 200, opens=days[2], closes=days[1]),
            _reading(days[3], f"{serial}3", base + 300, opens=days[3], closes=days[2]),
            _reading("2026-08-01", f"{serial}4", base + 350),
        ]
        for serial, base in (("W1", 1000), ("E1", 500))
    }
    before.periods["a1"] = [
        _period(days[0], days[1]),
        _period(days[1], days[2]),
        _period(days[2], days[3]),
        _period(days[3]),
    ]
    return before


def test_deleting_a_meter_leaves_the_closes_another_meter_shares():
    before = _two_meters_closing_three_periods()
    planned = _plan(_delete("meter|E1"), before=before)
    assert planned.refusals == []
    assert [(p["start"], p["closed_on"]) for p in planned.after.periods["a1"]] == [
        (p["start"], p["closed_on"]) for p in before.periods["a1"]
    ]
    readings = planned.after.readings["W1"]
    assert readings == before.readings["W1"]
    assert [consumption(readings, p).units for p in planned.after.periods["a1"]] == [
        100,
        100,
        100,
        50,
    ]
    assert not any(f.serial == "W1" for f in planned.figures)
    assert texts.period_reopened("עורף", date(2026, 1, 1)) not in planned.warnings


def test_deleting_a_meter_realigns_a_close_it_was_the_latest_of():
    before = _two_meters_closing_three_periods()
    before.readings["W1"][1]["date"] = "2026-02-20"
    planned = _plan(_delete("meter|E1"), before=before)
    assert planned.refusals == []
    closed, following = planned.after.periods["a1"][:2]
    assert closed["closed_on"] == "2026-02-20"
    assert following["start"] == "2026-02-21"


def test_deleting_a_meter_reopens_the_periods_no_other_meter_closes():
    before = _account()
    planned = _plan(_delete("meter|W1"), before=before)
    [period] = planned.after.periods["a1"]
    assert (period["start"], period["closed_on"]) == (FIRST, None)
    assert texts.period_reopened("עורף", date(2026, 5, 1)) in planned.warnings


def test_deleting_a_meter_with_no_readings_removes_its_row_and_undoes_its_link():
    old = _meter("E0", "a1", "electricity", retired_at="2026-07-01")
    before = _with_meter(old, [_reading("2026-05-01", "e0", 400, opens=FIRST)])
    before.meters.append({**_meter("E9", "a1", "electricity"), "replaces": "E0"})
    before.readings["E9"] = []
    records(before, "meter")["E1"]["retired_at"] = "2026-07-02"
    planned = _plan(_delete("meter|E9"), before=before)
    assert planned.refusals == []
    assert "E9" not in records(planned.after, "meter")
    assert "E9" not in planned.after.readings
    assert records(planned.after, "meter")["E0"]["retired_at"] is None


@pytest.mark.parametrize(
    "targets",
    [
        ("reading|N|2026-08-20#n1", "meter|N"),
        ("meter|N", "apartment|a4"),
        ("meter|N", "apartment|a4", "property|p3"),
        ("reading|N|2026-08-20#n1", "meter|N", "apartment|a4", "property|p3"),
    ],
)
def test_deletes_that_reach_the_same_record_read_alike_in_any_order(targets):
    plans = [
        _plan(*[_delete(target) for target in order], before=_linked_alone())
        for order in (targets, targets[::-1])
    ]
    for planned in plans:
        assert planned.refusals == []
        assert "N" not in records(planned.after, "meter")
        assert "a4" not in records(planned.after, "apartment")
        assert "p3" not in records(planned.after, "property")
        assert len(planned.changes) == len(targets)
    assert plans[0].after == plans[1].after
    assert {c.line for c in plans[0].changes} == {c.line for c in plans[1].changes}


def test_a_delete_of_a_record_absent_from_the_account_still_raises():
    with pytest.raises(UnknownTarget):
        _plan(_delete("meter|E1"), _delete("meter|nope"))


def test_the_starts_map_keeps_each_row_alive_so_its_id_stays_its_own():
    before = _account()
    after = before.copy()
    row = after.periods["a1"][0]
    assert _starts_of_copy(before, after)[id(row)] == (row, FIRST)


def test_a_period_moved_and_reopened_reads_as_reopened_not_deleted():
    planned = _plan(
        _set(f"period|a1|{FIRST}", "closed_on", None),
        _set(f"period|a1|{FIRST}", "start", "2026-04-20"),
    )
    assert planned.refusals == []
    assert texts.period_reopened("עורף", date(2026, 5, 1)) in planned.warnings
    assert texts.period_removed("עורף", date(2026, 5, 1)) not in planned.warnings
    assert texts.period_removed("עורף", date(2026, 7, 1)) in planned.warnings


def test_a_merge_of_a_meter_the_message_moves_away_is_refused():
    retired = _meter("W2", "a1", retired_at="2026-05-02")
    before = _with_meter(retired, [_reading("2026-05-01", "x", 5, opens=FIRST)])
    planned = _plan(
        _set("meter|W2", "apartment", "a2"), _set("meter|W2", "serial", "W1"), before=before
    )
    assert planned.refusals == [texts.serial_taken("W1")]


def test_a_merge_of_a_meter_the_message_moves_in_is_judged_where_it_lands():
    retired = _meter("W2", "a2", retired_at="2026-05-02")
    before = _with_meter(retired, [_reading("2026-05-02", "x", 1001)])
    planned = _plan(
        _set("meter|W2", "apartment", "a1"), _set("meter|W2", "serial", "W1"), before=before
    )
    assert planned.refusals == []
    assert "W2" not in records(planned.after, "meter")


def test_a_merge_that_leaves_an_opening_with_no_closing_before_the_next_is_refused():
    retired = _meter("W0", "a1", retired_at="2026-05-21")
    before = _with_meter(
        retired,
        [_reading("2026-05-01", "w0", 5, opens=FIRST), _reading("2026-05-20", "w1", 9)],
    )
    before.readings["W1"] = before.readings["W1"][2:]
    before.readings["W1"][0]["closes_period"] = None
    planned = _plan(_set("meter|W0", "serial", "W1"), before=before)
    assert planned.refusals == [texts.opening_without_closing("W1", date(2026, 5, 1))]


def test_a_row_that_opens_and_closes_its_own_period_leaves_no_opening_running():
    before = _account()
    before.readings["W1"] = [
        _reading("2026-05-01", "a", 1, opens=FIRST, closes=FIRST),
        _reading("2026-07-01", "b", 2, opens=SECOND),
        _reading("2026-08-01", "c", 3),
    ]
    assert _plan(_set("reading|W1|2026-08-01#c", "value", "4"), before=before).refusals == []


def test_a_rename_leaves_an_unclosed_opening_already_on_record():
    before = _account()
    before.readings["W1"][2]["closes_period"] = None
    assert _plan(_set("meter|W1", "serial", "W2"), before=before).refusals == []


def _checked(before: Account, change) -> list[str]:
    """The refusals of a result built by hand, as no rewrite would reach it, so the guard is judged
    with no rule's help."""
    after = before.copy()
    starts = _starts_of_copy(before, after)
    change(after)
    return check(before, after, starts)[0]


def test_the_guard_refuses_a_reading_that_stops_counting():
    def strand(state):
        state.readings["W1"][-1]["opens_period"] = None
        state.readings["W1"][2]["opens_period"] = None
        del state.periods["a1"][1]
        state.readings["E1"][0].update(opens_period=FIRST, closes_period=FIRST)

    refusals = _checked(_account(), strand)
    assert refusals == [texts.reading_stops_counting("W1", date(2026, 8, 15), "עורף")]


def test_the_guard_follows_a_row_across_a_rename():
    def rename(state):
        state.readings["W9"] = state.readings.pop("W1")
        records(state, "meter")["W1"]["serial"] = "W9"
        records(state, "meter")["W9"]["former_serials"] = ["W1"]

    assert _checked(_account(), rename) == []


def test_the_guard_follows_a_row_across_a_merge():
    def merge(state):
        rows = state.readings.pop("W2")
        state.meters.remove(records(state, "meter")["W2"])
        state.readings["W1"].extend(rows)
        state.readings["W1"].sort(key=lambda r: r["date"])

    before = _with_meter(_meter("W2", "a1"), [_reading("2026-05-10", "x", 1010)])
    before.readings["W2"][0]["opens_period"] = FIRST
    assert _checked(before, merge) == []


def test_the_guard_allows_a_deleted_reading():
    assert _checked(_account(), lambda state: state.readings["W1"].pop()) == []


def test_the_guard_counts_a_reading_in_another_apartment_when_a_period_there_does():
    def move(state):
        rows = state.readings["W1"]
        records(state, "meter")["W1"]["apartment_id"] = "a2"
        state.periods["a2"] = [_period(FIRST)]
        for r in rows:
            r.update(opens_period=None, closes_period=None)
        rows[0]["opens_period"] = FIRST

    assert _checked(_account(), move) == []


def test_the_guard_counts_a_lone_reading_carried_into_the_next_period():
    before = _account()
    before.readings["W1"] = [
        _reading("2026-06-01", "lone", 1000, opens=FIRST),
        _reading("2026-08-01", "next", 1100),
    ]

    def carry(state):
        state.readings["W1"][0].update(opens_period=SECOND)

    assert _checked(before, carry) == []


def test_the_guard_refuses_a_live_meter_left_unclosed_in_a_closed_period():
    before = _with_meter(
        _meter("O", "a1", retired_at="2026-05-25"),
        [_reading("2026-05-01", "o1", 100, opens=FIRST), _reading("2026-05-20", "o2", 150)],
    )

    records(before, "meter")["W1"]["retired_at"] = "2026-05-25"

    def restore(state):
        records(state, "meter")["O"]["retired_at"] = None

    assert _checked(before, restore) == [texts.meter_left_unclosed("O", date(2026, 5, 1))]


def test_the_guard_leaves_a_retired_meter_unclosed():
    before = _with_meter(
        _meter("O", "a1", retired_at="2026-05-25"),
        [_reading("2026-05-01", "o1", 100, opens=FIRST), _reading("2026-05-20", "o2", 150)],
    )
    assert _checked(before, lambda state: None) == []


def test_the_guard_leaves_a_fault_already_on_record():
    before = _account()
    before.readings["W1"][2]["closes_period"] = None
    assert _checked(before, lambda state: state.readings["E1"][0].update(value=501)) == []


def test_the_guard_refusals_name_the_reopen():
    assert "לפתוח מחדש" in texts.reading_stops_counting("W1", date(2026, 8, 15), "עורף")
    assert "לפתוח מחדש" in texts.meter_left_unclosed("W1", date(2026, 5, 1))


def test_the_guard_refuses_a_flag_naming_a_period_the_apartment_no_longer_has():
    def dangle(state):
        state.readings["W1"][3]["closes_period"] = "2026-09-01"

    refusals = _checked(_account(), dangle)
    bound = texts.reading_names_missing_period("W1", date(2026, 8, 15), date(2026, 9, 1), "עורף")
    assert refusals == [bound]
    assert "הודעה" in bound


def test_a_closing_chosen_on_the_opening_row_and_the_source_emptied_is_refused_by_the_guard():
    """The chosen row loses its opening, and the move empties the only other opener of the
    period, so the period goes while the row still closes it."""
    before = _account()
    before.meters = [
        _meter("O", "a1", retired_at="2026-01-24"),
        {**_meter("N", "a1"), "replaces": "O"},
    ]
    before.readings = {
        "O": [_reading("2026-01-22", "o1", 53, opens="2026-01-01")],
        "N": [
            _reading("2026-01-01", "n1", 34, opens="2026-01-01"),
            _reading("2026-01-24", "n2", 2),
            _reading("2026-02-19", "n3", 53, opens="2026-02-20", closes="2026-01-01"),
            _reading("2026-03-16", "n4", 85),
            _reading("2026-03-31", "n5", 93, closes="2026-02-20"),
        ],
    }
    before.periods["a1"] = [
        _period("2026-01-01", "2026-02-19"),
        _period("2026-02-20", "2026-03-31"),
    ]
    planned = _plan(
        _set("period|a1|2026-01-01", "closing", "N|2026-01-01#n1"),
        _set("reading|O|2026-01-22#o1", "serial", "N"),
        before=before,
    )
    assert planned.refusals == [
        texts.reading_names_missing_period("N", date(2026, 1, 1), date(2026, 1, 1), "עורף")
    ]


def test_a_retired_at_that_changes_to_a_date_leaves_the_flags():
    planned = _plan(_set("meter|W1", "retired_at", "2026-08-20"))
    assert _flags(planned.after, "W1") == _flags(_account(), "W1")


def _vacated_target() -> Account:
    """Retired W1 closes a1's only period, a vacate; W2 of a2 reads on after that close."""
    before = _account()
    before.meters = [_meter("W1", "a1", retired_at="2026-02-02"), _meter("W2", "a2")]
    before.readings = {
        "W1": [
            _reading("2026-01-01", "w1", 100, opens="2026-01-01"),
            _reading("2026-02-01", "w2", 200, closes="2026-01-01"),
        ],
        "W2": [
            _reading("2026-01-05", "x1", 50, opens="2026-01-01"),
            _reading("2026-01-20", "x2", 70),
            _reading("2026-03-01", "x3", 90),
            _reading("2026-04-01", "x4", 130),
        ],
    }
    before.periods["a1"] = [_period("2026-01-01", "2026-02-01")]
    before.periods["a2"] = [_period("2026-01-01")]
    return before


def test_a_meter_moved_then_merged_is_judged_on_the_rows_it_brought_after_a_vacate():
    move = _set("meter|W2", "apartment", "a1")
    planned = _plan(
        move, _set("meter|W2", "serial", "W1"), _delete("apartment|a2"), before=_vacated_target()
    )
    days = [date(2026, 3, 1), date(2026, 4, 1)]
    assert planned.refusals == [texts.reassign_uncovered("עורף", days)]


def test_a_meter_moved_then_merged_is_judged_on_the_rows_it_brought_before_the_periods():
    before = _account()
    before.meters = [_meter("W1", "a1"), _meter("W2", "a2", retired_at="2026-02-02")]
    before.readings = {
        "W1": [
            _reading("2026-03-01", "w1", 100, opens="2026-03-01"),
            _reading("2026-04-01", "w2", 200),
        ],
        "W2": [
            _reading("2026-01-01", "x1", 50, opens="2026-01-01"),
            _reading("2026-02-01", "x2", 70),
        ],
    }
    before.periods["a1"] = [_period("2026-03-01")]
    before.periods["a2"] = [_period("2026-01-01")]
    planned = _plan(
        _set("meter|W2", "apartment", "a1"), _set("meter|W2", "serial", "W1"), before=before
    )
    days = [date(2026, 1, 1), date(2026, 2, 1)]
    assert planned.refusals == [texts.reassign_uncovered("עורף", days)]


def test_a_merge_within_an_apartment_does_not_judge_the_kept_meters_rows():
    before = _vacated_target()
    records(before, "meter")["W2"]["apartment_id"] = "a1"
    before.readings["W2"] = [_reading("2026-01-20", "x2", 150)]
    before.periods["a2"] = []
    assert _plan(_set("meter|W2", "serial", "W1"), before=before).refusals == []


def test_a_start_change_on_the_target_does_not_pass_for_a_copy():
    planned = _plan(
        _set("meter|W2", "apartment", "a1"),
        _set("period|a1|2026-01-01", "start", "2026-01-02"),
        before=_vacated_target(),
    )
    days = [date(2026, 3, 1), date(2026, 4, 1)]
    assert planned.refusals == [texts.reassign_uncovered("עורף", days)]


def _replaced_in_the_closed_period() -> Account:
    """Retired O and its replacement N of a1, whose closed period N closes."""
    before = _account()
    before.meters = [_meter("O", "a1", retired_at="2026-05-25"), _meter("N", "a1")]
    before.readings = {
        "O": [
            _reading("2026-05-01", "o1", 100, opens=FIRST),
            _reading("2026-05-20", "o2", 150),
        ],
        "N": [
            _reading("2026-05-25", "n1", 1, opens=FIRST),
            _reading("2026-06-10", "n2", 20),
            _reading("2026-07-01", "n3", 40, opens=SECOND, closes=FIRST),
            _reading("2026-08-15", "n4", 60),
        ],
    }
    records(before, "meter")["N"]["replaces"] = "O"
    return before


def test_a_reading_moved_from_before_the_open_period_opens_nothing():
    planned = _plan(
        _set("reading|N|2026-06-10#n2", "serial", "O"), before=_replaced_in_the_closed_period()
    )
    assert planned.refusals == []
    assert _flags(planned.after, "O")[-1] == ("2026-06-10", None, None)


def test_a_reading_moved_from_inside_the_open_period_still_opens_it():
    before = _replaced_in_the_closed_period()
    records(before, "meter")["O"]["retired_at"] = None
    records(before, "meter")["N"]["retired_at"] = "2026-08-16"
    before.readings["O"].append(_reading("2026-06-30", "o3", 170, closes=FIRST))
    planned = _plan(_set("reading|N|2026-08-15#n4", "serial", "O"), before=before)
    assert planned.refusals == []
    assert _flags(planned.after, "O")[-1] == ("2026-08-15", SECOND, None)


def test_an_opening_with_no_closing_is_refused_naming_the_reopen():
    shown = texts.opening_without_closing("W1", date(2026, 5, 1))
    assert "לפתוח מחדש" in shown
    assert "קריאת הפתיחה של התקופה הבאה" not in shown


def _swap_of_closers() -> Account:
    """E1 alone closes a2's last period on 02-06, W0's reading of 02-05 would close it instead."""
    before = _account()
    before.meters = [
        _meter("W0", "a1"),
        _meter("E0", "a1", "electricity"),
        _meter("E1", "a2", "electricity", retired_at="2026-06-01"),
    ]
    before.readings = {
        "W0": [
            _reading("2026-01-01", "w1", 94, opens="2026-01-01"),
            _reading("2026-01-18", "w2", 134, opens="2026-01-20", closes="2026-01-01"),
            _reading("2026-01-27", "w3", 180),
            _reading("2026-02-05", "w4", 192),
        ],
        "E0": [
            _reading("2026-01-01", "e1", 21, opens="2026-01-01"),
            _reading("2026-01-19", "e2", 54, opens="2026-01-20", closes="2026-01-01"),
            _reading("2026-01-27", "e3", 75),
            _reading("2026-02-07", "e4", 122),
        ],
        "E1": [
            _reading("2026-01-01", "x1", 14, opens="2026-01-01"),
            _reading("2026-01-27", "x2", 73, opens="2026-01-28", closes="2026-01-01"),
            _reading("2026-02-06", "x3", 74, closes="2026-01-28"),
        ],
    }
    before.periods["a1"] = [_period("2026-01-01", "2026-01-19"), _period("2026-01-20")]
    before.periods["a2"] = [
        _period("2026-01-01", "2026-01-27"),
        _period("2026-01-28", "2026-02-06"),
    ]
    return before


@pytest.mark.parametrize("reverse", [False, True])
def test_two_reassigns_leave_a_closed_on_on_the_new_closing_reading_in_either_order(reverse):
    moves = [_set("meter|E1", "apartment", "a1"), _set("meter|W0", "apartment", "a2")]
    planned = _plan(*(moves[::-1] if reverse else moves), before=_swap_of_closers())
    assert planned.refusals == []
    assert planned.after.periods["a2"][1]["closed_on"] == "2026-02-05"


def test_a_moved_reading_before_the_targets_first_opening_takes_the_opening():
    before = _replaced_in_the_closed_period()
    planned = _plan(_set("reading|O|2026-05-20#o2", "serial", "N"), before=before)
    assert planned.refusals == []
    assert _flags(planned.after, "N")[:2] == [
        ("2026-05-20", FIRST, None),
        ("2026-05-25", None, None),
    ]
    period = planned.after.periods["a1"][0]
    assert consumption(planned.after.readings["N"], period).opening["seq"] == "o2"
    assert any(f.serial == "N" and f.start == FIRST for f in planned.figures)
    line = texts.opening_moved("water", "N", 150, date(2026, 5, 20), date(2026, 5, 1))
    assert line in planned.warnings


def _opening_on_a_shared_row() -> Account:
    """Retired T's first reading both closes the period before and opens the one it falls in, and
    W1 holds a reading dated before it, inside that period."""
    before = _with_meter(
        _meter("T", "a1", retired_at="2026-08-02"),
        [
            _reading("2026-07-10", "t1", 5, opens=SECOND, closes=FIRST),
            _reading("2026-08-01", "t2", 9),
        ],
    )
    before.readings["W1"].insert(3, _reading("2026-07-05", "w", 1120))
    return before


def test_a_moved_reading_leaves_an_opening_on_a_shared_row_and_the_guard_refuses():
    planned = _plan(
        _set("reading|W1|2026-07-05#w", "serial", "T"), before=_opening_on_a_shared_row()
    )
    assert planned.refusals == [texts.moved_reading_stops_counting("T", date(2026, 7, 5))]
    assert planned.after.readings["T"][1]["opens_period"] == SECOND


def test_a_moved_reading_of_another_type_does_not_take_the_opening():
    before = _account()
    before.readings["E1"][0]["date"] = "2026-07-10"
    before.readings["W1"].insert(3, _reading("2026-07-05", "w", 1120))
    planned = _plan(_set("reading|W1|2026-07-05#w", "serial", "E1"), before=before)
    assert planned.refusals == [texts.moved_reading_stops_counting("E1", date(2026, 7, 5))]
    assert _flags(planned.after, "E1")[1] == ("2026-07-10", SECOND, None)


def test_a_moved_reading_outside_the_targets_first_period_does_not_take_the_opening():
    before = _replaced_in_the_closed_period()
    before.readings["N"] = before.readings["N"][2:]
    before.readings["N"][0]["closes_period"] = None
    planned = _plan(_set("reading|O|2026-05-20#o2", "serial", "N"), before=before)
    assert _flags(planned.after, "N")[1] == ("2026-07-01", SECOND, None)


def _lone_reading_past_the_last_close() -> Account:
    """Retired O's only reading stands in a1's closed periods, and nothing follows the last one.
    Its replacement N and the live E both close them."""
    before = _account()
    before.meters = [
        _meter("O", "a1", retired_at="2026-01-10"),
        {**_meter("N", "a1"), "replaces": "O"},
        _meter("E", "a1", "electricity"),
    ]
    before.readings = {
        "O": [_reading("2026-01-01", "o", 5, opens="2026-01-01")],
        "N": [
            _reading("2026-01-10", "n1", 1, opens="2026-01-01"),
            _reading("2026-02-01", "n2", 9, opens="2026-02-02", closes="2026-01-01"),
            _reading("2026-03-01", "n3", 20, closes="2026-02-02"),
        ],
        "E": [
            _reading("2026-01-01", "e1", 1, opens="2026-01-01"),
            _reading("2026-02-01", "e2", 9, opens="2026-02-02", closes="2026-01-01"),
            _reading("2026-03-01", "e3", 20, closes="2026-02-02"),
        ],
    }
    before.periods["a1"] = [
        _period("2026-01-01", "2026-02-01"),
        _period("2026-02-02", "2026-03-01"),
    ]
    return before


def test_a_lone_reading_refusal_names_the_period_that_is_the_way_out():
    before = _lone_reading_past_the_last_close()
    planned = _plan(_delete("meter|N"), before=before)
    assert planned.refusals == [texts.reading_stops_counting("O", date(2026, 1, 1), "עורף")]
    assert "התקופה האחרונה" in planned.refusals[0]
    earlier = _plan(_set("period|a1|2026-01-01", "closed_on", None), before=before)
    assert earlier.refusals == [texts.reopen_refused("עורף", date(2026, 1, 1))]
    reopened = _plan(_set("period|a1|2026-02-02", "closed_on", None), before=before)
    assert reopened.refusals == []
    assert _plan(_delete("meter|N"), before=reopened.after).refusals == []


def test_a_moved_reading_refusal_names_the_date_or_the_delete_and_no_reopen():
    shown = texts.moved_reading_stops_counting("T", date(2026, 7, 5))
    assert "התאריך" in shown and "למחוק" in shown
    assert "לפתוח מחדש" not in shown


def test_a_lone_reading_moved_with_its_meter_keeps_the_reopen_text():
    before = _lone_reading_past_the_last_close()
    before.meters.append(_meter("X", "a2", "electricity", retired_at="2026-01-10"))
    before.readings["X"] = [_reading("2026-01-01", "x", 5, opens="2026-01-01")]
    before.periods["a2"] = [_period("2026-01-01")]
    planned = _plan(_set("meter|X", "apartment", "a1"), before=before)
    assert planned.refusals == [texts.reading_stops_counting("X", date(2026, 1, 1), "עורף")]


@pytest.mark.parametrize("target", ["apartment|a3", "property|p2"])
def test_a_delegate_is_refused_deleting_an_apartment_or_a_property(target):
    before = _account()
    planned = _plan(_delete(target), before=before, role=DELEGATE)
    assert planned.refusals == [texts.owner_only()]
    assert planned.changes == []


def test_a_refused_delete_refuses_the_whole_message_of_a_delegate():
    planned = _plan(
        _set(f"reading|W1|{LATE}", "value", "1050"), _delete("apartment|a3"), role=DELEGATE
    )
    assert planned.refusals == [texts.owner_only()]


def test_a_delegate_still_corrects_a_reading_and_deletes_a_tariff():
    planned = _plan(
        _set(f"reading|W1|{LATE}", "value", "1050"), _delete("tariff|2026-01-01"), role=DELEGATE
    )
    assert planned.refusals == []
    assert len(planned.changes) == 2


def _address(old, new) -> Operation:
    return _set(f"delegate|{old}", "address", new)


def test_a_corrected_address_moves_the_delegate_and_names_who_gains_and_loses():
    before = _account()
    planned = _plan(_address(HELPER, " Fresh@X.com "), before=before)
    assert planned.refusals == []
    assert [d["email"] for d in planned.after.delegates] == [FRESH, OTHER]
    [change] = planned.changes
    assert (change.old, change.new) == (HELPER, FRESH)
    assert change.record_label == texts.delegate_label(HELPER)
    assert change.field_label == FIELDS[("delegate", "address")].label
    assert planned.warnings == [texts.access_lost(HELPER), texts.access_gained(FRESH)]
    assert diff(before, planned.after).size() == 2


def test_a_delegate_is_refused_correcting_a_delegate_address():
    planned = _plan(_address(HELPER, FRESH), role=DELEGATE)
    assert planned.refusals == [texts.owner_only()]


def test_an_address_of_another_delegate_of_the_account_is_refused():
    planned = _plan(_address(HELPER, OTHER))
    assert planned.refusals == [texts.delegate_exists(OTHER)]


def test_an_address_that_is_no_address_asks_again_with_the_one_on_record():
    with pytest.raises(InvalidValue) as raised:
        _plan(_address(HELPER, "not an address"))
    assert raised.value.current == HELPER


def test_the_same_address_changes_nothing():
    assert _plan(_address(HELPER, HELPER.upper())).changes == []


def test_an_unknown_delegate_is_refused_naming_the_address():
    planned = _plan(_address("Nobody@x.com", FRESH))
    assert planned.refusals == [texts.unknown_delegate("nobody@x.com")]


def test_a_delegate_target_in_another_case_finds_the_delegate():
    planned = _plan(_address(HELPER.upper(), FRESH))
    assert planned.refusals == []
    assert [d["email"] for d in planned.after.delegates] == [FRESH, OTHER]


def test_deleting_a_delegate_is_refused_saying_what_can_be_done():
    planned = _plan(_delete(f"delegate|{HELPER}"))
    assert planned.refusals == [texts.delegate_not_removable(HELPER)]
    assert planned.after.delegates == _account().delegates


def _with_tariffs(count) -> tuple[Account, list[Operation]]:
    before = _account()
    days = [f"2020-{month:02}-{day:02}" for month in range(1, 13) for day in range(1, 11)]
    tariff = before.tariffs[0]
    before.tariffs = [{**tariff, "effective_date": day} for day in days[:count]]
    return before, [_set(f"tariff|{day}", "water_price", "11") for day in days[:count]]


def test_the_access_delete_and_put_count_towards_the_row_limit():
    before, prices = _with_tariffs(98)
    assert _plan(*prices, _address(HELPER, FRESH), before=before).refusals == []
    before, prices = _with_tariffs(99)
    planned = _plan(*prices, _address(HELPER, FRESH), before=before)
    assert planned.refusals == [texts.correction_too_large(101)]


def test_a_delegate_asking_to_remove_a_delegate_is_told_the_owner_can():
    assert _plan(_delete(f"delegate|{OTHER}"), role=DELEGATE).refusals == [texts.owner_only()]


def test_a_delegate_is_refused_deleting_a_meter_with_more_than_one_reading():
    planned = _plan(_delete("meter|W1"), role=DELEGATE)
    assert planned.refusals == [texts.owner_only()]


def test_a_delegate_is_refused_deleting_each_reading_of_a_meter_in_one_message():
    before = _account()
    ids = [f"reading|E1|{r['date']}#{r['seq']}" for r in before.readings["E1"]]
    before.readings["E1"].append(_reading("2026-08-01", "e2", 600))
    ids.append("reading|E1|2026-08-01#e2")
    planned = _plan(*[_delete(i) for i in ids], before=before, role=DELEGATE)
    assert planned.refusals == [texts.owner_only()]


def test_a_delegate_still_undoes_a_link_with_its_single_reading():
    planned = _plan(_delete("reading|N|2026-08-20#n1"), before=_linked_alone(), role=DELEGATE)
    assert planned.refusals == []
    assert "a4" not in records(planned.after, "apartment")


def test_a_delegate_is_refused_when_a_place_holds_two_single_reading_meters():
    before = _linked_alone()
    before.meters.append({**_meter("M", "a4"), "type": "electricity"})
    before.readings["M"] = [_reading("2026-08-21", "m1", 9, opens="2026-08-20")]
    planned = _plan(
        _delete("reading|N|2026-08-20#n1"),
        _delete("reading|M|2026-08-21#m1"),
        before=before,
        role=DELEGATE,
    )
    assert planned.refusals == [texts.owner_only()]


def test_a_delegate_renaming_a_meter_removes_nothing():
    assert _plan(_set("meter|W1", "serial", "W9"), role=DELEGATE).refusals == []


def test_the_owner_deletes_a_meter_with_its_readings():
    assert _plan(_delete("meter|W1")).refusals != [texts.owner_only()]
