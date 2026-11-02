"""Edits on a waiting card: every flow's form takes an edit on any of its fields at any step and
shows the card again, a bare input fills the card's default field, an edit the card cannot take
leaves it as it was, and a choice made on a card can be taken back on it."""

from datetime import date

import pytest
from engine_kit import (
    ACCOUNT,
    USER,
    Kit,
    candidate,
    edit,
    edits,
    electricity,
    options,
    reading,
    water,
)

from common import texts
from common.fields import FIELDS
from common.flows import card_text
from common.model import IntentResult, MeterReadingResult, Operation

SERIAL = "W-1250"
OPENED = "2026-10-01"


def _meter(kit, readings, **period):
    apartment = kit.apartment(**{"start": OPENED, **period})
    kit.meter(apartment, SERIAL, "water", readings)
    return apartment


def _help(card) -> str:
    return texts.card_help([o["label"] for o in card["options"]])


def _say(kit, intent):
    kit.model.queue(intent)
    return kit.say("הודעה")


def _on_property(kit):
    kit.send_photo(water())


def _on_apartment(kit):
    _on_property(kit)
    _say(kit, edit("property", "הגפן 7"))


def _on_period(kit):
    _on_apartment(kit)
    _say(kit, edit("apartment", "עורף"))


def _on_link(kit):
    _on_period(kit)
    kit.choose("start_today")


def _on_unreadable(kit):
    _meter(kit, [reading(OPENED, 1000, opens=OPENED)])
    kit.send_photo(water(None, unreadable=True))


def _on_reading(kit):
    _meter(kit, [reading(OPENED, 1000, opens=OPENED)])
    kit.send_photo(water())


def _on_duplicate(kit):
    _meter(kit, [reading(OPENED, 1000, opens=OPENED), reading("2026-10-29", 1100)])
    kit.send_photo(water())
    kit.choose("confirm")


def _on_lower_reading(kit):
    _meter(kit, [reading(OPENED, 1300, opens=OPENED)])
    kit.send_photo(water())
    kit.choose("confirm")


def _closed_apartment(kit, closed_on, next_start):
    apartment = kit.apartment(start="2026-08-01", closed_on=closed_on)
    kit.period(apartment, next_start)
    kit.meter(
        apartment,
        SERIAL,
        "water",
        [
            reading("2026-08-01", 1000, opens="2026-08-01"),
            reading(closed_on, 1300, opens=next_start, closes="2026-08-01"),
        ],
    )


def _on_swap(kit):
    _closed_apartment(kit, "2026-10-05", "2026-10-06")
    kit.send_photo(water(1250))
    _say(kit, edit("date", "2026-10-01"))
    kit.choose("confirm")


def _on_propose_reclose(kit):
    _closed_apartment(kit, "2026-10-25", "2026-10-26")
    kit.send_photo(water(1350))
    kit.choose("confirm")


def _on_open_period(kit):
    apartment = kit.apartment(start="2026-08-01", closed_on="2026-10-05")
    kit.meter(apartment, SERIAL, "water", [reading("2026-08-01", 1000, opens="2026-08-01")])
    kit.send_photo(water())
    kit.choose("confirm")


def _on_propose_close(kit):
    _meter(kit, [reading("2026-09-01", 1000, opens="2026-09-01")], start="2026-09-01")
    kit.send_photo(water())
    kit.choose("confirm")


def _on_change(kit):
    kit.apartment(closed_on="2026-08-31")
    _say(kit, IntentResult(intent="change_period", name="עורף", months=3))


