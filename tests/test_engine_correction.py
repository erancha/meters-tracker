"""A typed change to a saved record, through the engine: it is planned on a copy, shown on one
correction card ahead of any waiting card, and written only on confirmation; it never mails a
report."""

from datetime import date

import pytest
from conftest import REGION
from engine_kit import (
    ACCOUNT,
    NOT_STATEMENT,
    UNKNOWN,
    USER,
    Kit,
    candidate,
    choice,
    edit,
    edits,
    options,
    reading,
    statement,
    water,
)
from moto.core import DEFAULT_ACCOUNT_ID
from moto.ses.models import ses_backends

from common import access, corrections, texts
from common.access import DELEGATE, OWNER, User
from common.account import diff, load
from common.corrections import address_refusal
from common.engine import AlreadyHandled
from common.fields import FIELDS
from common.flows import Action, card_text, meter_photo
from common.model import IntentResult, MeterReadingResult, Operation
from common.periods import consumption

FIRST, SECOND = "2026-05-01", "2026-07-01"
SERIAL = "2721056"
LATEST = "2026-08-15"


def _target(day) -> str:
    return f"reading|{SERIAL}|{day}#{day}T08:00:00"


def _seeded(kit):
    """An apartment with a closed period whose closing reading opens the open one."""
    apartment = kit.apartment(start=FIRST, closed_on=SECOND)
    kit.period(apartment, SECOND)
    kit.tariff("2026-01-01", 0.5, 10)
    kit.meter(
        apartment,
        SERIAL,
        "water",
        [
            reading(FIRST, 800, opens=FIRST),
            reading("2026-06-01", 840),
            reading(SECOND, 900, opens=SECOND, closes=FIRST),
            reading(LATEST, 950),
        ],
    )
    return apartment


def _say(kit, intent, text="טקסט"):
    kit.model.queue(intent)
    return kit.say(text)


def _sent():
    return ses_backends[DEFAULT_ACCOUNT_ID][REGION].sent_messages


def _text(card) -> str:
    return card_text(card)


def _asked(record, name, current) -> str:
    """The text of the question card asking for a field's value."""
    field = FIELDS[(record, name)]
    return card_text({"title": field.question.format(current=current), "lines": [field.how_to]})


# The apartment's name _seeded and Kit.apartment give it.
NAMES = ["עורף"]


def test_saved_value_shows_before_after_and_recomputed_charge(env, ddb):
    kit = Kit()
    _seeded(kit)
    stored = load(kit.store, ACCOUNT, OWNER)
    result = _say(kit, edit("value", "943", _target(LATEST)), "הקריאה הקודמת היא 943")
    card = result.snapshot.card
    assert card["kind"] == "correction"
    assert options(card) == ["confirm"]
    shown = _text(card)
    assert "950" in shown and "943" in shown
    assert "43 קוב" in shown and "50 קוב" in shown
    assert "01/07/2026" in shown
    assert load(kit.store, ACCOUNT, OWNER) == stored


def test_confirm_writes_and_sends_no_report(env, ddb):
    kit = Kit()
    _seeded(kit)
    _say(kit, edit("value", "943", _target(LATEST)))
    result = kit.choose("confirm")
    assert kit.readings(SERIAL)[-1]["value"] == 943
    assert result.snapshot.card is None
    assert texts.correction_saved() in result.replies[0].text
    assert "943" in result.replies[0].text
    assert kit.store.pending(USER.email) == []
    assert _sent() == []


def test_a_typed_confirm_writes_too(env, ddb):
    kit = Kit()
    _seeded(kit)
    _say(kit, edit("value", "943", _target(LATEST)))
    _say(kit, IntentResult(intent="confirm"), "אישור")
    assert kit.readings(SERIAL)[-1]["value"] == 943


def test_cancel_writes_nothing(env, ddb):
    kit = Kit()
    _seeded(kit)
    _say(kit, edit("value", "943", _target(LATEST)))
    result = _say(kit, IntentResult(intent="reject"), "בטל")
    assert result.replies[0].text == texts.correction_cancelled()
    assert result.snapshot.card is None
    assert kit.readings(SERIAL)[-1]["value"] == 950


def test_shared_row_changes_both_periods(env, ddb):
    kit = Kit()
    _seeded(kit)
    card = _say(kit, edit("value", "910", _target(SECOND))).snapshot.card
    shown = _text(card)
    assert "01/05/2026" in shown and "01/07/2026" in shown
    assert "110 קוב" in shown and "100 קוב" in shown
    assert "40 קוב" in shown and "50 קוב" in shown
    kit.choose("confirm")
    row = kit.readings(SERIAL)[2]
    assert (row["value"], row["opens_period"], row["closes_period"]) == (910, SECOND, FIRST)


def test_property_name_is_corrected(env, ddb):
    kit = Kit()
    apartment = _seeded(kit)
    _say(kit, edit("name", "הגפן 9", f"property|{apartment['property_id']}"))
    assert "הגפן 9" in _text(kit.card())
    kit.choose("confirm")
    assert kit.store.properties(ACCOUNT)[0]["name"] == "הגפן 9"


def test_apartment_name_and_months_are_corrected_together(env, ddb):
    kit = Kit()
    apartment = _seeded(kit)
    _say(kit, edits(("name", "קדמית"), ("months", "3"), target=f"apartment|{apartment['id']}"))
    kit.choose("confirm")
    [stored] = kit.store.apartments(ACCOUNT)
    assert (stored["name"], stored["period_months"]) == ("קדמית", 3)
    assert [p["months"] for p in kit.periods(apartment)] == [2, 3]


