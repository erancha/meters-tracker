"""A photo of a known meter: the reading card and its confirmation, duplicates, backdating,
lower readings, unreadable digits, typed corrections, the photo queue, the reading time
limit, and the photos a saved reading records as failed."""

from datetime import timedelta

import pytest
from engine_kit import ACCOUNT, USER, Kit, candidate, edit, electricity, options, reading, water

from common import dates, texts
from common.engine import AlreadyHandled, MessageFailed
from common.flows import READING, card_text, new_item
from common.model import IntentResult, MeterReadingResult, ModelRefused


def _electric_apartment(kit, display_choice):
    apartment = kit.apartment(start="2026-10-01")
    kit.meter(
        apartment,
        "E-392",
        "electricity",
        [reading("2026-10-01", 392000, opens="2026-10-01")],
        display_choice=display_choice,
    )
    return apartment


def _water_apartment(kit, readings):
    apartment = kit.apartment(start="2026-10-01")
    kit.meter(apartment, "W-1250", "water", readings)
    return apartment


def test_known_meter_shows_reading_card_with_saved_choice(env, ddb):
    kit = Kit()
    _electric_apartment(kit, display_choice=1)
    result = kit.send_photo(electricity())
    card = result.replies[-1].card
    assert card["kind"] == "reading"
    assert options(card) == ["confirm"]
    text = card_text(card)
    assert "392,693" in text and "392,691" not in text
    assert "עורף" in text
    assert result.snapshot.card == card


def test_confirm_saves_reading_and_replies_summary(env, ddb):
    kit = Kit()
    _electric_apartment(kit, display_choice=0)
    kit.send_photo(electricity())
    result = kit.choose("confirm")
    saved = kit.readings("E-392")[-1]
    assert (saved["value"], saved["date"], saved["recorded_by"]) == (392691, "2026-10-29", ACCOUNT)
    assert "עורף" in result.replies[-1].text
    assert "צריכה מאז הקריאה הקודמת: 691" in result.replies[-1].text
    assert result.snapshot.card is None
    assert kit.store.pending(USER.email) == []


def test_not_a_meter_leaves_no_pending(env, ddb):
    kit = Kit()
    not_a_meter = MeterReadingResult(
        meter_type="unknown",
        serial=None,
        serial_unreadable=False,
        candidates=[],
        handwritten_label=None,
    )
    result = kit.send_photo(not_a_meter)
    assert [r.text for r in result.replies] == [texts.not_a_meter()]
    assert kit.store.pending(USER.email) == []
    assert result.snapshot.card is None


def test_duplicate_same_date_is_offered_not_saved(env, ddb):
    kit = Kit([IntentResult(intent="reject")])
    apartment = _electric_apartment(kit, display_choice=0)
    kit.store.put_reading(ACCOUNT, "E-392", reading("2026-10-29", 392691))
    kit.send_photo(electricity())
    result = kit.choose("confirm")
    card = result.snapshot.card
    assert card["kind"] == "duplicate"
    assert "392,691" in result.replies[-1].text
    assert options(card) == ["confirm"]
    assert len(kit.readings("E-392")) == 2
    kit.say("לא")
    assert len(kit.readings("E-392")) == 2
    assert kit.store.pending(USER.email) == []
    assert kit.periods(apartment)[0]["closed_on"] is None


def test_backdated_reading_inserts_by_date(env, ddb):
    kit = Kit([edit("date", "2026-10-10")])
    _water_apartment(
        kit, [reading("2026-10-01", 1000, opens="2026-10-01"), reading("2026-10-20", 1200)]
    )
    kit.send_photo(water(1100), note="צולם ב-10/10")
    assert "10/10/2026" in "\n".join(kit.card()["lines"])
    kit.choose("confirm")
    assert [(r["date"], r["value"]) for r in kit.readings("W-1250")] == [
        ("2026-10-01", 1000),
        ("2026-10-10", 1100),
        ("2026-10-20", 1200),
    ]