SETUPS = {
    "property": _on_property,
    "apartment": _on_apartment,
    "period": _on_period,
    "link": _on_link,
    "unreadable": _on_unreadable,
    "reading": _on_reading,
    "duplicate": _on_duplicate,
    "lower_reading": _on_lower_reading,
    "swap": _on_swap,
    "propose_reclose": _on_propose_reclose,
    "open_period": _on_open_period,
    "propose_close": _on_propose_close,
    "change_period": _on_change,
}
CHECK_STEPS = (
    "duplicate",
    "lower_reading",
    "swap",
    "propose_reclose",
    "open_period",
    "propose_close",
)
# The card each step shows after an edit of field; a step left out of a field's entry stays.
LINK_MOVES = {
    "property": {"property": "apartment", "period": "period", "link": "period"},
    "apartment": {"apartment": "period", "period": "period", "link": "period"},
    "period_start": {"period": "link"},
    # The period question asks for a date, so a date named there answers it.
    "date": {"period": "link"},
}
LINK_EDITS = [
    ("value", "1300", 1300),
    ("date", "2026-10-20", "2026-10-20"),
    ("serial", "W-9", "W-9"),
    ("meter_type", "electricity", "electricity"),
    ("property", "לאונר 18", "לאונר 18"),
    ("apartment", "קדמית", "קדמית"),
    ("period_start", "2026-11-01", "2026-11-01"),
    ("months", "3", 3),
]
READING_EDITS = [("value", "1300", 1300), ("date", "2026-10-20", "2026-10-20")]


def _start(step):
    kit = Kit()
    SETUPS[step](kit)
    assert kit.card()["kind"] == step
    return kit


def _link_cases():
    for step in ("property", "apartment", "period", "link"):
        for field, raw, expected in LINK_EDITS:
            yield step, field, raw, expected, LINK_MOVES.get(field, {}).get(step, step)


@pytest.mark.parametrize("step, field, raw, expected, shown", list(_link_cases()))
def test_edit_on_any_step_of_the_link_flow(env, ddb, step, field, raw, expected, shown):
    kit = _start(step)
    version = kit.card()["version"]
    card = _say(kit, edit(field, raw)).snapshot.card
    assert (card["kind"], kit.form()[field]) == (shown, expected)
    assert card["version"] > version


def _reading_cases():
    for step in ("unreadable", "reading", *CHECK_STEPS):
        for case in READING_EDITS:
            yield (step, *case)


@pytest.mark.parametrize("step, field, raw, expected", list(_reading_cases()))
def test_edit_on_any_step_of_the_reading_flow(env, ddb, step, field, raw, expected):
    kit = _start(step)
    version = kit.card()["version"]
    card = _say(kit, edit(field, raw)).snapshot.card
    assert card["kind"] == ("unreadable" if step == "unreadable" and field == "date" else "reading")
    assert kit.form()[field] == expected
    assert card["version"] > version


@pytest.mark.parametrize("step", ["reading", *CHECK_STEPS])
def test_a_serial_on_any_step_of_the_reading_flow_moves_the_photo(env, ddb, step):
    kit = _start(step)
    card = _say(kit, edit("serial", "W-9")).snapshot.card
    assert card["kind"] == "property"
    assert "W-9" in card_text(card)
    assert kit.store.meter(ACCOUNT, "W-9") is None


@pytest.mark.parametrize(
    "field, raw, shown",
    [("period_start", "2026-09-15", "15/09/2026"), ("months", "5", "ל־5 חודשים")],
)
def test_edit_on_the_change_card(env, ddb, field, raw, shown):
    kit = _start("change_period")
    card = _say(kit, edit(field, raw)).snapshot.card
    assert card["kind"] == "change_period"
    assert shown in card_text(card)


def test_moving_an_open_periods_start_lists_the_readings_it_moves(env, ddb):
    kit = Kit()
    apartment = kit.apartment()
    kit.meter(apartment, SERIAL, "water", [reading("2026-09-01", 1000, opens="2026-09-01")])
    _say(kit, IntentResult(intent="change_period", name="עורף", months=3))
    assert "1,000" not in card_text(kit.card())
    _say(kit, IntentResult(intent="reject"))
    moved = IntentResult(intent="change_period", name="עורף", date="2026-09-15", months=3)
    card = _say(kit, moved).snapshot.card
    assert "1,000" in card_text(card)
    kit.choose("confirm")
    assert kit.readings(SERIAL)[0]["opens_period"] == "2026-09-15"
    assert [(p["start"], p["months"]) for p in kit.periods(apartment)] == [("2026-09-15", 3)]


