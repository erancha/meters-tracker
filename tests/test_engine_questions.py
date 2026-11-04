"""Messages that are not yet an operation the app can apply, through the engine: a correction
missing its value asks for it and keeps the question until the answer, a how-to question gets the
field's explanation, an ambiguous message lists its candidates, a bare value with no card is asked
about, a request the app lacks says so, and anything else says what the assistant can do. No reply
says "לא הבנתי"."""

from datetime import date
from pathlib import Path

from engine_kit import ACCOUNT, USER, Kit, candidate, edit, edits, reading, water

from common import texts
from common.access import OWNER
from common.account import load
from common.fields import FIELDS
from common.flows import card_text
from common.model import IntentResult, MeterReadingResult, Operation

SERIAL = "2721056"
OPENED = "2026-10-01"
LATEST = "2026-10-20"
NAMES = ["עורף"]


def _target(day) -> str:
    return f"reading|{SERIAL}|{day}#{day}T08:00:00"


def _seeded(kit):
    apartment = kit.apartment(start=OPENED)
    kit.meter(
        apartment,
        SERIAL,
        "water",
        [reading(OPENED, 800, opens=OPENED), reading(LATEST, 850)],
    )
    return apartment


def _say(kit, intent, text="טקסט"):
    kit.model.queue(intent)
    return kit.say(text)


def _texts(result) -> list[str]:
    return [r.text for r in result.replies]


def _question_card(field, current):
    return card_text({"title": field.question.format(current=current), "lines": [field.how_to]})


def test_a_set_with_no_value_asks_the_fields_question_naming_the_current_value(env, ddb):
    kit = Kit()
    _seeded(kit)
    stored = load(kit.store, ACCOUNT, OWNER)
    result = _say(kit, edit("value", None, _target(OPENED)))
    field = FIELDS[("reading", "value")]
    assert _texts(result) == [_question_card(field, "800")]
    assert result.snapshot.card["kind"] == "question"
    assert load(kit.store, ACCOUNT, OWNER) == stored


def test_a_bare_answer_to_the_question_fills_the_asked_field(env, ddb):
    kit = Kit()
    _seeded(kit)
    _say(kit, edit("value", None, _target(OPENED)))
    result = _say(kit, edit(None, "843"), "843")
    card = result.snapshot.card
    assert card["kind"] == "correction"
    assert "843" in card_text(card)
    assert [i["kind"] for i in kit.store.pending(USER.email)] == ["correction"]
    assert kit.readings(SERIAL)[0]["value"] == 800
    kit.choose("confirm")
    assert kit.readings(SERIAL)[0]["value"] == 843


def test_the_question_comes_before_a_waiting_card_and_its_answer_never_reaches_it(env, ddb):
    kit = Kit(today=date(2026, 10, 29))
    _seeded(kit)
    waiting = kit.send_photo(water(900, serial=SERIAL)).snapshot.card
    asked = _say(kit, edit("value", None, _target(OPENED)))
    assert asked.snapshot.card["kind"] == "question"
    assert asked.snapshot.waiting == 1
    result = _say(kit, edit(None, "843"), "843")
    assert result.snapshot.card["kind"] == "correction"
    kit.choose("confirm")
    assert kit.readings(SERIAL)[0]["value"] == 843
    after = kit.card()
    assert after["kind"] == "reading" and card_text(after) == card_text(waiting)


def test_an_invalid_answer_asks_again_and_keeps_the_question(env, ddb):
    kit = Kit()
    _seeded(kit)
    _say(kit, edit("value", None, _target(OPENED)))
    result = _say(kit, edit(None, "abc"))
    field = FIELDS[("reading", "value")]
    assert _texts(result) == [_question_card(field, "800")]
    assert [i["kind"] for i in kit.store.pending(USER.email)] == ["question"]


def test_cancelling_the_question_writes_nothing(env, ddb):
    kit = Kit()
    _seeded(kit)
    stored = load(kit.store, ACCOUNT, OWNER)
    _say(kit, edit("value", None, _target(OPENED)))
    result = _say(kit, IntentResult(intent="reject"), "בטל")
    assert _texts(result) == [texts.question_cancelled()]
    assert kit.store.pending(USER.email) == []
    assert load(kit.store, ACCOUNT, OWNER) == stored


def test_a_new_question_replaces_the_one_waiting(env, ddb):
    kit = Kit()
    _seeded(kit)
    _say(kit, edit("value", None, _target(OPENED)))
    _say(kit, edit("value", None, _target(LATEST)))
    assert [i["kind"] for i in kit.store.pending(USER.email)] == ["question"]
    _say(kit, edit(None, "860"), "860")
    kit.choose("confirm")
    assert [r["value"] for r in kit.readings(SERIAL)] == [800, 860]