def test_corrected_apartment_months_reach_the_next_period_through_a_close(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    apartment = _seeded(kit)
    card = _say(kit, edit("months", "3", f"apartment|{apartment['id']}")).snapshot.card
    assert _text(card) == "תיקון נתונים שמורים\nדירה עורף · מספר החודשים: 3 במקום 2"
    kit.choose("confirm")
    _say(kit, IntentResult(intent="close_period", name="עורף"))
    kit.choose("confirm")
    assert [(p["start"], p["months"]) for p in kit.periods(apartment)][-1] == ("2026-08-16", 3)


def test_period_months_are_corrected(env, ddb):
    kit = Kit()
    apartment = _seeded(kit)
    _say(kit, edit("months", "3", f"period|{apartment['id']}|{SECOND}"))
    assert "01/07/2026" in _text(kit.card())
    kit.choose("confirm")
    assert [p["months"] for p in kit.periods(apartment)] == [2, 3]


def test_reset_flag_is_corrected(env, ddb):
    kit = Kit()
    _seeded(kit)
    _say(kit, edit("resets", "true", _target(LATEST)))
    kit.choose("confirm")
    assert kit.readings(SERIAL)[-1]["resets"] is True


def test_display_change_lists_past_readings_and_stores_the_choice(env, ddb):
    kit = Kit()
    apartment = kit.apartment(start=FIRST)
    kit.meter(
        apartment, "E-392", "electricity", [reading(FIRST, 500, opens=FIRST)], display_choice=0
    )
    _say(kit, edit("display", "bottom", "meter|E-392"))
    assert "500 (01/05/2026)" in _text(kit.card())
    kit.choose("confirm")
    assert kit.store.meter(ACCOUNT, "E-392")["display_choice"] == 1


def test_tariff_prices_are_corrected_and_the_periods_they_priced_are_named(env, ddb):
    kit = Kit()
    _seeded(kit)
    _say(
        kit, edits(("electricity_price", "0.7"), ("water_price", "12"), target="tariff|2026-01-01")
    )
    shown = _text(kit.card())
    assert "1,200.00 ₪" in shown and "1,000.00 ₪" in shown
    kit.choose("confirm")
    [tariff] = kit.tariffs()
    assert (tariff["electricity_per_kwh"], tariff["water_per_m3"]) == (0.7, 12)


def test_duplicate_name_is_refused(env, ddb):
    kit = Kit()
    apartment = _seeded(kit)
    kit.store.put_property(ACCOUNT, "לאונר 18")
    result = _say(kit, edit("name", "לאונר 18", f"property|{apartment['property_id']}"))
    assert [r.text for r in result.replies] == [texts.duplicate_property("לאונר 18")]
    assert result.snapshot.card is None
    assert kit.store.pending(USER.email) == []


def test_a_value_equal_to_the_stored_one_shows_no_card(env, ddb):
    kit = Kit()
    _seeded(kit)
    result = _say(kit, edit("value", "950", _target(LATEST)))
    assert [r.text for r in result.replies] == [texts.nothing_to_correct()]
    assert result.snapshot.card is None


def test_a_value_out_of_order_is_a_warning_on_a_card_that_can_be_confirmed(env, ddb):
    kit = Kit()
    _seeded(kit)
    card = _say(kit, edit("value", "905", _target("2026-06-01"))).snapshot.card
    assert options(card) == ["confirm"]
    assert "900 (01/07/2026)" in _text(card)


def test_correction_shows_ahead_of_a_waiting_card_then_that_card_returns(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    _seeded(kit)
    waiting = kit.send_photo(water(960, serial=SERIAL)).snapshot.card
    assert waiting["kind"] == "reading"
    result = _say(kit, edit("value", "943", _target(LATEST)))
    assert result.snapshot.card["kind"] == "correction"
    assert result.snapshot.waiting == 1
    result = kit.choose("confirm")
    assert result.snapshot.card == waiting
    assert kit.readings(SERIAL)[-1]["value"] == 943


def test_cancelling_a_correction_returns_to_the_waiting_card(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    _seeded(kit)
    waiting = kit.send_photo(water(960, serial=SERIAL)).snapshot.card
    _say(kit, edit("value", "943", _target(LATEST)))
    result = _say(kit, IntentResult(intent="reject"), "בטל")
    assert result.snapshot.card == waiting


def test_text_goes_to_the_correction_card_before_the_older_waiting_card(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    _seeded(kit)
    kit.send_photo(water(960, serial=SERIAL))
    _say(kit, edit("value", "943", _target(LATEST)))
    _say(kit, IntentResult(intent="confirm"), "אישור")
    assert kit.readings(SERIAL)[-1]["value"] == 943
    assert kit.card()["kind"] == "reading"


def test_a_photo_sent_while_a_correction_waits_stays_behind_it(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    _seeded(kit)
    _say(kit, edit("value", "943", _target(LATEST)))
    result = kit.send_photo(water(960, serial=SERIAL))
    assert result.snapshot.card["kind"] == "correction"


def test_a_new_correction_replaces_the_waiting_one_and_only_it_is_written(env, ddb):
    kit = Kit()
    _seeded(kit)
    first = _say(kit, edit("value", "843", _target(LATEST)), "הקריאה היא 843").snapshot.card
    result = _say(kit, edit("value", "845", _target(LATEST)), "לא, 845")
    assert result.replies[0].text == texts.correction_replaced()
    second = result.snapshot.card
    assert "845" in _text(second) and "843" not in _text(second)
    assert result.snapshot.waiting == 0
    assert [i["kind"] for i in kit.store.pending(USER.email)] == ["correction"]
    with pytest.raises(AlreadyHandled):
        kit.choose_on(first, "confirm")
    assert kit.readings(SERIAL)[-1]["value"] == 950
    assert kit.choose("confirm").snapshot.card is None
    assert kit.readings(SERIAL)[-1]["value"] == 845


def test_a_correction_of_another_record_replaces_the_waiting_one_too(env, ddb):
    kit = Kit()
    apartment = _seeded(kit)
    _say(kit, edit("value", "943", _target(LATEST)))
    _say(kit, edit("name", "הגפן 9", f"property|{apartment['property_id']}"))
    assert kit.choose("confirm").snapshot.card is None
    assert kit.store.properties(ACCOUNT)[0]["name"] == "הגפן 9"
    assert kit.readings(SERIAL)[-1]["value"] == 950


@pytest.mark.parametrize(
    ("intent", "reply"),
    [
        (edit("value", "943", "meter|nope"), texts.unknown_record(NAMES)),
        (edit("value", "950", _target(LATEST)), texts.nothing_to_correct()),
    ],
)
def test_a_new_operation_that_shows_no_card_keeps_the_waiting_correction(env, ddb, intent, reply):
    kit = Kit()
    _seeded(kit)
    first = _say(kit, edit("value", "843", _target(LATEST))).snapshot.card
    result = _say(kit, intent)
    assert [r.text for r in result.replies] == [reply, _text(first)]
    assert result.snapshot.card == first


def test_answering_after_the_records_changed_shows_the_card_again(env, ddb):
    kit = Kit()
    _seeded(kit)
    shown = _say(kit, edit("value", "943", _target(LATEST))).snapshot.card
    kit.store.update_reading(ACCOUNT, SERIAL, LATEST, f"{LATEST}T08:00:00", value=960)
    result = kit.choose("confirm")
    assert kit.readings(SERIAL)[-1]["value"] == 960
    again = result.snapshot.card
    assert again["kind"] == "correction" and again["version"] > shown["version"]
    assert "960" in _text(again) and _text(again) != _text(shown)
    assert result.replies[0].text == texts.correction_changed()
    kit.choose("confirm")
    assert kit.readings(SERIAL)[-1]["value"] == 943


def test_a_record_gone_by_confirmation_drops_the_card_and_writes_nothing(env, ddb):
    kit = Kit()
    apartment = _seeded(kit)
    _say(kit, edit("months", "3", f"period|{apartment['id']}|{SECOND}"))
    kit.store.delete_period(ACCOUNT, apartment["id"], SECOND)
    result = kit.choose("confirm")
    assert [r.text for r in result.replies] == [texts.correction_gone()]
    assert result.snapshot.card is None
    assert [p["months"] for p in kit.periods(apartment)] == [2]


def test_a_name_taken_by_confirmation_refuses_and_writes_nothing(env, ddb):
    kit = Kit()
    apartment = _seeded(kit)
    _say(kit, edit("name", "לאונר 18", f"property|{apartment['property_id']}"))
    kit.store.put_property(ACCOUNT, "לאונר 18")
    result = kit.choose("confirm")
    assert [r.text for r in result.replies] == [texts.duplicate_property("לאונר 18")]
    assert result.snapshot.card is None
    assert sorted(p["name"] for p in kit.store.properties(ACCOUNT)) == ["הגפן 7", "לאונר 18"]


def test_a_value_already_stored_by_confirmation_drops_the_card(env, ddb):
    kit = Kit()
    _seeded(kit)
    _say(kit, edit("value", "943", _target(LATEST)))
    kit.store.update_reading(ACCOUNT, SERIAL, LATEST, f"{LATEST}T08:00:00", value=943)
    result = kit.choose("confirm")
    assert [r.text for r in result.replies] == [texts.nothing_to_correct()]
    assert result.snapshot.card is None


def test_operation_on_an_id_not_in_context_writes_nothing(env, ddb):
    kit = Kit()
    _seeded(kit)
    stored = load(kit.store, ACCOUNT, OWNER)
    result = _say(kit, edit("value", "943", f"reading|{SERIAL}|2026-01-01#nope"))
    assert [r.text for r in result.replies] == [texts.unknown_record(NAMES)]
    assert result.snapshot.card is None
    assert kit.store.pending(USER.email) == []
    assert load(kit.store, ACCOUNT, OWNER) == stored


def test_a_field_not_in_the_table_writes_nothing(env, ddb):
    kit = Kit()
    apartment = _seeded(kit)
    result = _say(kit, edit("color", "red", f"property|{apartment['property_id']}"))
    assert [r.text for r in result.replies] == [texts.unknown_record(NAMES)]
    assert kit.store.pending(USER.email) == []


@pytest.mark.parametrize("value", ["abc", None])
def test_an_invalid_value_asks_the_fields_question_and_writes_nothing(env, ddb, value):
    kit = Kit()
    _seeded(kit)
    stored = load(kit.store, ACCOUNT, OWNER)
    result = _say(kit, edit("value", value, _target(LATEST)))
    assert [r.text for r in result.replies] == [_asked("reading", "value", "950")]
    assert [i["kind"] for i in kit.store.pending(USER.email)] == ["question"]
    assert load(kit.store, ACCOUNT, OWNER) == stored


def test_an_operation_that_fails_leaves_the_waiting_card_in_front(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    _seeded(kit)
    waiting = kit.send_photo(water(960, serial=SERIAL)).snapshot.card
    result = _say(kit, edit("value", "943", "meter|nope"))
    assert [r.text for r in result.replies] == [texts.unknown_record(NAMES), _text(waiting)]
    assert result.snapshot.card == waiting


def test_a_refused_correction_shows_the_waiting_card_again(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    apartment = _seeded(kit)
    kit.store.put_property(ACCOUNT, "לאונר 18")
    waiting = kit.send_photo(water(960, serial=SERIAL)).snapshot.card
    result = _say(kit, edit("name", "לאונר 18", f"property|{apartment['property_id']}"))
    assert [r.text for r in result.replies] == [
        texts.duplicate_property("לאונר 18"),
        _text(waiting),
    ]


def test_a_message_mixing_the_card_and_a_record_edits_the_card_then_shows_the_correction(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    _seeded(kit)
    kit.send_photo(water(960, serial=SERIAL))
    mixed = IntentResult(
        intent="edit",
        operations=[
            Operation(action="set", target="card", field="value", value="965"),
            Operation(action="set", target=_target(LATEST), field="value", value="943"),
        ],
    )
    result = _say(kit, mixed)
    assert [r.card["kind"] for r in result.replies] == ["reading", "correction"]
    assert result.snapshot.card["kind"] == "correction"
    after = kit.choose("confirm").snapshot.card
    assert after["kind"] == "reading" and "965" in _text(after)


def test_an_edit_on_a_record_reaches_a_correction_from_a_standby_card(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    _seeded(kit)
    kit.store.put_pending(
        USER.email,
        {
            "seq": "2026-08-20T00:00:00_0000",
            "kind": "tariffs",
            "status": "standby",
            "step": "ask",
            "version": 0,
            "data": {
                "given": {"electricity": None, "water": None},
                "prices": {"electricity": None, "water": None},
                "effective_date": "2026-08-20",
                "from_close": True,
                "source_image_key": None,
                "ex_vat": None,
            },
        },
    )
    result = _say(kit, edit("value", "943", _target(LATEST)))
    assert result.snapshot.card["kind"] == "correction"
    confirmed = kit.choose("confirm")
    assert texts.tariffs_not_missing() in [r.text for r in confirmed.replies]
    assert confirmed.snapshot.card is None


def test_a_record_edit_with_a_card_edit_and_no_card_asks_about_the_value_then_corrects(env, ddb):
    kit = Kit()
    _seeded(kit)
    mixed = IntentResult(
        intent="edit",
        operations=[
            Operation(action="set", target="card", field="value", value="965"),
            Operation(action="set", target=_target(LATEST), field="value", value="943"),
        ],
    )
    result = _say(kit, mixed)
    assert result.replies[0].text == texts.bare_input("965")
    assert result.snapshot.card["kind"] == "correction"


def test_a_card_edit_on_a_correction_card_says_what_the_card_takes_and_shows_it(env, ddb):
    kit = Kit()
    _seeded(kit)
    card = _say(kit, edit("value", "943", _target(LATEST))).snapshot.card
    result = _say(kit, edit("value", "1"))
    help_text = texts.card_help([texts.OPTION_LABELS["confirm"]])
    assert [r.text for r in result.replies] == [help_text, _text(card)]


def test_the_correction_card_explains_itself(env, ddb):
    kit = Kit()
    _seeded(kit)
    _say(kit, edit("value", "943", _target(LATEST)))
    result = _say(kit, IntentResult(intent="explain"), "מה זה?")
    assert result.replies[0].text == texts.EXPLANATIONS["correction"]


def test_a_close_card_edit_to_a_closing_value_becomes_a_correction(env, ddb):
    kit = Kit(today=date(2026, 10, 29))
    apartment = kit.apartment()
    kit.tariff("2026-01-01", 0.5, 10)
    opening = reading("2026-09-01", 1000, opens="2026-09-01")
    closing = reading("2026-10-20", 1100)
    kit.meter(apartment, SERIAL, "water", [opening, closing])
    _say(kit, IntentResult(intent="close_period", name="עורף"))
    target = f"reading|{SERIAL}|2026-10-20#2026-10-20T08:00:00"
    assert kit.form() == {target: 1100}
    result = _say(kit, edit("value", "1150", target))
    assert result.snapshot.card["kind"] == "correction"
    after = kit.choose("confirm").snapshot.card
    assert after["kind"] == "close" and "1,150" in _text(after)
    assert kit.periods(apartment)[0]["closed_on"] is None
    kit.choose("confirm")
    assert kit.periods(apartment)[0]["closed_on"] == "2026-10-20"
    assert kit.readings(SERIAL)[-1]["value"] == 1150


def test_a_close_card_edit_naming_the_reading_as_a_field_becomes_a_correction(env, ddb):
    kit = Kit(today=date(2026, 10, 29))
    apartment = kit.apartment()
    closing = reading("2026-10-20", 1100)
    kit.meter(
        apartment, SERIAL, "water", [reading("2026-09-01", 1000, opens="2026-09-01"), closing]
    )
    _say(kit, IntentResult(intent="close_period", name="עורף"))
    field = f"reading|{SERIAL}|2026-10-20#2026-10-20T08:00:00"
    assert _say(kit, edit(field, "1150")).snapshot.card["kind"] == "correction"
    kit.choose("confirm")
    assert kit.readings(SERIAL)[-1]["value"] == 1150


def test_a_reading_id_the_card_does_not_list_stays_a_card_edit(env, ddb):
    kit = Kit(today=date(2026, 10, 29))
    apartment = kit.apartment()
    closing = reading("2026-10-20", 1100)
    kit.meter(
        apartment, SERIAL, "water", [reading("2026-09-01", 1000, opens="2026-09-01"), closing]
    )
    _say(kit, IntentResult(intent="close_period", name="עורף"))
    card = kit.card()
    field = f"reading|{SERIAL}|2026-09-01#2026-09-01T08:00:00"
    result = _say(kit, edit(field, "1150"))
    help_text = texts.card_help([o["label"] for o in card["options"]])
    assert [r.text for r in result.replies] == [help_text, _text(card)]


def test_a_parked_close_has_no_form(env, ddb):
    kit = Kit()
    apartment = kit.apartment()
    kit.meter(apartment, SERIAL, "water", [reading("2026-09-01", 1000, opens="2026-09-01")])
    _say(kit, IntentResult(intent="close_period", name="עורף"))
    _say(kit, IntentResult(intent="close_period", name="עורף"))
    assert kit.card()["kind"] == "close_waiting"
    assert kit.form() == {}


def test_the_records_lookup_is_offered_with_a_card_and_with_no_card(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    _seeded(kit)
    kit.send_photo(water(960, serial=SERIAL))
    _say(kit, IntentResult(intent="unknown"))
    on_card = kit.model.classified[-1][1]
    assert texts.FORM_TITLE in on_card and f"meter|{SERIAL}" not in on_card
    assert f"meter|{SERIAL}" in kit.lookup(apartment="עורף")
    _say(kit, IntentResult(intent="reject"), "בטל")
    _say(kit, IntentResult(intent="unknown"))
    no_card = kit.model.classified[-1][1]
    assert f"meter|{SERIAL}" not in no_card
    assert f"meter|{SERIAL}" in kit.lookup(apartment="עורף")


def test_a_message_naming_no_record_sends_no_records(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    _seeded(kit)
    _say(kit, IntentResult(intent="unknown"), "ניסוי")
    _, context = kit.model.classified[-1]
    assert not any(kind in context for kind in ("apartment|", "meter|", "reading|"))


def test_a_correction_of_a_reading_saved_days_ago_works_from_today(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    _seeded(kit)
    kit.send_photo(water(960, serial=SERIAL))
    kit.choose("confirm")
    [saved] = [r for r in kit.readings(SERIAL) if r["value"] == 960]
    kit.today = date(2026, 8, 29)
    _say(kit, IntentResult(intent="unknown"), "ניסוי")
    target = f"reading|{SERIAL}|{saved['date']}#{saved['seq']}"
    assert target in kit.lookup(apartment="עורף", reading_date=saved["date"])
    result = _say(kit, edit("value", "965", target))
    assert result.snapshot.card["kind"] == "correction"


def test_a_close_request_still_finds_its_apartment_by_name_beside_the_records(env, ddb):
    kit = Kit()
    apartment = kit.apartment()
    kit.meter(apartment, SERIAL, "water", [reading("2026-09-01", 1000, opens="2026-09-01")])
    result = _say(kit, IntentResult(intent="close_period", name="עורף"))
    assert "עורף" in result.replies[0].text


def test_a_renamed_serial_is_written_in_one_step_with_its_readings(env, ddb):
    kit = Kit()
    _seeded(kit)
    _say(kit, edit("serial", "2721058", f"meter|{SERIAL}"))
    assert kit.store.meter(ACCOUNT, "2721058") is None
    kit.choose("confirm")
    assert kit.store.meter(ACCOUNT, SERIAL) is None
    assert kit.readings(SERIAL) == []
    assert [r["value"] for r in kit.readings("2721058")] == [800, 840, 900, 950]
    assert kit.store.meter(ACCOUNT, "2721058")["former_serials"] == [SERIAL]
    assert _sent() == []


def test_a_meter_row_with_no_former_serials_reads_as_empty(env, ddb):
    kit = Kit()
    _seeded(kit)
    assert "former_serials" not in kit.store.meter(ACCOUNT, SERIAL)
    [meter] = load(kit.store, ACCOUNT, OWNER).meters
    assert meter["former_serials"] == []


def test_a_moved_reading_date_is_written_with_the_periods_it_moves(env, ddb):
    kit = Kit()
    apartment = _seeded(kit)
    card = _say(kit, edit("date", "2026-06-25", _target(SECOND))).snapshot.card
    assert "25/06/2026" in _text(card)
    kit.choose("confirm")
    assert [(p["start"], p["closed_on"]) for p in kit.periods(apartment)] == [
        (FIRST, "2026-06-25"),
        ("2026-06-26", None),
    ]
    assert kit.readings(SERIAL)[2]["date"] == "2026-06-25"
    assert kit.readings(SERIAL)[2]["opens_period"] == "2026-06-26"


def test_a_meter_reassigned_to_a_new_apartment_takes_copies_of_its_periods(env, ddb):
    kit = Kit()
    apartment = _seeded(kit)
    empty = kit.store.put_apartment(
        ACCOUNT, apartment["property_id"], "קדמית", FIRST, apartment["period_months"]
    )
    card = _say(kit, edit("apartment", empty["id"], f"meter|{SERIAL}")).snapshot.card
    assert "קדמית" in _text(card)
    kit.choose("confirm")
    assert kit.store.meter(ACCOUNT, SERIAL)["apartment_id"] == empty["id"]
    assert [p["start"] for p in kit.periods(empty)] == [FIRST, SECOND]


def test_a_correction_over_one_transaction_shows_no_card_and_names_its_size(env, ddb):
    kit = Kit()
    apartment = kit.apartment(start=FIRST)
    rows = [reading(f"2026-0{1 + i % 9}-{1 + i // 9:02}", i, opens=FIRST) for i in range(60)]
    kit.meter(apartment, SERIAL, "water", rows)
    result = _say(kit, edit("serial", "2721058", f"meter|{SERIAL}"))
    assert [r.text for r in result.replies] == [texts.correction_too_large(122)]
    assert result.snapshot.card is None
    assert kit.store.pending(USER.email) == []


def test_a_value_no_longer_valid_at_confirmation_drops_the_card_and_writes_nothing(env, ddb):
    kit = Kit()
    _seeded(kit)
    _say(kit, edit("date", "2026-10-20", _target(LATEST)))
    kit.today = date(2026, 10, 10)
    result = kit.choose("confirm")
    assert [r.text for r in result.replies] == [_asked("reading", "date", "15/08/2026")]
    assert result.snapshot.card["kind"] == "question"
    assert kit.readings(SERIAL)[-1]["date"] == LATEST


def test_a_refusal_at_confirmation_writes_nothing(env, ddb):
    kit = Kit()
    apartment = _seeded(kit)
    _say(kit, edit("date", "2026-06-20", _target(LATEST)))
    kit.store.put_reading(ACCOUNT, SERIAL, reading("2026-06-20", 870))
    result = kit.choose("confirm")
    assert [r.text for r in result.replies] == [texts.same_day_clash(SERIAL, date(2026, 6, 20))]
    assert kit.periods(apartment)[0]["closed_on"] == SECOND
    assert kit.readings(SERIAL)[-1]["date"] == LATEST


def test_waiting_card_on_the_corrected_serial_becomes_a_reading_card(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    apartment = kit.apartment(start=SECOND)
    kit.tariff("2026-01-01", 0.5, 10)
    kit.meter(apartment, SERIAL, "water", [reading(SECOND, 843, opens=SECOND)])
    assert kit.send_photo(water(866, serial="2721088")).snapshot.card["kind"] == "property"
    _say(kit, edit("serial", "2721088", f"meter|{SERIAL}"))
    result = kit.choose("confirm")
    card = result.snapshot.card
    assert card["kind"] == "reading"
    assert "2721088" in _text(card) and "866" in _text(card)
    assert result.replies[-1].card == card
    saved = kit.choose("confirm")
    assert "23 קוב" in saved.replies[0].text
    assert [r["value"] for r in kit.readings("2721088")] == [843, 866]
    assert kit.store.pending(USER.email) == []


def test_a_waiting_link_of_another_serial_stays_where_it_was(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    apartment = kit.apartment(start=SECOND)
    kit.meter(apartment, SERIAL, "water", [reading(SECOND, 843, opens=SECOND)])
    waiting = kit.send_photo(water(866, serial="999")).snapshot.card
    _say(kit, edit("serial", "2721058", f"meter|{SERIAL}"))
    result = kit.choose("confirm")
    assert result.snapshot.card == waiting


def test_a_meters_last_reading_moved_away_deletes_the_meter_on_confirmation(env, ddb):
    kit = Kit()
    apartment = _seeded(kit)
    kit.meter(apartment, "E-1", "electricity", [reading("2026-08-01", 5)], display_choice=0)
    target = "reading|E-1|2026-08-01#2026-08-01T08:00:00"
    card = _say(kit, edit("serial", SERIAL, target)).snapshot.card
    assert texts.meter_removed("electricity", "E-1") in card["lines"]
    kit.choose("confirm")
    assert kit.store.meter(ACCOUNT, "E-1") is None
    assert kit.readings("E-1") == []


def test_another_closing_reading_is_written_with_the_periods_it_moves(env, ddb):
    kit = Kit()
    apartment = _seeded(kit)
    chosen = _target("2026-06-01").removeprefix("reading|")
    _say(kit, edit("closing", chosen, f"period|{apartment['id']}|{FIRST}"))
    kit.choose("confirm")
    assert [(p["start"], p["closed_on"]) for p in kit.periods(apartment)] == [
        (FIRST, "2026-06-01"),
        ("2026-06-02", None),
    ]
    assert kit.readings(SERIAL)[1]["closes_period"] == FIRST


def test_two_serials_crossing_in_one_message_show_no_card_and_say_what_to_do(env, ddb):
    kit = Kit()
    apartment = _seeded(kit)
    kit.meter(apartment, "E-1", "electricity", [reading(SECOND, 5, opens=SECOND)])
    crossing = IntentResult(
        intent="edit",
        operations=[
            Operation(action="set", target=f"meter|{SERIAL}", field="serial", value="E-1"),
            Operation(action="set", target="meter|E-1", field="serial", value="Z-1"),
        ],
    )
    result = _say(kit, crossing)
    assert [r.text for r in result.replies] == [texts.one_serial_at_a_time()]
    assert result.snapshot.card is None


def _reading_id(row) -> str:
    return f"{row['date']}#{row['seq']}"


def test_a_replacement_is_undone_after_the_new_meters_date_was_corrected(env, ddb):
    kit = Kit([edit("property", "הגפן 7"), edit("apartment", "עורף")])
    apartment = kit.apartment(start="2026-10-01")
    kit.meter(apartment, "W-0999", "water", [reading("2026-10-01", 900, opens="2026-10-01")])
    kit.send_photo(water())
    kit.say("הגפן 7")
    kit.say("עורף")
    kit.choose("confirm")
    assert kit.store.meter(ACCOUNT, "W-1250")["replaces"] == "W-0999"
    [linked] = kit.readings("W-1250")
    _say(kit, edit("date", "2026-10-20", f"reading|W-1250|{_reading_id(linked)}"))
    kit.choose("confirm")
    [moved] = kit.readings("W-1250")
    _say(kit, edit("serial", "W-0999", f"reading|W-1250|{_reading_id(moved)}"))
    kit.choose("confirm")
    assert kit.store.meter(ACCOUNT, "W-1250") is None
    assert kit.store.meter(ACCOUNT, "W-0999")["retired_at"] is None


def test_a_meter_row_with_no_link_reads_as_none(env, ddb):
    kit = Kit()
    _seeded(kit)
    [meter] = load(kit.store, ACCOUNT, OWNER).meters
    assert meter["replaces"] is None


def _deleting(target) -> IntentResult:
    operation = Operation(action="delete", target=target, field=None, value=None)
    return IntentResult(intent="edit", operations=[operation])


def test_deleted_reading_leaves_history_and_figures(env, ddb):
    kit = Kit()
    apartment = _seeded(kit)
    stored = load(kit.store, ACCOUNT, OWNER)
    card = _say(kit, _deleting(_target(LATEST)), "התכוונתי לבטל").snapshot.card
    assert card["kind"] == "correction" and options(card) == ["confirm"]
    label = texts.reading_label("water", SERIAL, date(2026, 8, 15))
    assert texts.reading_removed(label, 950) in card["lines"]
    assert "50 קוב" in _text(card)
    assert load(kit.store, ACCOUNT, OWNER) == stored
    kit.choose("confirm")
    assert [r["date"] for r in kit.readings(SERIAL)] == [FIRST, "2026-06-01", SECOND]
    assert kit.readings(SERIAL)[-1]["opens_period"] == SECOND
    assert [p["start"] for p in kit.periods(apartment)] == [FIRST, SECOND]
    assert _sent() == []


def test_a_cancelled_delete_leaves_the_reading(env, ddb):
    kit = Kit()
    _seeded(kit)
    _say(kit, _deleting(_target(LATEST)))
    _say(kit, IntentResult(intent="reject"))
    assert len(kit.readings(SERIAL)) == 4


def test_a_deleted_meter_goes_on_confirmation_with_its_readings(env, ddb):
    kit = Kit()
    apartment = _seeded(kit)
    kit.meter(apartment, "E-1", "electricity", [reading("2026-08-01", 5)], display_choice=0)
    card = _say(kit, _deleting("meter|E-1")).snapshot.card
    assert card["lines"].count(texts.meter_removed("electricity", "E-1")) == 1
    kit.choose("confirm")
    assert kit.store.meter(ACCOUNT, "E-1") is None
    assert kit.readings("E-1") == []


def test_a_refused_delete_shows_no_card_and_says_what_to_do(env, ddb):
    kit = Kit()
    apartment = _seeded(kit)
    result = _say(kit, _deleting(f"apartment|{apartment['id']}"))
    assert result.snapshot.card is None
    assert result.replies[-1].text == texts.apartment_holds_meters(
        "עורף", [texts.meter_name("water", SERIAL)]
    )
    assert kit.store.meter(ACCOUNT, SERIAL) is not None


def test_a_reopened_close_merges_the_next_period_on_confirmation(env, ddb):
    kit = Kit()
    apartment = _seeded(kit)
    card = _say(kit, edit("closed_on", None, f"period|{apartment['id']}|{FIRST}")).snapshot.card
    assert texts.period_reopened("עורף", date(2026, 5, 1)) in card["lines"]
    kit.choose("confirm")
    assert [(p["start"], p["closed_on"]) for p in kit.periods(apartment)] == [(FIRST, None)]
    assert kit.readings(SERIAL)[2]["closes_period"] is None
    assert kit.readings(SERIAL)[2]["opens_period"] is None


def test_an_undone_vacate_is_written_with_its_new_period(env, ddb):
    kit = Kit()
    apartment = kit.apartment(start=FIRST, closed_on=SECOND)
    kit.meter(
        apartment,
        SERIAL,
        "water",
        [reading(FIRST, 800, opens=FIRST), reading(SECOND, 900, closes=FIRST)],
    )
    _say(kit, edit("vacant", "false", f"period|{apartment['id']}|{FIRST}"))
    kit.choose("confirm")
    assert [(p["start"], p["closed_on"]) for p in kit.periods(apartment)] == [
        (FIRST, SECOND),
        ("2026-07-02", None),
    ]
    assert kit.readings(SERIAL)[-1]["opens_period"] == "2026-07-02"


def test_a_deleted_tariff_names_the_closed_period_on_its_card(env, ddb):
    kit = Kit()
    _seeded(kit)
    kit.tariff("2026-06-01", 0.5, 20)
    card = _say(kit, _deleting("tariff|2026-06-01")).snapshot.card
    assert "01/05/2026" in _text(card)
    assert "01/07/2026" in _text(card)
    kit.choose("confirm")
    assert [t["effective_date"] for t in kit.tariffs()] == ["2026-01-01"]


def _closed_twice(kit, second_closed):
    """An apartment whose first period closed on 2026-02-28 and whose second period, from
    2026-03-01, is closed on 2026-03-20 by E when second_closed, open otherwise."""
    kit.today = date(2026, 3, 20)
    apartment = kit.apartment(start="2026-01-01", closed_on="2026-02-28")
    kit.period(apartment, "2026-03-01", months=6, closed_on="2026-03-20" if second_closed else None)
    kit.tariff("2026-01-01", 0.5, 10)
    kit.meter(
        apartment,
        "E-1",
        "electricity",
        [
            reading("2026-01-01", 10, opens="2026-01-01"),
            reading("2026-02-28", 60, opens="2026-03-01", closes="2026-01-01"),
            reading("2026-03-20", 80, closes="2026-03-01" if second_closed else None),
        ],
        display_choice=0,
    )
    return apartment


def _replaced_then_closed(kit, second_closed=False):
    """O was replaced by N in the first period, which then closed on N and E, with no closing
    reading of O's; with second_closed, the second period closed on N and E as well."""
    apartment = _closed_twice(kit, second_closed)
    kit.meter(
        apartment,
        "O",
        "water",
        [reading("2026-01-01", 100, opens="2026-01-01"), reading("2026-01-20", 150)],
        retired_at="2026-01-25",
    )
    kit.meter(
        apartment,
        "N",
        "water",
        [
            reading("2026-01-25", 1, opens="2026-01-01"),
            reading("2026-02-28", 31, opens="2026-03-01", closes="2026-01-01"),
            reading("2026-03-20", 51, closes="2026-03-01" if second_closed else None),
        ],
    )
    kit.store.update_meter(ACCOUNT, "N", replaces="O")
    return apartment


def _two_later_photos_count_once(kit, apartment, serial="O"):
    """Two photos past the correction, each counted in one period: a meter left with no opening in
    the open period, or left open in the closed one, would drop or double the units between its
    last reading and the first photo, which no check of the tables alone shows."""
    for today, value in ((date(2026, 3, 25), 170), (date(2026, 4, 20), 200)):
        kit.today = today
        kit.send_photo(water(value, serial=serial))
        kit.choose("confirm")
    rows = kit.readings(serial)
    counted = [consumption(rows, p) for p in kit.periods(apartment)]
    assert [(c.units, c.final) for c in counted] == [(50, True), (50, False)]


def _reopening(apartment, start) -> IntentResult:
    operation = Operation(
        action="set", target=f"period|{apartment['id']}|{start}", field="closed_on", value=None
    )
    return IntentResult(intent="edit", operations=[operation])


def test_a_reopen_after_a_deleted_replacement_opens_the_old_meter_on_its_last_reading(env, ddb):
    kit = Kit()
    apartment = _replaced_then_closed(kit, second_closed=True)
    _say(kit, _deleting("meter|N"))
    kit.choose("confirm")
    card = _say(kit, _reopening(apartment, "2026-03-01")).snapshot.card
    opened = texts.opening_moved("water", "O", 150, date(2026, 1, 20), date(2026, 3, 1))
    assert opened in card["lines"]
    kit.choose("confirm")
    _two_later_photos_count_once(kit, apartment)


def test_a_reopen_after_a_reassign_opens_the_moved_meter_on_its_last_reading(env, ddb):
    kit = Kit()
    apartment = _closed_twice(kit, second_closed=True)
    other = kit.apartment(name="קדמית", property_name="לאונר 18", start="2026-01-01")
    kit.meter(
        other,
        "W",
        "water",
        [reading("2026-01-02", 100, opens="2026-01-01"), reading("2026-01-20", 150)],
    )
    move = Operation(action="set", target="meter|W", field="apartment", value=apartment["id"])
    _say(kit, IntentResult(intent="edit", operations=[move]))
    kit.choose("confirm")
    card = _say(kit, _reopening(apartment, "2026-03-01")).snapshot.card
    opened = texts.opening_moved("water", "W", 150, date(2026, 1, 20), date(2026, 3, 1))
    assert opened in card["lines"]
    kit.choose("confirm")
    _two_later_photos_count_once(kit, apartment, serial="W")


def test_a_deleted_replacement_restores_the_old_meter_and_later_photos_count_once(env, ddb):
    kit = Kit()
    apartment = _replaced_then_closed(kit)
    card = _say(kit, _deleting("meter|N")).snapshot.card
    assert texts.replacement_undone("water", "O") in card["lines"]
    kit.choose("confirm")
    assert kit.store.meter(ACCOUNT, "O")["retired_at"] is None
    _two_later_photos_count_once(kit, apartment)


def test_a_cleared_retirement_rebuilds_the_old_meter_and_later_photos_count_once(env, ddb):
    kit = Kit()
    apartment = _replaced_then_closed(kit)
    swap = IntentResult(
        intent="edit",
        operations=[
            Operation(action="set", target="meter|N", field="retired_at", value="2026-03-20"),
            Operation(action="set", target="meter|O", field="retired_at", value=None),
        ],
    )
    card = _say(kit, swap).snapshot.card
    assert card["kind"] == "correction"
    kit.choose("confirm")
    assert kit.store.meter(ACCOUNT, "O")["retired_at"] is None
    _two_later_photos_count_once(kit, apartment)


def test_a_reading_moved_before_the_replacements_opening_counts_on_through_a_close(env, ddb):
    kit = Kit([], today=date(2026, 2, 10))
    apartment = kit.apartment(start="2026-01-01", months=1)
    kit.tariff("2026-01-01", 0.5, 10)
    kit.meter(
        apartment,
        "O",
        "water",
        [reading("2026-01-01", 100, opens="2026-01-01"), reading("2026-01-15", 120)],
        retired_at="2026-01-25",
    )
    kit.meter(
        apartment,
        "N",
        "water",
        [reading("2026-01-25", 30, opens="2026-01-01"), reading("2026-02-01", 40)],
    )
    kit.store.update_meter(ACCOUNT, "N", replaces="O")
    target = "reading|O|2026-01-15#2026-01-15T08:00:00"
    kit.store.update_reading(ACCOUNT, "O", "2026-01-15", "2026-01-15T08:00:00", value=20)
    card = _say(kit, edit("serial", "N", target)).snapshot.card
    line = texts.opening_moved("water", "N", 20, date(2026, 1, 15), date(2026, 1, 1))
    assert line in card["lines"]
    kit.choose("confirm")
    kit.today = date(2026, 2, 20)
    kit.send_photo(water(60, serial="N"))
    kit.choose("confirm")
    kit.choose("close")
    period = kit.periods(apartment)[0]
    assert period["closed_on"] == "2026-02-20"
    counted = consumption(kit.readings("N"), period)
    assert (counted.opening["date"], counted.units, counted.final) == ("2026-01-15", 40, True)


def test_corrected_value_adds_the_photo_to_failed_photos_once(env, ddb):
    kit = Kit()
    _seeded(kit)
    for value in ("943", "944"):
        _say(kit, edit("value", value, _target(LATEST)))
        kit.choose("confirm")
    saved = kit.readings(SERIAL)[-1]
    assert saved["value"] == 944
    assert saved["failed_photos"] == [saved["image_key"]]
    assert saved["model"] == kit.engine.model_name
    assert "failed_photos" not in kit.readings(SERIAL)[-2]


def test_a_corrected_reading_keeps_the_model_that_failed_on_it_before(env, ddb):
    kit = Kit()
    _seeded(kit)
    seq = f"{LATEST}T08:00:00"
    kit.store.update_reading(
        ACCOUNT, SERIAL, LATEST, seq, failed_photos=["earlier"], model="older-model"
    )
    _say(kit, edit("value", "943", _target(LATEST)))
    kit.choose("confirm")
    saved = kit.readings(SERIAL)[-1]
    assert saved["failed_photos"] == ["earlier", "k"]
    assert saved["model"] == "older-model"


def test_a_corrected_serial_adds_each_photo_of_the_meter_to_failed_photos(env, ddb):
    kit = Kit()
    _seeded(kit)
    _say(kit, edit("serial", "2721058", f"meter|{SERIAL}"))
    kit.choose("confirm")
    assert all(r["failed_photos"] == ["k"] for r in kit.readings("2721058"))


def test_two_meters_with_one_seq_record_a_failure_only_on_the_corrected_one(env, ddb):
    kit = Kit()
    apartment = _seeded(kit)
    kit.meter(apartment, "E-1", "electricity", [reading(FIRST, 10, opens=FIRST)])
    _say(kit, edit("value", "20", f"reading|E-1|{FIRST}#{FIRST}T08:00:00"))
    kit.choose("confirm")
    assert kit.readings("E-1")[0]["failed_photos"] == ["k"]
    assert "failed_photos" not in kit.readings(SERIAL)[0]


def test_a_corrected_date_or_flag_records_no_failed_photo(env, ddb):
    kit = Kit()
    _seeded(kit)
    _say(kit, edit("date", "2026-08-16", _target(LATEST)))
    kit.choose("confirm")
    _say(kit, edit("resets", "true", f"reading|{SERIAL}|2026-08-16#{LATEST}T08:00:00"))
    kit.choose("confirm")
    saved = kit.readings(SERIAL)[-1]
    assert saved["resets"] is True and saved["date"] == "2026-08-16"
    assert "failed_photos" not in saved


def _photo_tags(kit, user=USER) -> list[dict]:
    messages, _ = kit.engine.load(user)
    return [m["reading"] for m in messages if m["role"] == "user" and m["image_key"]]


def _saved_photo(kit, value=960):
    kit.send_photo(water(value, serial=SERIAL))
    kit.choose("confirm")
    return kit.readings(SERIAL)[-1]


def test_correction_refreshes_day_panel_tags(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    apartment = _seeded(kit)
    saved = _saved_photo(kit)
    assert _photo_tags(kit)[0]["apartment"] == "עורף"
    _say(kit, edit("name", "סלון", f"apartment|{apartment['id']}"))
    kit.choose("confirm")
    assert _photo_tags(kit) == [
        {"state": "saved", "meter_type": "water", "apartment": "סלון", "property": "הגפן 7"}
    ]
    _say(kit, _deleting(f"reading|{SERIAL}|{_reading_id(saved)}"))
    kit.choose("confirm")
    assert _photo_tags(kit)[0]["state"] == "dropped"
    assert _photo_tags(kit)[0]["apartment"] == "סלון"


def test_a_photo_a_duplicate_replaced_shows_dropped_after_a_correction(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    apartment = _seeded(kit)
    _saved_photo(kit, 960)
    kit.send_photo(water(961, serial=SERIAL))
    assert kit.choose("confirm").snapshot.card["kind"] == "duplicate"
    kit.choose("confirm")
    _say(kit, edit("name", "סלון", f"apartment|{apartment['id']}"))
    kit.choose("confirm")
    assert [t["state"] for t in _photo_tags(kit)] == ["dropped", "saved"]


def test_a_renamed_meters_photo_stays_saved_in_the_day_panel(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    _seeded(kit)
    _saved_photo(kit)
    _say(kit, edit("serial", "2721058", f"meter|{SERIAL}"))
    kit.choose("confirm")
    assert _photo_tags(kit)[0]["state"] == "saved"


ASSISTANT = User(email="helper@x.com", account=ACCOUNT, role=DELEGATE)


def _say_as_delegate(kit, intent, text="טקסט"):
    kit.model.queue(intent)
    return kit.engine.turn(ASSISTANT, Action(text=text))


def test_a_correction_leaves_another_persons_day_panel_tags(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    apartment = _seeded(kit)
    image = b"helper-photo"
    kit.register_photo(image, water(960, serial=SERIAL))
    card = kit.engine.turn(ASSISTANT, Action(image=image, media_type="image/jpeg")).snapshot.card
    kit.engine.turn(ASSISTANT, choice(card, "confirm"))
    _say(kit, edit("name", "סלון", f"apartment|{apartment['id']}"))
    kit.choose("confirm")
    assert _photo_tags(kit, ASSISTANT)[0]["apartment"] == "עורף"


def _delegate_photo(kit, result):
    image = f"helper-{len(kit._photos)}".encode()
    kit.register_photo(image, result)
    return kit.engine.turn(ASSISTANT, Action(image=image, media_type="image/jpeg")).snapshot.card


def _delegate_choose(kit, card, option):
    return kit.engine.turn(ASSISTANT, choice(card, option))


def _rename_meter(kit, old, new):
    _say(kit, edit("serial", new, f"meter|{old}"))
    kit.choose("confirm")


def test_delegate_card_on_a_corrected_serial_finds_the_meter(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    _seeded(kit)
    assert _delegate_photo(kit, water(866, serial="999"))["kind"] == "property"
    other = kit.apartment(name="חצר", property_name="הגפן 9", start=SECOND)
    kit.meter(other, "999", "water", [reading(SECOND, 843, opens=SECOND)])
    _rename_meter(kit, "999", "1000")
    result = _say_as_delegate(kit, edit("property", "הגפן 9"))
    assert result.replies[0].text == texts.card_changed()
    card = result.snapshot.card
    assert card["kind"] == "reading" and "1000" in _text(card)
    assert [r["value"] for r in kit.readings("1000")] == [843]
    _delegate_choose(kit, card, "confirm")
    assert [r["value"] for r in kit.readings("1000")] == [843, 866]
    assert kit.store.meter(ACCOUNT, "999") is None


def test_owner_rename_shows_again_on_delegate_card(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    _seeded(kit)
    _with_delegate(kit)
    shown = _delegate_photo(kit, water(960, serial=SERIAL))
    _rename_meter(kit, SERIAL, "2721058")
    result = _delegate_choose(kit, shown, "confirm")
    card = result.snapshot.card
    assert result.replies[0].text == texts.card_changed()
    assert "2721058" in _text(card) and SERIAL not in _text(card)
    assert card["version"] > shown["version"]
    assert [r["value"] for r in kit.readings("2721058")][-1] == 950
    assert OLD not in "\n".join(r.text for r in result.replies)
    _delegate_choose(kit, card, "confirm")
    saved = kit.readings("2721058")[-1]
    assert saved["value"] == 960
    assert saved["failed_photos"] == [saved["image_key"]]


def test_a_cancel_on_a_changed_card_shows_it_again_and_drops_nothing(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    _seeded(kit)
    shown = _delegate_photo(kit, water(960, serial=SERIAL))
    _rename_meter(kit, SERIAL, "2721058")
    kit.model.queue(IntentResult(intent="reject"))
    result = kit.engine.turn(ASSISTANT, Action(text="עזוב"))
    assert result.replies[0].text == texts.card_changed()
    assert result.snapshot.card["kind"] == "reading"
    assert result.snapshot.card["pending"] == shown["pending"]


def test_a_renamed_apartment_shows_again_on_the_waiting_reading_card(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    apartment = _seeded(kit)
    shown = kit.send_photo(water(960, serial=SERIAL)).snapshot.card
    _say(kit, edit("name", "סלון", f"apartment|{apartment['id']}"))
    returned = kit.choose("confirm").snapshot.card
    assert "סלון" in _text(returned) and "סלון" not in _text(shown)
    kit.choose_on(returned, "confirm")
    assert kit.readings(SERIAL)[-1]["value"] == 960
    assert _photo_tags(kit)[-1]["apartment"] == "סלון"


def test_a_deleted_meter_turns_its_waiting_card_into_a_link(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    _seeded(kit)
    kit.send_photo(water(960, serial=SERIAL))
    _say(kit, _deleting(f"meter|{SERIAL}"))
    assert kit.choose("confirm").snapshot.card["kind"] == "property"
    assert kit.store.meter(ACCOUNT, SERIAL) is None


def test_a_reading_card_returns_with_the_meters_new_serial_after_a_rename(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    _seeded(kit)
    kit.send_photo(water(960, serial=SERIAL))
    _say(kit, edit("serial", "2721099", f"meter|{SERIAL}"))
    result = kit.choose("confirm")
    returned = result.snapshot.card
    assert returned["kind"] == "reading"
    assert "2721099" in _text(returned) and SERIAL not in _text(returned)
    assert [r.card for r in result.replies if r.card is not None][-1] == returned
    kit.choose_on(returned, "confirm")
    assert kit.readings("2721099")[-1]["value"] == 960


def test_a_reading_card_whose_meter_was_deleted_returns_as_the_new_meters_link(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    _seeded(kit)
    kit.send_photo(water(960, serial=SERIAL))
    _say(kit, _deleting(f"meter|{SERIAL}"))
    returned = kit.choose("confirm").snapshot.card
    assert returned["kind"] == "property"
    assert SERIAL in _text(returned)
    assert kit.store.meter(ACCOUNT, SERIAL) is None


def test_a_close_card_returns_with_the_closing_reading_a_correction_changed(env, ddb):
    kit = Kit(today=date(2026, 10, 29))
    apartment = kit.apartment()
    opening = reading("2026-09-01", 1000, opens="2026-09-01")
    kit.meter(apartment, SERIAL, "water", [opening, reading("2026-10-20", 1100)])
    shown = _say(kit, IntentResult(intent="close_period", name="עורף")).snapshot.card
    target = f"reading|{SERIAL}|2026-10-20#2026-10-20T08:00:00"
    _say(kit, edit("value", "1120", target))
    returned = kit.choose("confirm").snapshot.card
    assert returned["kind"] == "close" and returned["pending"] == shown["pending"]
    assert "1,120" in _text(returned) and "1,120" not in _text(shown)
    kit.choose_on(returned, "confirm")
    assert kit.periods(apartment)[0]["closed_on"] == "2026-10-20"


def test_a_load_rebuilding_a_card_leaves_an_item_a_tap_finished_meanwhile(env, ddb, monkeypatch):
    kit = Kit(today=date(2026, 8, 20))
    _seeded(kit)
    kit.send_photo(water(960, serial=SERIAL))
    [item] = kit.store.pending(USER.email)
    stored = load(kit.store, ACCOUNT, OWNER)
    renamed = stored.copy()
    renamed.apartments[0]["name"] = "סלון"
    kit.store.apply(ACCOUNT, diff(stored, renamed))
    read = meter_photo.refresh

    def tap_meanwhile(engine, user, refreshed):
        """A tap claims and finishes the item while the load rebuilds its card."""
        read(engine, user, refreshed)
        assert kit.store.claim_pending(USER.email, item["seq"], item["version"], item["status"])
        kit.store.delete_pending(USER.email, item["seq"])

    monkeypatch.setattr(meter_photo, "refresh", tap_meanwhile)
    _, snapshot = kit.engine.load(USER)
    assert kit.store.pending(USER.email) == []
    assert snapshot.card is None


def test_a_tap_on_the_card_a_load_rebuilt_is_answered(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    _seeded(kit)
    shown = kit.send_photo(water(960, serial=SERIAL)).snapshot.card
    stored = load(kit.store, ACCOUNT, OWNER)
    renamed = stored.copy()
    renamed.apartments[0]["name"] = "סלון"
    kit.store.apply(ACCOUNT, diff(stored, renamed))
    _, snapshot = kit.engine.load(USER)
    rebuilt = snapshot.card
    assert rebuilt["pending"] == shown["pending"] and rebuilt["version"] > shown["version"]
    assert "סלון" in _text(rebuilt)
    result = kit.choose_on(rebuilt, "confirm")
    assert texts.card_changed() not in [r.text for r in result.replies]
    assert kit.readings(SERIAL)[-1]["value"] == 960


def test_a_meter_retired_while_its_card_waits_drops_the_card(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    _seeded(kit)
    kit.send_photo(water(960, serial=SERIAL))
    _say(kit, edit("retired_at", LATEST, f"meter|{SERIAL}"))
    result = kit.choose("confirm")
    assert result.replies[-1].text == texts.retired_meter_alone(SERIAL)
    assert "לא הוחלף" in result.replies[-1].text
    assert kit.store.pending(USER.email) == []
    assert kit.readings(SERIAL)[-1]["value"] == 950


def test_a_duplicate_card_whose_other_reading_was_deleted_returns_to_the_reading_card(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    _seeded(kit)
    photographed = reading("2026-08-20", 955)
    kit.store.put_reading(ACCOUNT, SERIAL, photographed)
    kit.send_photo(water(960, serial=SERIAL))
    assert kit.choose("confirm").snapshot.card["kind"] == "duplicate"
    _say(kit, _deleting(f"reading|{SERIAL}|{_reading_id(photographed)}"))
    assert kit.choose("confirm").snapshot.card["kind"] == "reading"
    assert kit.choose("confirm").snapshot.card is None
    assert kit.readings(SERIAL)[-1]["value"] == 960


def test_a_renamed_property_follows_on_a_waiting_link_card(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    apartment = kit.apartment(start=SECOND)
    kit.send_photo(water(866, serial="999"))
    _say(kit, edit("property", "הגפן 7"))
    assert kit.card()["kind"] == "apartment"
    _say(kit, edit("name", "הגפן 8", f"property|{apartment['property_id']}"))
    assert "הגפן 8" in _text(kit.choose("confirm").snapshot.card)
    result = _say(kit, edit("apartment", "עורף"))
    assert "הגפן 8" in _text(result.snapshot.card)
    assert [p["name"] for p in kit.store.properties(ACCOUNT)] == ["הגפן 8"]


def test_a_close_card_of_an_apartment_gone_by_its_answer_drops(env, ddb):
    kit = Kit(today=date(2026, 10, 29))
    apartment = kit.apartment()
    kit.meter(
        apartment,
        SERIAL,
        "water",
        [reading("2026-09-01", 1000, opens="2026-09-01"), reading("2026-10-20", 1100)],
    )
    _say(kit, IntentResult(intent="close_period", name="עורף"))
    shown = kit.card()
    kit.store._tables["apartments"].delete_item(Key={"pk": ACCOUNT, "sk": apartment["id"]})
    result = kit.choose_on(shown, "confirm")
    assert result.replies[-1].text == texts.card_gone()
    assert kit.store.pending(USER.email) == []


def test_a_close_card_whose_period_was_reopened_away_says_how_to_open_one(env, ddb):
    kit = Kit(today=date(2026, 10, 29))
    apartment = kit.apartment(start="2026-09-01")
    kit.meter(
        apartment,
        SERIAL,
        "water",
        [reading("2026-09-01", 1000, opens="2026-09-01"), reading("2026-10-20", 1100)],
    )
    _say(kit, IntentResult(intent="close_period", name="עורף"))
    shown = kit.card()
    kit.store.update_period(ACCOUNT, apartment["id"], "2026-09-01", closed_on="2026-10-20")
    result = kit.choose_on(shown, "confirm")
    assert result.replies[-1].text == texts.no_open_period("עורף")
    assert "לפתוח תקופה" in result.replies[-1].text


def test_a_close_card_whose_period_was_closed_elsewhere_drops(env, ddb):
    kit = Kit(today=date(2026, 10, 29))
    apartment = kit.apartment(start="2026-09-01")
    kit.meter(
        apartment,
        SERIAL,
        "water",
        [reading("2026-09-01", 1000, opens="2026-09-01"), reading("2026-10-20", 1100)],
    )
    _say(kit, IntentResult(intent="close_period", name="עורף"))
    shown = kit.card()
    kit.store.update_reading(
        ACCOUNT,
        SERIAL,
        "2026-10-20",
        "2026-10-20T08:00:00",
        closes_period="2026-09-01",
        opens_period="2026-10-21",
    )
    kit.store.update_period(ACCOUNT, apartment["id"], "2026-09-01", closed_on="2026-10-20")
    kit.period(apartment, "2026-10-21")
    result = kit.choose_on(shown, "confirm")
    assert result.replies[-1].text == texts.period_already_closed("עורף", date(2026, 9, 1))
    assert kit.store.pending(USER.email) == []
    assert kit.periods(apartment)[-1]["closed_on"] is None


def test_a_period_card_of_an_apartment_opened_by_its_answer_drops(env, ddb):
    kit = Kit()
    apartment = kit.apartment(start="2026-07-01", closed_on="2026-08-31")
    shown = _say(kit, IntentResult(intent="change_period", name="עורף")).snapshot.card
    kit.period(apartment, "2026-09-01")
    result = kit.choose_on(shown, "confirm")
    assert result.replies[-1].text == texts.period_already_open("עורף")
    assert kit.store.pending(USER.email) == []


def test_a_period_card_whose_buttons_changed_overnight_is_shown_again(env, ddb):
    kit = Kit(today=date(2026, 8, 31))
    kit.send_photo(water(866, serial="999"))
    _say(kit, edit("property", "הגפן 9"))
    shown = _say(kit, edit("apartment", "חצר")).snapshot.card
    assert options(shown) == ["start_today", "start_month"]
    kit.today = date(2026, 9, 1)
    result = kit.choose_on(shown, "start_month")
    assert result.replies[0].text == texts.card_changed()
    assert options(result.snapshot.card) == ["start_today"]
    [item] = kit.store.pending(USER.email)
    assert item["data"]["period_start"] is None


def test_a_delegate_asking_to_delete_an_apartment_is_told_the_owner_can(env, ddb):
    kit = Kit()
    apartment = kit.apartment()
    target = f"apartment|{apartment['id']}"
    delete = Operation(action="delete", target=target, field=None, value=None)
    result = _say_as_delegate(kit, IntentResult(intent="edit", operations=[delete]))
    assert result.snapshot.card is None
    assert result.replies[-1].text == texts.owner_only()
    assert [a["id"] for a in kit.store.apartments(ACCOUNT)] == [apartment["id"]]


OLD, NEW = "old@x.com", "new@x.com"


def _with_delegate(kit):
    kit.store.access_put_if_absent(OLD, ACCOUNT, DELEGATE)


def _correct_address(kit, new=NEW):
    return _say(kit, edit("address", new, f"delegate|{OLD}"))


def _access(kit, address):
    return kit.store.access_get(address)


def test_owner_corrects_a_delegate_address_through_a_card(env, ddb):
    kit = Kit()
    _with_delegate(kit)
    card = _correct_address(kit, "New@X.com").snapshot.card
    assert card["kind"] == "correction"
    shown = _text(card)
    assert OLD in shown and NEW in shown
    assert texts.access_lost(OLD) in shown and texts.access_gained(NEW) in shown
    assert _access(kit, OLD) == {"email": OLD, "account": ACCOUNT, "role": DELEGATE}
    assert _access(kit, NEW) is None

    result = kit.choose("confirm")
    assert result.snapshot.card is None
    assert texts.correction_saved() in result.replies[0].text
    assert _access(kit, OLD) is None
    assert _access(kit, NEW) == {"email": NEW, "account": ACCOUNT, "role": DELEGATE}
    assert _sent() == []


def test_delegate_cannot_correct_a_delegate_address(env, ddb):
    kit = Kit()
    _with_delegate(kit)
    result = _say_as_delegate(kit, edit("address", NEW, f"delegate|{OLD}"))
    assert result.snapshot.card is None
    assert result.replies[-1].text == texts.owner_only()
    assert _access(kit, NEW) is None


def test_new_address_holding_data_is_refused(env, ddb):
    kit = Kit()
    _with_delegate(kit)
    kit.store.access_put_if_absent(NEW, NEW, OWNER)
    kit.store.put_property(NEW, "הגפן 7")
    result = _correct_address(kit)
    assert result.snapshot.card is None
    assert result.replies[-1].text == texts.address_taken()
    assert _access(kit, NEW)["role"] == OWNER


def test_new_address_delegating_for_another_owner_is_refused(env, ddb):
    kit = Kit()
    _with_delegate(kit)
    kit.store.access_put_if_absent(NEW, "someone@x.com", DELEGATE)
    result = _correct_address(kit)
    assert result.snapshot.card is None
    assert result.replies[-1].text == texts.address_taken()


def test_new_address_of_an_owner_with_delegates_is_refused(env, ddb):
    kit = Kit()
    _with_delegate(kit)
    kit.store.access_put_if_absent(NEW, NEW, OWNER)
    kit.store.access_put_if_absent("theirs@x.com", NEW, DELEGATE)
    assert address_refusal(kit.store, NEW) == texts.address_taken()


def test_the_owner_own_address_is_refused(env, ddb):
    kit = Kit()
    kit.store.access_put_if_absent(ACCOUNT, ACCOUNT, OWNER)
    _with_delegate(kit)
    result = _correct_address(kit, ACCOUNT)
    assert result.replies[-1].text == texts.address_taken()
    assert _access(kit, ACCOUNT)["role"] == OWNER


def test_empty_owner_account_can_take_the_address(env, ddb):
    kit = Kit()
    _with_delegate(kit)
    kit.store.access_put_if_absent(NEW, NEW, OWNER)
    _correct_address(kit)
    kit.choose("confirm")
    assert _access(kit, NEW) == {"email": NEW, "account": ACCOUNT, "role": DELEGATE}
    assert _access(kit, OLD) is None


def test_refused_on_confirmation_when_it_gained_data(env, ddb):
    kit = Kit()
    _with_delegate(kit)
    kit.store.access_put_if_absent(NEW, NEW, OWNER)
    _correct_address(kit)
    kit.store.put_property(NEW, "הגפן 7")
    result = kit.choose("confirm")
    assert result.snapshot.card is None
    assert result.replies[-1].text == texts.address_taken()
    assert _access(kit, NEW)["role"] == OWNER
    assert _access(kit, OLD)["account"] == ACCOUNT


def test_a_condition_failing_at_the_write_is_a_refusal(env, ddb, monkeypatch):
    kit = Kit()
    _with_delegate(kit)
    _correct_address(kit)
    kit.store.access_put_if_absent(NEW, "someone@x.com", DELEGATE)
    monkeypatch.setattr(corrections, "address_refusal", lambda store, address: None)
    result = kit.choose("confirm")
    assert result.snapshot.card is None
    assert result.replies[-1].text == texts.delegate_address_changed()
    assert _access(kit, OLD)["account"] == ACCOUNT
    assert _access(kit, NEW)["account"] == "someone@x.com"


def test_a_delegate_gone_by_confirmation_drops_the_card(env, ddb):
    kit = Kit()
    _with_delegate(kit)
    _correct_address(kit)
    kit.store._tables["access"].delete_item(Key={"pk": OLD})
    result = kit.choose("confirm")
    assert result.snapshot.card is None
    assert result.replies[-1].text == texts.unknown_delegate(OLD)
    assert _access(kit, NEW) is None


def test_an_invalid_address_asks_again(env, ddb):
    kit = Kit()
    _with_delegate(kit)
    result = _correct_address(kit, "לא כתובת")
    assert result.replies[-1].text == _asked("delegate", "address", OLD)
    assert result.snapshot.card["kind"] == "question"


FORMER = User(email=OLD, account=ACCOUNT, role=DELEGATE)


def _photo_as(kit, user, result):
    image = f"photo-{len(kit._photos)}".encode()
    kit.register_photo(image, result)
    return kit.engine.turn(user, Action(image=image, media_type="image/jpeg", note=None))


def _delegate_with_a_meter(kit, months=2):
    apartment = kit.apartment(months=months)
    kit.meter(apartment, "W1", "water", [reading("2026-09-01", 100, opens="2026-09-01")])
    _with_delegate(kit)
    return apartment


def _waiting_new_meter(kit, user):
    """Leaves the user on a new meter's last card, naming the account's own property."""
    _photo_as(kit, user, water(5, serial="E9"))
    prop = kit.store.properties(user.account)[0]
    kit.model.queue(edits(("property", prop["name"]), ("apartment", "חדשה")))
    result = kit.engine.turn(user, Action(text="טקסט"))
    return kit.engine.turn(user, choice(result.snapshot.card, "start_today"))


def _move_address(kit):
    assert _correct_address(kit).snapshot.card["kind"] == "correction"
    kit.choose("confirm")
    assert _access(kit, OLD) is None


def test_an_access_change_clears_a_waiting_new_meter_of_the_old_address(env, ddb):
    kit = Kit()
    _delegate_with_a_meter(kit)
    assert _waiting_new_meter(kit, FORMER).snapshot.card is not None
    _move_address(kit)
    fresh = access.resolve(kit.store, OLD)
    assert kit.store.pending(OLD) == []
    assert kit.engine.snapshot(fresh).card is None
    kit.model.queue(IntentResult(intent="unknown"))
    kit.engine.turn(fresh, Action(text="שלום"))
    assert kit.store.properties(OLD) == [] and kit.store.meters(OLD) == []


def test_an_old_card_never_lands_in_a_second_account_the_address_joins(env, ddb):
    kit = Kit()
    _delegate_with_a_meter(kit)
    _waiting_new_meter(kit, FORMER)
    _move_address(kit)
    access.resolve(kit.store, OLD)
    second = User("two@x.com", "two@x.com", OWNER)
    kit.store.access_put_if_absent(second.email, second.account, OWNER)
    kit.store.access_put_if_absent("d2@x.com", second.account, DELEGATE)
    kit.store.put_property(second.account, "שלי")
    kit.model.queue(edit("address", OLD, "delegate|d2@x.com"))
    card = kit.engine.turn(second, Action(text="טקסט")).snapshot.card
    kit.engine.turn(second, choice(card, "confirm"))
    assert access.resolve(kit.store, OLD).account == second.account
    assert kit.store.pending(OLD) == []
    assert load(kit.store, second.account, OWNER).apartments == []


def test_an_access_change_clears_a_waiting_reading_and_a_waiting_close(env, ddb):
    kit = Kit()
    _delegate_with_a_meter(kit, months=1)
    shown = _photo_as(kit, FORMER, water(150, serial="W1")).snapshot.card
    kit.engine.turn(FORMER, choice(shown, shown["options"][0]["id"]))
    assert kit.store.pending(OLD) != []
    _move_address(kit)
    fresh = access.resolve(kit.store, OLD)
    assert kit.engine.snapshot(fresh).card is None
    kit.model.queue(IntentResult(intent="confirm"))
    kit.engine.turn(fresh, Action(text="כן"))
    assert kit.store.meters(OLD) == []


def test_a_cleared_photo_stops_waiting_on_the_day_panel(env, ddb):
    kit = Kit()
    _delegate_with_a_meter(kit)
    _photo_as(kit, FORMER, water(150, serial="W1"))
    _move_address(kit)
    [photo] = kit.store.photo_messages(OLD)
    assert photo["reading"]["state"] == "dropped"


def test_the_card_says_when_the_new_address_loses_a_waiting_card(env, ddb):
    kit = Kit()
    _delegate_with_a_meter(kit)
    newcomer = access.resolve(kit.store, NEW)
    _photo_as(kit, newcomer, water(5, serial="NW"))
    card = _correct_address(kit).snapshot.card
    assert texts.waiting_card_lost(NEW) in _text(card)
    kit.choose("confirm")
    assert kit.store.pending(NEW) == []


def test_no_waiting_card_means_no_such_line(env, ddb):
    kit = Kit()
    _with_delegate(kit)
    assert texts.waiting_card_lost(NEW) not in _text(_correct_address(kit).snapshot.card)


def test_a_waiting_card_appearing_by_confirmation_shows_the_card_again(env, ddb):
    kit = Kit()
    _delegate_with_a_meter(kit)
    _correct_address(kit)
    _photo_as(kit, access.resolve(kit.store, NEW), water(5, serial="NW"))
    result = kit.choose("confirm")
    assert result.replies[0].text == texts.correction_changed()
    assert texts.waiting_card_lost(NEW) in _text(result.snapshot.card)
    assert _access(kit, OLD)["account"] == ACCOUNT


def test_the_waiting_items_count_towards_the_row_limit(env, ddb):
    kit = Kit()
    _with_delegate(kit)
    _correct_address(kit)
    for number in range(99):
        kit.store.put_pending(OLD, {"seq": f"s{number:03}", "kind": "stub", "status": "standby"})
    result = kit.choose("confirm")
    assert result.replies[-1].text == texts.correction_too_large(101)
    assert _access(kit, OLD)["account"] == ACCOUNT
    assert len(kit.store.pending(OLD)) == 99


def test_a_delegate_deleting_a_meter_that_empties_the_place_is_told_the_owner_can(env, ddb):
    kit = Kit()
    apartment = kit.apartment()
    kit.meter(
        apartment,
        "W1",
        "water",
        [reading("2026-09-01", 100, opens="2026-09-01"), reading("2026-10-01", 150)],
    )
    delete = Operation(action="delete", target="meter|W1", field=None, value=None)
    result = _say_as_delegate(kit, IntentResult(intent="edit", operations=[delete]))
    assert result.snapshot.card is None
    assert result.replies[-1].text == texts.owner_only()
    assert [m["serial"] for m in kit.store.meters(ACCOUNT)] == ["W1"]


def test_the_records_block_carries_delegate_addresses_on_an_owner_message_only(env, ddb):
    kit = Kit()
    kit.apartment()
    _with_delegate(kit)
    _say_as_delegate(kit, IntentResult(intent="unknown"))
    assert OLD not in kit.lookup(delegates=True)
    _say(kit, IntentResult(intent="unknown"))
    assert f"delegate|{OLD}" in kit.lookup(delegates=True)


def test_a_delegate_correction_leaves_the_access_rows(env, ddb):
    kit = Kit()
    _seeded(kit)
    _with_delegate(kit)
    card = _say_as_delegate(kit, edit("value", "943", _target(LATEST))).snapshot.card
    kit.engine.turn(ASSISTANT, choice(card, "confirm"))
    assert kit.readings(SERIAL)[-1]["value"] == 943
    assert _access(kit, OLD)["account"] == ACCOUNT


def test_a_swap_of_two_addresses_has_nothing_to_confirm(env, ddb):
    kit = Kit()
    _with_delegate(kit)
    kit.store.access_put_if_absent("peer@x.com", ACCOUNT, DELEGATE)
    swap = [
        Operation(action="set", target=f"delegate|{OLD}", field="address", value="peer@x.com"),
        Operation(action="set", target="delegate|peer@x.com", field="address", value=OLD),
    ]
    result = _say(kit, IntentResult(intent="edit", operations=swap))
    assert result.snapshot.card is None
    assert result.replies[-1].text == texts.nothing_to_correct()


def test_an_address_refusal_ignores_the_case_it_is_given(env, ddb):
    kit = Kit()
    kit.store.access_put_if_absent(NEW, "someone@x.com", DELEGATE)
    assert address_refusal(kit.store, "New@X.com") == texts.address_taken()


def test_an_address_too_long_to_be_one_asks_again(env, ddb):
    kit = Kit()
    _with_delegate(kit)
    result = _correct_address(kit, "a" * 3000 + "@x.com")
    assert result.replies[-1].text == _asked("delegate", "address", OLD)


def _second_apartment_photo_waits(kit, value=1300):
    """A reading card of a second apartment's meter, waiting behind the first photo's card."""
    other = kit.apartment(name="קדמית", start="2026-08-01")
    kit.meter(other, "W-2", "water", [reading("2026-08-01", 1000, opens="2026-08-01")])
    kit.send_photo(water(960, serial=SERIAL))
    kit.send_photo(water(value, serial="W-2"))
    first, second = sorted(kit.store.pending(USER.email), key=lambda i: i["seq"])
    return first, second


def test_edit_reaches_the_card_behind_the_first(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    _seeded(kit)
    first, second = _second_apartment_photo_waits(kit)
    result = _say(kit, edit("value", "1310", f"card|{second['seq']}"), "הקריאה של קדמית 1310")
    assert "1,310" in result.replies[-1].text
    assert result.snapshot.card["pending"] == first["seq"]
    kit.choose("confirm")
    kit.choose("confirm")
    assert kit.readings("W-2")[-1]["value"] == 1310
    assert kit.readings(SERIAL)[-1]["value"] == 960


def test_the_records_block_lists_every_waiting_card_with_its_form(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    _seeded(kit)
    first, second = _second_apartment_photo_waits(kit)
    _say(kit, IntentResult(intent="unknown"))
    _, context = kit.model.classified[-1]
    assert f"card|{first['seq']}: value=960; date=2026-08-20; serial={SERIAL}" in context
    assert f"card|{second['seq']}: value=1300; date=2026-08-20; serial=W-2" in context


NOT_A_METER = MeterReadingResult(
    meter_type="unknown",
    serial=None,
    serial_unreadable=False,
    serial_uncertain=False,
    candidates=[candidate(960, "single", "m3")],
    handwritten_label=None,
)


def _photo_message(kit) -> dict:
    """The latest photo message, whatever its state."""
    return kit.store.photo_messages(USER.email)[-1]


def _reopen(kit, *changes):
    """Types changes to the latest photo message, which the app dropped."""
    target = f"photo|{_photo_message(kit)['at']}"
    return _say(kit, edits(*changes, target=target))


def test_typed_serial_reopens_a_photo_called_not_a_meter(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    _seeded(kit)
    assert kit.send_photo(NOT_A_METER).replies[-1].text == texts.not_a_meter()
    key = _photo_message(kit)["image_key"]
    result = _reopen(kit, ("serial", SERIAL))
    assert result.snapshot.card["kind"] == "reading"
    assert "960" in _text(result.snapshot.card)
    assert _photo_message(kit)["reading"]["state"] == "awaiting"
    kit.choose("confirm")
    saved = kit.readings(SERIAL)[-1]
    assert (saved["value"], saved["image_key"], saved["date"]) == (960, key, "2026-08-20")
    assert saved["failed_photos"] == [key]
    assert _photo_message(kit)["reading"]["state"] == "saved"


def test_a_typed_serial_equal_to_the_one_read_still_records_the_miss_once(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    _seeded(kit)
    kit.send_photo(NOT_A_METER.model_copy(update={"serial": "W-9"}))
    key = _photo_message(kit)["image_key"]
    _reopen(kit, ("serial", "W-9"))
    assert kit.card()["kind"] == "unreadable"
    _say(kit, edit("meter_type", "water"))
    assert kit.card()["kind"] == "property"
    assert kit.store.pending(USER.email)[0]["data"]["failed_photos"] == [key]


def test_the_records_block_lists_the_last_five_dropped_photos(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    _seeded(kit)
    for _ in range(6):
        kit.send_photo(NOT_A_METER)
    kit.send_photo(water(960, serial=SERIAL))
    ats = [m["at"] for m in kit.store.photo_messages(USER.email)]
    _say(kit, IntentResult(intent="unknown"))
    _, context = kit.model.classified[-1]
    empty = texts.EMPTY_VALUE
    line = f"kind=meter; meter_type=unknown; apartment={empty}; property={empty}"
    assert [f"photo|{at}: {line}" in context for at in ats] == [False, *[True] * 5, False]


def test_cancelled_photo_reopens(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    _seeded(kit)
    kit.send_photo(water(960, serial=SERIAL))
    _say(kit, IntentResult(intent="reject"), "בטל")
    assert kit.store.pending(USER.email) == []
    result = _reopen(kit, ("kind", "meter"))
    assert result.snapshot.card["kind"] == "reading"
    kit.choose("confirm")
    saved = kit.readings(SERIAL)[-1]
    assert saved["value"] == 960
    assert "failed_photos" not in saved


def test_an_unfitting_kind_for_a_dropped_photo_asks_naming_how_it_was_read(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    _seeded(kit)
    kit.send_photo(water(960, serial=SERIAL))
    _say(kit, IntentResult(intent="reject"), "בטל")
    result = _reopen(kit, ("kind", "video"))
    meter = texts.field_value("photo_kind", "meter")
    assert [r.text for r in result.replies] == [_asked("photo", "kind", meter)]


def test_misread_retired_serial_reopens(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    apartment = _seeded(kit)
    kit.meter(apartment, "OLD-1", "water", [reading("2026-04-01", 500)], retired_at=FIRST)
    result = kit.send_photo(water(960, serial="OLD-1"))
    assert result.replies[-1].text == texts.retired_meter("OLD-1", SERIAL)
    key = _photo_message(kit)["image_key"]
    result = _reopen(kit, ("serial", SERIAL))
    assert result.snapshot.card["kind"] == "reading"
    kit.choose("confirm")
    saved = kit.readings(SERIAL)[-1]
    assert (saved["value"], saved["failed_photos"]) == (960, [key])
    assert [r["value"] for r in kit.readings("OLD-1")] == [500]


def test_statement_read_as_meter_reopens_as_statement(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    _seeded(kit)
    read_as_meter = NOT_A_METER.model_copy(update={"serial": "12345", "candidates": []})
    result = kit.send_photo(read_as_meter, as_statement=statement(0.7, 16.5))
    assert result.replies[-1].text == texts.not_a_meter()
    key = _photo_message(kit)["image_key"]
    result = _reopen(kit, ("kind", "statement"))
    assert result.snapshot.card["kind"] == "tariffs"
    kit.choose("confirm")
    saved = [t for t in kit.tariffs() if t["source_image_key"] == key]
    assert [(t["electricity_per_kwh"], t["water_per_m3"]) for t in saved] == [(0.7, 16.5)]


def test_a_statement_forced_on_a_photo_read_as_none_shows_its_card(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    _seeded(kit)
    kit.send_photo(UNKNOWN, as_statement=NOT_STATEMENT)
    result = _reopen(kit, ("kind", "statement"))
    assert result.snapshot.card["kind"] == "tariffs"


def test_meter_read_as_statement_reopens_as_meter(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    _seeded(kit)
    assert kit.send_statement().snapshot.card["kind"] == "tariffs"
    key = _photo_message(kit)["image_key"]
    result = _reopen(kit, ("kind", "meter"), ("serial", SERIAL), ("value", "960"))
    assert result.snapshot.card["kind"] == "reading"
    assert [i["kind"] for i in kit.store.pending(USER.email)] == ["meter_photo"]
    kit.choose("confirm")
    saved = kit.readings(SERIAL)[-1]
    assert (saved["value"], saved["failed_photos"]) == (960, [key])
    assert [t["effective_date"] for t in kit.tariffs()] == ["2026-01-01"]


def test_a_meter_forced_on_a_photo_with_no_type_asks_its_type(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    _seeded(kit)
    kit.send_photo(NOT_A_METER.model_copy(update={"serial": "W-9"}))
    result = _reopen(kit, ("kind", "meter"))
    assert result.snapshot.card["kind"] == "unreadable"
    assert texts.UNREADABLE_TYPE in _text(result.snapshot.card)


def test_an_invalid_value_on_a_dropped_photo_asks_its_question_and_reads_nothing(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    _seeded(kit)
    kit.send_photo(NOT_A_METER)
    result = _reopen(kit, ("value", "abc"))
    assert [r.text for r in result.replies] == [_asked("card", "value", texts.EMPTY_VALUE)]
    assert [i["kind"] for i in kit.store.pending(USER.email)] == ["question"]
    assert _photo_message(kit)["reading"]["state"] == "dropped"


def test_a_reopened_photo_a_retake_replaces_is_listed_once_among_failed_photos(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    _seeded(kit)
    kit.send_photo(NOT_A_METER)
    key = _photo_message(kit)["image_key"]
    assert _reopen(kit, ("kind", "meter")).snapshot.card["kind"] == "unreadable"
    kit.send_photo(water(960, serial=SERIAL))
    kit.choose("confirm")
    assert kit.readings(SERIAL)[-1]["failed_photos"] == [key]


def _listed_photo(kit, at) -> bool:
    """Whether the context of the user's next message lists the photo message stamped at."""
    _say(kit, IntentResult(intent="unknown"))
    _, context = kit.model.classified[-1]
    return f"photo|{at}:" in context


def _reopen_at(kit, at, *changes):
    return _say(kit, edits(*changes, target=f"photo|{at}"))


def test_a_photo_a_retake_replaced_is_not_listed_and_is_not_read_again(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    _seeded(kit)
    kit.send_photo(water(960, serial=None).model_copy(update={"serial_unreadable": True}))
    [replaced] = kit.store.photo_messages(USER.email)
    kit.send_photo(water(960, serial=SERIAL))
    kit.choose("confirm")
    assert kit.readings(SERIAL)[-1]["failed_photos"] == [replaced["image_key"]]
    assert not _listed_photo(kit, replaced["at"])
    result = _reopen_at(kit, replaced["at"], ("serial", SERIAL))
    assert [r.text for r in result.replies] == [texts.photo_not_reopenable()]
    assert kit.store.pending(USER.email) == []
    assert len(kit.readings(SERIAL)) == 5


def test_a_statement_whose_tariff_was_saved_is_not_listed_and_is_not_read_again(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    _seeded(kit)
    kit.send_statement()
    at = _photo_message(kit)["at"]
    assert _listed_photo(kit, at)
    kit.choose("confirm")
    assert not _listed_photo(kit, at)
    result = _reopen_at(kit, at, ("kind", "meter"), ("serial", SERIAL))
    assert [r.text for r in result.replies] == [texts.photo_not_reopenable()]
    assert kit.store.pending(USER.email) == []


def test_a_dropped_photo_of_a_previous_account_is_not_listed_and_is_not_read_again(env, ddb):
    kit = Kit(today=date(2026, 8, 20))
    _seeded(kit)
    at = "2026-08-01T08:00:00.000000_ab12"
    tag = {"state": "dropped", "meter_type": "unknown", "apartment": None, "property": None}
    kit.store.append_message(
        USER.email,
        {
            "at": at,
            "role": "user",
            "text": None,
            "card": None,
            "image_key": "earlier@x.com/images/2026-08-01T08:00:00.000000_ab12.jpg",
            "pdf_key": None,
            "choice": None,
            "reading": tag,
        },
    )
    assert not _listed_photo(kit, at)
    result = _reopen_at(kit, at, ("serial", SERIAL))
    assert [r.text for r in result.replies] == [texts.photo_not_reopenable()]
    assert kit.store.pending(USER.email) == []