def test_a_close_card_edit_to_a_closing_value_is_left_unapplied_and_the_card_repeats(env, ddb):
    kit = Kit()
    apartment = kit.apartment()
    kit.meter(
        apartment,
        SERIAL,
        "water",
        [reading("2026-09-01", 1000, opens="2026-09-01"), reading("2026-10-20", 1100)],
    )
    _say(kit, IntentResult(intent="close_period", name="עורף"))
    card = kit.card()
    assert card["kind"] == "close"
    result = _say(kit, edit("value", "1150"))
    assert [r.text for r in result.replies] == [_help(card), card_text(card)]
    assert result.snapshot.card == card
    assert kit.readings(SERIAL)[-1]["value"] == 1100


def test_two_number_choice_can_be_undone_by_pressing_the_other(env, ddb):
    kit = Kit()
    apartment = kit.apartment(start=OPENED)
    kit.meter(apartment, "E-392", "electricity", [reading(OPENED, 392000, opens=OPENED)])
    kit.send_photo(electricity(top=392691, bottom=392693))
    card = kit.choose("bottom").snapshot.card
    assert options(card) == ["top", "bottom", "confirm"]
    assert "תחתון: 392,693 (מסומן)" in card_text(card)
    card = kit.choose("top").snapshot.card
    assert "עליון: 392,691 (מסומן)" in card_text(card)
    assert "תחתון: 392,693 (מסומן)" not in card_text(card)
    kit.choose("confirm")
    assert kit.readings("E-392")[-1]["value"] == 392691
    assert kit.store.meter(ACCOUNT, "E-392")["display_choice"] == 0


def test_meter_type_edit_changes_the_card(env, ddb):
    kit = Kit()
    _on_link(kit)
    assert "מונה מים W-1250" in card_text(kit.card())
    card = _say(kit, edit("meter_type", "electricity")).snapshot.card
    assert card["kind"] == "link"
    assert "מונה חשמל W-1250" in card_text(card)
    kit.choose("confirm")
    assert kit.store.meter(ACCOUNT, SERIAL)["type"] == "electricity"


def test_meter_type_edit_finds_the_meter_it_replaces(env, ddb):
    kit = Kit()
    apartment = kit.apartment(start=OPENED)
    kit.meter(apartment, "E-1", "electricity", [reading(OPENED, 500, opens=OPENED)])
    kit.send_photo(water())
    _say(kit, edits(("property", "הגפן 7"), ("apartment", "עורף")))
    assert kit.card()["kind"] == "link"
    card = _say(kit, edit("meter_type", "electricity")).snapshot.card
    assert card["kind"] == "replaced"
    assert "E-1" in card_text(card)


def test_one_message_may_change_several_fields(env, ddb):
    kit = Kit()
    _on_property(kit)
    card = _say(kit, edits(("property", "הגפן 7"), ("apartment", "עורף"))).snapshot.card
    assert card["kind"] == "period"


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize(
    "changes, expected",
    [
        (
            [("period_start", "2026-09-01"), ("months", "3"), ("apartment", "קדמית")],
            {"apartment": "קדמית", "period_start": "2026-09-01", "months": 3},
        ),
        (
            [("date", "2026-10-20"), ("period_start", "2026-10-10")],
            {"date": "2026-10-10", "period_start": "2026-10-10"},
        ),
    ],
)
def test_a_message_changing_several_fields_means_one_thing_in_any_order(
    env, ddb, changes, expected, reverse
):
    kit = _start("link")
    _say(kit, edits(*(reversed(changes) if reverse else changes)))
    assert kit.card()["kind"] == "link"
    assert {field: kit.form()[field] for field in expected} == expected


def test_an_apartment_named_before_its_property_is_kept(env, ddb):
    kit = Kit()
    _on_property(kit)
    assert _say(kit, edit("apartment", "עורף")).snapshot.card["kind"] == "property"
    assert _say(kit, edit("property", "הגפן 7")).snapshot.card["kind"] == "period"


def test_a_start_and_a_length_typed_before_the_apartment_are_kept(env, ddb):
    kit = Kit()
    _on_property(kit)
    _say(kit, edits(("period_start", "2026-09-01"), ("months", "3")))
    assert kit.card()["kind"] == "property"
    _say(kit, edits(("property", "הגפן 7"), ("apartment", "עורף")))
    assert kit.card()["kind"] == "link"
    assert "קריאת פתיחה לתקופה מ־01/09/2026 ל־3 חודשים" in card_text(kit.card())