def test_a_note_is_classified_with_the_date_the_photo_would_take(env, ddb):
    kit = Kit([edit("date", "2026-10-10")])
    _water_apartment(kit, [reading("2026-10-01", 1000, opens="2026-10-01")])
    kit.send_photo(water(1100), note="צולם ב-10/10")
    assert kit.model.classified == [("צולם ב-10/10", f"{texts.FORM_TITLE}:\ndate: 2026-10-29")]


def test_a_note_that_is_only_a_date_dates_the_reading(env, ddb):
    kit = Kit([edit(None, "2026-10-10")])
    _water_apartment(kit, [reading("2026-10-01", 1000, opens="2026-10-01")])
    kit.send_photo(water(1100), note="10/10")
    assert "10/10/2026" in card_text(kit.card())


def test_a_note_naming_a_day_after_the_photo_keeps_the_day_it_was_sent(env, ddb):
    kit = Kit([edit("date", "2026-10-30")])
    _water_apartment(kit, [reading("2026-10-01", 1000, opens="2026-10-01")])
    kit.send_photo(water(1100), note="צולם מחר")
    assert "29/10/2026" in card_text(kit.card())


def test_a_note_naming_no_date_keeps_the_day_it_was_sent(env, ddb):
    kit = Kit([edit("serial", "W-1250")])
    _water_apartment(kit, [reading("2026-10-01", 1000, opens="2026-10-01")])
    kit.send_photo(water(1100), note="המונה של עורף")
    assert "29/10/2026" in card_text(kit.card())


def test_backdated_before_closing_offers_swap(env, ddb):
    kit = Kit([edit("date", "2026-10-01")])
    apartment = kit.apartment(start="2026-08-01", closed_on="2026-10-05")
    kit.period(apartment, "2026-10-06")
    kit.meter(
        apartment,
        "W-1250",
        "water",
        [
            reading("2026-08-01", 1000, opens="2026-08-01"),
            reading("2026-10-05", 1300, opens="2026-10-06", closes="2026-08-01"),
        ],
    )
    kit.send_photo(water(1250), note="צולם ב-1/10")
    result = kit.choose("confirm")
    assert result.snapshot.card["kind"] == "swap"
    assert options(result.snapshot.card) == ["reclose", "keep_open"]
    assert result.snapshot.card["default"] == "reclose"
    assert len(kit.readings("W-1250")) == 2
    kit.choose("reclose")
    assert [(r["date"], r["opens_period"], r["closes_period"]) for r in kit.readings("W-1250")] == [
        ("2026-08-01", "2026-08-01", None),
        ("2026-10-01", "2026-10-02", "2026-08-01"),
        ("2026-10-05", None, None),
    ]
    assert [(p["start"], p["closed_on"]) for p in kit.periods(apartment)] == [
        ("2026-08-01", "2026-10-01"),
        ("2026-10-02", None),
    ]


def test_retired_serial_is_refused(env, ddb):
    kit = Kit()
    apartment = kit.apartment()
    kit.meter(
        apartment,
        "W-0999",
        "water",
        [reading("2026-09-01", 900, opens="2026-09-01")],
        retired_at="2026-10-01",
    )
    kit.meter(apartment, "W-1250", "water", [reading("2026-10-01", 1000, opens="2026-09-01")])
    result = kit.send_photo(water(950, serial="W-0999"))
    assert [r.text for r in result.replies] == [texts.retired_meter("W-0999", "W-1250")]
    assert kit.store.pending(USER.email) == []
    assert len(kit.readings("W-0999")) == 1


def test_lower_reading_asks_misread_or_replaced(env, ddb):
    kit = Kit()
    _water_apartment(kit, [reading("2026-10-01", 1300, opens="2026-10-01")])
    kit.send_photo(water(1250))
    result = kit.choose("confirm")
    assert result.snapshot.card["kind"] == "lower_reading"
    assert options(result.snapshot.card) == ["misread", "replaced", "reset"]
    assert result.snapshot.card["default"] == "misread"
    assert len(kit.readings("W-1250")) == 1
    kit.choose("reset")
    saved = kit.readings("W-1250")[-1]
    assert (saved["value"], saved["resets"]) == (1250, True)