def test_a_set_with_no_value_on_a_card_asks_and_the_bare_answer_fills_that_field(env, ddb):
    kit = Kit(today=date(2026, 10, 29))
    _seeded(kit)
    kit.send_photo(water(900, serial=SERIAL))
    card = kit.card()
    result = _say(kit, edit("date", None))
    question = FIELDS[("card", "date")].question.format(current="29/10/2026")
    assert _texts(result) == [question, card_text(card)]
    _say(kit, edit(None, "2026-10-28"), "28/10")
    assert kit.form() == {"value": 900, "date": "2026-10-28", "serial": SERIAL}


def test_the_card_names_the_field_it_asked_for_to_the_classifier(env, ddb):
    kit = Kit(today=date(2026, 10, 29))
    _seeded(kit)
    kit.send_photo(water(900, serial=SERIAL))
    _say(kit, edit("serial", None))
    _say(kit, edit(None, "2721058"), "2721058")
    assert f"\n{texts.ASKED_TITLE}: serial" in kit.model.classified[-1][1]


def test_how_to_replies_with_the_fields_how_to(env, ddb):
    kit = Kit()
    _seeded(kit)
    how_to = Operation(action="set", target=_target(OPENED), field="date", value=None)
    result = _say(kit, IntentResult(intent="how_to", operations=[how_to]))
    assert _texts(result) == [texts.how_to_reply(FIELDS[("reading", "date")].how_to)]
    assert kit.store.pending(USER.email) == []


def test_how_to_on_a_card_explains_its_field_and_shows_the_card_again(env, ddb):
    kit = Kit(today=date(2026, 10, 29))
    _seeded(kit)
    card = kit.send_photo(water(900, serial=SERIAL)).snapshot.card
    how_to = Operation(action="set", target="card", field="serial", value=None)
    result = _say(kit, IntentResult(intent="how_to", operations=[how_to]))
    assert _texts(result) == [
        texts.how_to_reply(FIELDS[("card", "serial")].how_to),
        card_text(card),
    ]


def test_how_to_with_no_field_says_what_the_assistant_can_do(env, ddb):
    kit = Kit()
    _seeded(kit)
    result = _say(kit, IntentResult(intent="how_to"))
    assert _texts(result) == [texts.options_reply(NAMES)]


def test_which_lists_the_candidates(env, ddb):
    kit = Kit()
    _seeded(kit)
    candidates = [
        Operation(action="set", target=_target(day), field="value", value="843")
        for day in (OPENED, LATEST)
    ]
    result = _say(kit, IntentResult(intent="which", operations=candidates))
    labels = [texts.reading_label("water", SERIAL, date.fromisoformat(d)) for d in (OPENED, LATEST)]
    assert _texts(result) == [texts.which_record(labels)]
    assert kit.store.pending(USER.email) == []


def test_which_naming_an_unknown_id_says_what_the_assistant_can_do(env, ddb):
    kit = Kit()
    _seeded(kit)
    candidates = [Operation(action="set", target="meter|nope", field="type", value="water")]
    result = _say(kit, IntentResult(intent="which", operations=candidates))
    assert _texts(result) == [texts.unknown_record(NAMES)]


def test_bare_number_with_no_card_is_asked_about_and_writes_nothing(env, ddb):
    kit = Kit()
    _seeded(kit)
    stored = load(kit.store, ACCOUNT, OWNER)
    result = _say(kit, edit(None, "845"), "845")
    assert _texts(result) == [texts.bare_input("845")]
    assert result.snapshot.card is None
    assert kit.store.pending(USER.email) == []
    assert load(kit.store, ACCOUNT, OWNER) == stored


def test_unknown_on_a_card_and_with_no_card_never_says_lo_hevanti(env, ddb):
    source = Path(texts.__file__).read_text()
    assert "לא הבנתי" not in source
    kit = Kit(today=date(2026, 10, 29))
    _seeded(kit)
    alone = _say(kit, IntentResult(intent="unknown"))
    assert _texts(alone) == [texts.options_reply(NAMES)]
    card = kit.send_photo(water(900, serial=SERIAL)).snapshot.card
    on_card = _say(kit, IntentResult(intent="unknown"))
    assert _texts(on_card) == [texts.card_help(["אישור"]), card_text(card)]


def test_the_options_name_the_accounts_own_apartments(env, ddb):
    kit = Kit()
    _seeded(kit)
    assert "עורף" in texts.options_reply(NAMES)
    assert texts.options_reply([]) != texts.options_reply(NAMES)