def test_correcting_the_apartment_returns_a_reading_dated_at_the_old_start_to_its_photo(env, ddb):
    kit = Kit()
    _on_period(kit)
    _say(kit, edit("period_start", "2026-09-01"))
    assert kit.form()["date"] == "2026-09-01"
    _say(kit, edit("apartment", "קדמית"))
    assert (kit.card()["kind"], kit.form()["date"]) == ("period", "2026-10-29")


def test_joining_an_open_period_keeps_the_date_of_the_photo(env, ddb):
    kit = Kit()
    kit.apartment(start="2026-08-01", months=6)
    kit.send_photo(water())
    _say(
        kit,
        edits(("property", "הגפן 7"), ("apartment", "עורף"), ("period_start", "2026-09-01")),
    )
    assert kit.card()["kind"] == "link"
    assert kit.form()["date"] == "2026-10-29"
    kit.choose("confirm")
    [saved] = kit.readings(SERIAL)
    assert (saved["date"], saved["opens_period"]) == ("2026-10-29", "2026-08-01")


def test_a_serial_linked_meanwhile_returns_the_reading_to_the_date_of_the_photo(env, ddb):
    kit = Kit()
    _on_period(kit)
    _say(kit, edit("period_start", "2026-09-01"))
    assert kit.form()["date"] == "2026-09-01"
    other = kit.apartment(name="קדמית", property_name="לאונר 18", start=OPENED)
    kit.meter(other, SERIAL, "water", [reading(OPENED, 1000, opens=OPENED)])
    assert kit.choose("confirm").snapshot.card["kind"] == "reading"
    assert kit.form()["date"] == "2026-10-29"


def test_a_period_already_open_is_no_field_of_the_card(env, ddb):
    kit = Kit()
    kit.apartment(start=OPENED)
    kit.send_photo(water())
    _say(kit, edits(("property", "הגפן 7"), ("apartment", "עורף")))
    card = kit.card()
    assert card["kind"] == "link"
    assert "period_start" not in kit.form()
    result = _say(kit, edit("period_start", "2026-09-01"))
    assert [r.text for r in result.replies] == [_help(card), card_text(card)]


def test_a_date_on_a_check_step_runs_the_checks_again_on_confirm(env, ddb):
    kit = _start("duplicate")
    _say(kit, edit("date", "2026-10-20"))
    assert kit.choose("confirm").snapshot.card is None
    assert [(r["date"], r["value"]) for r in kit.readings(SERIAL)] == [
        (OPENED, 1000),
        ("2026-10-20", 1250),
        ("2026-10-29", 1100),
    ]


def test_a_value_on_the_lower_reading_card_runs_the_checks_again_on_confirm(env, ddb):
    kit = _start("lower_reading")
    assert _say(kit, edit("value", "1200")).snapshot.card["kind"] == "reading"
    assert kit.choose("confirm").snapshot.card["kind"] == "lower_reading"
    assert _say(kit, edit("value", "1400")).snapshot.card["kind"] == "reading"
    assert kit.choose("confirm").snapshot.card is None
    assert kit.readings(SERIAL)[-1]["value"] == 1400


def test_an_edit_after_a_reset_takes_the_reset_back(env, ddb):
    kit = Kit()
    _meter(
        kit,
        [reading("2026-09-01", 1300, opens="2026-09-01")],
        start="2026-09-01",
    )
    kit.send_photo(water())
    kit.choose("confirm")
    assert kit.choose("reset").snapshot.card["kind"] == "propose_close"
    _say(kit, edit("value", "1400"))
    assert kit.choose("confirm").snapshot.card["kind"] == "propose_close"
    kit.choose("keep_open")
    saved = kit.readings(SERIAL)[-1]
    assert (saved["value"], saved["resets"]) == (1400, False)


def _reset_on_propose_close(kit):
    apartment = _meter(kit, [reading("2026-09-01", 1300, opens="2026-09-01")], start="2026-09-01")
    kit.send_photo(water())
    kit.choose("confirm")
    assert kit.choose("reset").snapshot.card["kind"] == "propose_close"
    return apartment