def test_misread_keeps_display_choice_unsaved(env, ddb):
    kit = Kit()
    apartment = kit.apartment(start="2026-10-01")
    kit.meter(
        apartment, "E-392", "electricity", [reading("2026-10-01", 392700, opens="2026-10-01")]
    )
    kit.send_photo(electricity())
    kit.choose("top")
    result = kit.choose("misread")
    assert result.snapshot.card["kind"] == "unreadable"
    assert kit.store.meter(ACCOUNT, "E-392")["display_choice"] is None


def test_model_failure_leaves_no_pending(env, ddb):
    kit = Kit()

    def refuse(image, media_type):
        raise ModelRefused("refused")

    kit.model.read_meter = refuse
    with pytest.raises(MessageFailed):
        kit.send_photo(water())
    assert kit.store.pending(USER.email) == []


def test_retake_replaces_the_unreadable_card(env, ddb):
    kit = Kit()
    _water_apartment(kit, [reading("2026-10-01", 1000, opens="2026-10-01")])
    kit.send_photo(water(None, unreadable=True))
    result = kit.send_photo(water(1250))
    assert result.snapshot.card["kind"] == "reading"
    assert result.snapshot.waiting == 0
    assert len(kit.store.pending(USER.email)) == 1


def test_two_photos_show_oldest_first_and_confirm_pulls_next(env, ddb):
    kit = Kit()
    apartment = _electric_apartment(kit, display_choice=0)
    kit.meter(apartment, "W-1250", "water", [reading("2026-10-01", 1000, opens="2026-10-01")])
    first = kit.send_photo(electricity())
    second = kit.send_photo(water())
    assert second.snapshot.card == first.snapshot.card
    assert second.snapshot.waiting == 1
    result = kit.choose("confirm")
    assert result.snapshot.card == second.replies[-1].card
    assert result.snapshot.waiting == 0


def test_unreadable_digit_asks_for_retake_or_value(env, ddb):
    kit = Kit([edit("value", "1251")])
    _water_apartment(kit, [reading("2026-10-01", 1000, opens="2026-10-01")])
    result = kit.send_photo(water(None, unreadable=True))
    assert result.snapshot.card["kind"] == "unreadable"
    assert options(result.snapshot.card) == []
    result = kit.say("1251")
    assert result.snapshot.card["kind"] == "reading"
    kit.choose("confirm")
    assert kit.readings("W-1250")[-1]["value"] == 1251


def test_second_confirm_of_a_reading_card_does_not_answer_the_duplicate_card(env, ddb):
    kit = Kit()
    _electric_apartment(kit, display_choice=0)
    kit.store.put_reading(ACCOUNT, "E-392", reading("2026-10-29", 392000))
    kit.send_photo(electricity())
    reading_card = kit.card()
    kit.choose_on(reading_card, "confirm")
    duplicate_card = kit.card()
    assert duplicate_card["kind"] == "duplicate"
    assert duplicate_card["version"] != reading_card["version"]
    with pytest.raises(AlreadyHandled):
        kit.choose_on(reading_card, "confirm")
    assert [(r["date"], r["value"], r["image_key"]) for r in kit.readings("E-392")] == [
        ("2026-10-01", 392000, "k"),
        ("2026-10-29", 392000, "k"),
    ]
    assert kit.card() == duplicate_card


def test_option_not_on_the_current_card_is_already_handled(env, ddb):
    kit = Kit()
    _electric_apartment(kit, display_choice=0)
    kit.send_photo(electricity())
    card = kit.card()
    with pytest.raises(AlreadyHandled):
        kit.choose_on(card, "reset")
    assert kit.card() == card
    assert len(kit.readings("E-392")) == 1


def _photo_being_read(kit, seconds_ago):
    started = dates.now() - timedelta(seconds=seconds_ago)
    seq = "2026-10-29T08:00:00.000000_aaaa"
    kit.store.append_message(USER.email, {"at": seq, "role": "user", "text": None})
    data = {
        "image_key": "owner@x.com/images/a.jpg",
        "message_at": seq,
        "read_started": started.isoformat(),
        "failed_photos": [],
    }
    kit.store.put_pending(USER.email, new_item(seq, "meter_photo", READING, None, data))