def test_an_unknown_record_says_what_the_assistant_can_do(env, ddb):
    kit = Kit()
    _seeded(kit)
    result = _say(kit, edit("value", "943", "meter|nope"))
    assert _texts(result) == [texts.unknown_record(NAMES)]


def test_an_unknown_field_on_a_photo_says_what_a_photo_takes(env, ddb):
    kit = Kit(today=date(2026, 10, 29))
    _seeded(kit)
    kit.send_photo(_NOT_A_METER)
    at = kit.store.photo_messages(USER.email)[-1]["at"]
    result = _say(kit, edit("colour", "red", f"photo|{at}"))
    assert _texts(result) == [texts.photo_help()]


_NOT_A_METER = MeterReadingResult(
    meter_type="unknown",
    serial=None,
    serial_unreadable=False,
    serial_uncertain=False,
    candidates=[candidate(960, "single", "m3")],
    handwritten_label=None,
)


def test_an_invalid_value_on_a_dropped_photo_is_asked_and_the_answer_reads_it(env, ddb):
    kit = Kit(today=date(2026, 10, 29))
    _seeded(kit)
    kit.send_photo(_NOT_A_METER)
    at = kit.store.photo_messages(USER.email)[-1]["at"]
    asked = _say(kit, edits(("serial", SERIAL), ("value", "abc"), target=f"photo|{at}"))
    field = FIELDS[("card", "value")]
    assert _texts(asked) == [_question_card(field, texts.EMPTY_VALUE)]
    result = _say(kit, edit(None, "960"), "960")
    card = result.snapshot.card
    assert card["kind"] == "reading" and "960" in card_text(card)


def test_delegate_request_says_it_is_not_offered_yet(env, ddb):
    kit = Kit()
    _seeded(kit)
    result = _say(kit, IntentResult(intent="not_offered"), "תוסיף את דנה כמורשה")
    assert _texts(result) == [texts.not_offered(NAMES)]
    assert kit.store.pending(USER.email) == []


def test_not_offered_on_a_card_shows_the_card_again(env, ddb):
    kit = Kit(today=date(2026, 10, 29))
    _seeded(kit)
    card = kit.send_photo(water(900, serial=SERIAL)).snapshot.card
    result = _say(kit, IntentResult(intent="not_offered"))
    assert _texts(result) == [texts.not_offered(NAMES), card_text(card)]


def test_every_refusal_and_dropped_card_says_what_to_do_next():
    d = date(2026, 10, 1)
    replies = [
        texts.card_gone(),
        texts.correction_gone(),
        texts.nothing_to_correct(),
        texts.duplicate_property("לאונר 18"),
        texts.duplicate_apartment("עורף", "לאונר 18"),
        texts.same_day_clash(SERIAL, d),
        texts.serial_taken(SERIAL),
        texts.duplicate_tariff(d),
        texts.periods_overlap("עורף", d),
        texts.no_closing_reading(SERIAL, d),
        texts.single_counter_display(SERIAL),
        texts.correction_too_large(120),
        texts.unknown_apartment("מרתף", NAMES),
        texts.period_already_open("עורף"),
        texts.period_already_closed("עורף", d),
        texts.no_open_period("עורף"),
        texts.tariffs_not_missing(),
        texts.delegate_address_changed(),
    ]
    for reply in replies:
        assert "אפשר" in reply.replace("אי אפשר", ""), reply


def test_a_photo_after_a_question_drops_it_so_a_bare_value_fills_the_photos_card(env, ddb):
    kit = Kit(today=date(2026, 10, 29))
    _seeded(kit)
    _say(kit, edit("value", None, _target(OPENED)))
    shown = kit.send_photo(water(None, serial=SERIAL, unreadable=True)).snapshot
    assert shown.card["kind"] == "unreadable"
    assert [i["kind"] for i in kit.store.pending(USER.email)] == ["meter_photo"]
    result = _say(kit, edit(None, "905"), "905")
    assert result.snapshot.card["kind"] == "reading"
    assert kit.form()["value"] == 905
    assert kit.readings(SERIAL)[0]["value"] == 800


def test_a_new_correction_after_a_question_drops_it_so_a_confirm_reaches_the_correction(env, ddb):
    kit = Kit(today=date(2026, 10, 29))
    _seeded(kit)
    _say(kit, edit("value", None, _target(OPENED)))
    shown = _say(kit, edit("date", "2026-10-19", _target(LATEST))).snapshot
    assert shown.card["kind"] == "correction"
    assert [i["kind"] for i in kit.store.pending(USER.email)] == ["correction"]
    _say(kit, IntentResult(intent="confirm"), "אשר")
    assert kit.readings(SERIAL)[-1]["date"] == "2026-10-19"
    assert kit.store.pending(USER.email) == []