def test_a_serial_of_another_meter_takes_the_reset_back(env, ddb):
    kit = Kit()
    apartment = _reset_on_propose_close(kit)
    kit.meter(apartment, "W-2", "water", [reading("2026-09-01", 1000, opens="2026-09-01")])
    assert _say(kit, edit("serial", "W-2")).snapshot.card["kind"] == "reading"
    assert kit.choose("confirm").snapshot.card["kind"] == "propose_close"
    kit.choose("keep_open")
    saved = kit.readings("W-2")[-1]
    assert (saved["value"], saved["resets"]) == (1250, False)


def test_a_serial_of_an_unknown_meter_carries_no_reset_into_the_link(env, ddb):
    kit = Kit()
    _reset_on_propose_close(kit)
    assert _say(kit, edit("serial", "W-9")).snapshot.card["kind"] == "property"
    _say(kit, edits(("property", "לאונר 18"), ("apartment", "קדמית")))
    kit.choose("start_today")
    kit.choose("confirm")
    [saved] = kit.readings("W-9")
    assert (saved["value"], saved["resets"]) == (1250, False)


def test_a_serial_corrected_back_returns_to_the_known_meter(env, ddb):
    kit = _start("reading")
    assert _say(kit, edit("serial", "W-9")).snapshot.card["kind"] == "property"
    card = _say(kit, edit("serial", SERIAL)).snapshot.card
    assert card["kind"] == "reading"
    assert "הגפן 7" in card_text(card)


def _unreadable_serial(value):
    return MeterReadingResult(
        meter_type="electricity",
        serial=None,
        serial_unreadable=True,
        candidates=[candidate(value, "top", "kWh")],
        handwritten_label=None,
    )


def test_a_bare_input_fills_the_serial_an_unreadable_card_lacks(env, ddb):
    kit = Kit()
    assert kit.send_photo(_unreadable_serial(392691)).snapshot.card["kind"] == "unreadable"
    card = _say(kit, edit(None, "2721058")).snapshot.card
    assert card["kind"] == "property"
    assert "2721058" in card_text(card)


def test_a_bare_input_fills_the_value_an_unreadable_card_lacks(env, ddb):
    kit = _start("unreadable")
    assert _say(kit, edit(None, "1251")).snapshot.card["kind"] == "reading"
    assert kit.form()["value"] == 1251


@pytest.mark.parametrize(
    "step, field, raw, expected",
    [
        ("property", "property", "הגפן 7", "הגפן 7"),
        ("apartment", "apartment", "עורף", "עורף"),
        ("period", "period_start", "2026-09-01", "2026-09-01"),
        ("link", "value", "1300", 1300),
        ("reading", "value", "1300", 1300),
        ("lower_reading", "value", "1300", 1300),
        ("change_period", "period_start", "2026-09-15", "2026-09-15"),
    ],
)
def test_a_bare_input_fills_the_default_field(env, ddb, step, field, raw, expected):
    kit = _start(step)
    _say(kit, edit(None, raw))
    assert kit.form()[field] == expected


def test_a_bare_input_on_a_card_with_no_default_field_is_not_understood(env, ddb):
    kit = Kit([IntentResult(intent="set_tariffs", electricity_price=0.6, water_price=15)])
    kit.say("חשמל 0.6 מים 15")
    card = kit.card()
    result = _say(kit, edit(None, "16"))
    assert [r.text for r in result.replies] == [_help(card), card_text(card)]


def _question(field, current):
    return FIELDS[("card", field)].question.format(current=current)


@pytest.mark.parametrize(
    "field, raw, current",
    [
        ("value", "לא מספר", "1250"),
        ("value", None, "1250"),
        ("date", "2026-11-01", "29/10/2026"),
        ("serial", "   ", SERIAL),
    ],
)
def test_a_missing_or_invalid_value_asks_again_naming_the_current_one(
    env, ddb, field, raw, current
):
    kit = _start("reading")
    card, form = kit.card(), kit.form()
    result = _say(kit, edit(field, raw))
    assert [r.text for r in result.replies] == [_question(field, current), card_text(card)]
    assert result.snapshot.card == card
    assert kit.form() == form