def test_photo_still_within_the_lambda_timeout_stays_in_the_queue(env, ddb):
    kit = Kit()
    _photo_being_read(kit, seconds_ago=kit.engine.lambda_timeout.total_seconds() - 5)
    snapshot = kit.engine.snapshot(USER)
    assert (snapshot.card, snapshot.waiting) == (None, 1)


def test_photo_read_past_the_lambda_timeout_asks_for_a_retake(env, ddb):
    kit = Kit()
    _photo_being_read(kit, seconds_ago=kit.engine.lambda_timeout.total_seconds() + 1)
    card = kit.card()
    assert (card["kind"], options(card)) == ("unreadable", [])
    assert card["title"] == texts.unreadable([texts.UNREADABLE_PHOTO])[0]
    _electric_apartment(kit, display_choice=0)
    kit.send_photo(electricity())
    assert kit.card()["kind"] == "reading"
    assert len(kit.store.pending(USER.email)) == 1


def test_typed_correction_on_a_reading_card_replaces_the_value(env, ddb):
    kit = Kit([edit("value", "392650")])
    _electric_apartment(kit, display_choice=0)
    kit.send_photo(electricity())
    result = kit.say("הקריאה היא 392650")
    card = result.snapshot.card
    assert (card["kind"], options(card)) == ("reading", ["confirm"])
    assert "392,650" in card_text(card)
    kit.choose("confirm")
    assert kit.readings("E-392")[-1]["value"] == 392650


def test_retake_after_unreadable_is_recorded_on_the_saved_reading(env, ddb):
    kit = Kit()
    _water_apartment(kit, [reading("2026-10-01", 1000, opens="2026-10-01")])
    kit.send_photo(water(None, unreadable=True))
    kit.send_photo(water(1250))
    kit.choose("confirm")
    saved = kit.readings("W-1250")[-1]
    assert saved["failed_photos"] == kit.photo_keys()[:1]
    assert saved["model"] == "anthropic:claude-opus-5-5"


def test_two_failures_then_success_keep_both_keys_in_order(env, ddb):
    kit = Kit()
    _water_apartment(kit, [reading("2026-10-01", 1000, opens="2026-10-01")])
    kit.send_photo(water(None, unreadable=True))
    kit.send_photo(water(None, unreadable=True))
    kit.send_photo(water(1250))
    kit.choose("confirm")
    assert kit.readings("W-1250")[-1]["failed_photos"] == kit.photo_keys()[:2]


def test_a_crash_while_absorbing_a_card_keeps_its_photo_on_the_new_item(env, ddb, monkeypatch):
    kit = Kit()
    _water_apartment(kit, [reading("2026-10-01", 1000, opens="2026-10-01")])
    kit.send_photo(water(None, unreadable=True))
    [first] = kit.store.pending(USER.email)

    def crash(user, seq):
        raise RuntimeError("crash")

    monkeypatch.setattr(kit.store, "delete_pending", crash)
    with pytest.raises(MessageFailed):
        kit.send_photo(water(1250))
    [newer] = [i for i in kit.store.pending(USER.email) if i["seq"] != first["seq"]]
    assert newer["data"]["failed_photos"] == [first["data"]["image_key"]]


def test_reading_confirmed_as_proposed_carries_no_record(env, ddb):
    kit = Kit()
    _water_apartment(kit, [reading("2026-10-01", 1000, opens="2026-10-01")])
    kit.send_photo(water(1250))
    kit.choose("confirm")
    saved = kit.readings("W-1250")[-1]
    assert "failed_photos" not in saved and "model" not in saved


def test_typed_value_marks_the_saved_photo_as_failed(env, ddb):
    kit = Kit([edit("value", "1260")])
    _water_apartment(kit, [reading("2026-10-01", 1000, opens="2026-10-01")])
    kit.send_photo(water(1250))
    kit.say("הקריאה היא 1260")
    kit.choose("confirm")
    saved = kit.readings("W-1250")[-1]
    assert saved["failed_photos"] == [saved["image_key"]]
    assert saved["model"] == "anthropic:claude-opus-5-5"


def test_choosing_the_other_display_number_is_not_a_failure(env, ddb):
    kit = Kit()
    _electric_apartment(kit, display_choice=None)
    kit.send_photo(electricity())
    kit.choose("bottom")
    assert "failed_photos" not in kit.readings("E-392")[-1]


def test_unreadable_card_of_another_serial_stays_out_of_the_chain(env, ddb):
    kit = Kit()
    apartment = _water_apartment(kit, [reading("2026-10-01", 1000, opens="2026-10-01")])
    kit.meter(apartment, "W-2", "water", [reading("2026-10-01", 500, opens="2026-10-01")])
    kit.send_photo(water(None, serial="W-2", unreadable=True))
    result = kit.send_photo(water(1250))
    kit.choose_on(result.replies[-1].card, "confirm")
    assert "failed_photos" not in kit.readings("W-1250")[-1]
    assert len(kit.store.pending(USER.email)) == 1


def test_duplicate_replace_saves_the_chain(env, ddb):
    kit = Kit()
    _water_apartment(kit, [reading("2026-10-29", 1200, opens="2026-10-01")])
    kit.send_photo(water(None, unreadable=True))
    kit.send_photo(water(1250))
    kit.choose("confirm")
    kit.choose("confirm")
    replaced = kit.readings("W-1250")[-1]
    assert replaced["value"] == 1250
    assert replaced["failed_photos"] == kit.photo_keys()[:1]


def test_clean_duplicate_replace_clears_the_replaced_record(env, ddb):
    kit = Kit()
    _water_apartment(kit, [reading("2026-10-01", 1000, opens="2026-10-01")])
    kit.send_photo(water(None, unreadable=True))
    kit.send_photo(water(1250))
    kit.choose("confirm")
    assert "failed_photos" in kit.readings("W-1250")[-1]
    kit.send_photo(water(1251))
    kit.choose("confirm")
    kit.choose("confirm")
    replaced = kit.readings("W-1250")[-1]
    assert replaced["value"] == 1251
    assert "failed_photos" not in replaced and "model" not in replaced


def test_choice_message_names_the_card_it_answered(env, ddb):
    kit = Kit([IntentResult(intent="unknown")])
    _electric_apartment(kit, display_choice=0)
    kit.send_photo(electricity())
    card = kit.card()
    kit.choose("confirm")
    kit.say("שלום")
    user_messages = [m for m in kit.engine.load(USER)[0] if m["role"] == "user"]
    assert [m["choice"] for m in user_messages] == [
        None,
        {"pending": card["pending"], "version": card["version"]},
        None,
    ]


def test_typed_serial_on_unreadable_card_moves_the_photo_to_that_serial(env, ddb):
    kit = Kit([edit("serial", "2721058")])
    unreadable_serial = MeterReadingResult(
        meter_type="electricity",
        serial=None,
        serial_unreadable=True,
        candidates=[candidate(392691, "top", "kWh")],
        handwritten_label=None,
    )
    result = kit.send_photo(unreadable_serial)
    assert result.snapshot.card["kind"] == "unreadable"
    card = kit.say("המספר הסידורי הוא 2721058").snapshot.card
    assert card["kind"] == "property"
    assert "2721058" in card_text(card)


def test_a_retired_meter_with_no_live_meter_of_its_type_is_refused_naming_none(env, ddb):
    kit = Kit()
    apartment = kit.apartment()
    rows = [reading("2026-09-01", 1000, opens="2026-09-01")]
    kit.meter(apartment, "W-1", "water", rows, retired_at="2026-09-10")
    result = kit.send_photo(water(1300, serial="W-1"))
    assert [r.text for r in result.replies] == [texts.retired_meter_alone("W-1")]
    assert result.snapshot.card is None
    assert len(kit.readings("W-1")) == 1