def test_the_question_names_an_empty_field_as_such(env, ddb):
    kit = _start("property")
    result = _say(kit, edit("property", "  "))
    assert result.replies[0].text == _question("property", texts.EMPTY_VALUE)
    assert result.snapshot.card["kind"] == "property"


def test_the_question_names_the_type_in_the_chat_language(env, ddb):
    kit = _start("link")
    result = _say(kit, edit("meter_type", "gas"))
    assert result.replies[0].text == _question("meter_type", texts.METER_KINDS["water"])


@pytest.mark.parametrize(
    "operation",
    [
        Operation(action="set", target="card", field="colour", value="red"),
        Operation(action="set", target="card", field="months", value="3"),
        Operation(action="delete", target="card", field=None, value=None),
    ],
)
def test_an_operation_the_card_cannot_take_changes_nothing(env, ddb, operation):
    kit = _start("reading")
    card, form = kit.card(), kit.form()
    result = _say(kit, IntentResult(intent="edit", operations=[operation]))
    assert [r.text for r in result.replies] == [_help(card), card_text(card)]
    assert result.snapshot.card == card
    assert kit.form() == form


def test_an_edit_on_a_record_the_account_lacks_changes_nothing(env, ddb):
    kit = _start("reading")
    card, form = kit.card(), kit.form()
    target = "reading|W-1250|2026-10-01#s"
    result = _say(kit, edit("value", "1", target))
    assert [r.text for r in result.replies] == [texts.unknown_record(["עורף"]), card_text(card)]
    assert result.snapshot.card == card
    assert kit.form() == form


def test_an_edit_with_no_operations_changes_nothing(env, ddb):
    kit = _start("reading")
    card = kit.card()
    result = _say(kit, IntentResult(intent="edit"))
    assert [r.text for r in result.replies] == [_help(card), card_text(card)]


def test_a_bad_operation_keeps_the_good_ones_beside_it_from_applying(env, ddb):
    kit = _start("reading")
    form = kit.form()
    result = _say(kit, edits(("value", "1300"), ("date", "yesterday")))
    assert kit.form() == form
    assert result.replies[0].text == _question("date", "29/10/2026")


def test_the_place_of_a_known_meter_is_no_field_of_its_reading_card(env, ddb):
    kit = _start("reading")
    assert set(kit.form()) == {"value", "date", "serial"}


def test_the_model_is_given_the_card_with_its_fields(env, ddb):
    kit = _start("property")
    _say(kit, edit("property", "הגפן 7"))
    context = kit.model.classified[-1][1]
    assert f"\n{texts.FORM_TITLE}:\n" in context
    assert "value: 1250\n" in context
    assert f"property: {texts.EMPTY_VALUE}" in context


def test_a_standby_card_is_sent_with_its_fields_and_takes_an_edit(env, ddb):
    kit = Kit([IntentResult(intent="close_period", name="עורף")], today=date(2026, 11, 2))
    apartment = kit.apartment()
    kit.meter(
        apartment,
        SERIAL,
        "water",
        [reading("2026-09-01", 1000, opens="2026-09-01"), reading("2026-10-31", 1030)],
    )
    kit.say("סגור")
    assert kit.choose("confirm").snapshot.card["kind"] == "tariffs_ask"
    card = _say(kit, edits(("electricity_price", "0.5"), ("water_price", "10"))).snapshot.card
    assert card["kind"] == "tariffs"
    assert "water_price: " in kit.model.classified[-1][1]
    assert [i["status"] for i in kit.store.pending(USER.email)] == ["awaiting"]


def test_a_refresh_that_moves_the_card_to_another_step_forgets_the_asked_field(env, ddb):
    kit = _start("duplicate")
    _say(kit, edit("date", None))
    [item] = kit.store.pending(USER.email)
    assert (item["step"], item["asked"]) == ("duplicate", "date")
    other = item["data"]["other"]
    kit.store.update_reading(ACCOUNT, SERIAL, other["date"], other["seq"], value=1110)
    kit.engine._refresh(USER, item)
    assert item["step"] == "reading"
    assert "asked" not in item
