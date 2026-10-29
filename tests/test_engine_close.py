"""Closing a rental period: the close proposal after the end date, closing, reclosing and
vacating, opening the next period, moving a period's start, and the once-a-day greeting."""

from datetime import date

import pytest
from engine_kit import ACCOUNT, USER, Kit, electricity, options, reading, water

from common import texts
from common.engine import AlreadyHandled
from common.flows import card_text
from common.model import IntentResult

START = "2026-09-01"


def _apartment(kit, water_readings=()):
    apartment = kit.apartment(start=START)
    kit.meter(
        apartment, "E-392", "electricity", [reading(START, 392000, opens=START)], display_choice=0
    )
    kit.meter(apartment, "W-1250", "water", [reading(START, 1000, opens=START), *water_readings])
    return apartment


def _flags(kit, serial):
    return [(r["date"], r["opens_period"], r["closes_period"]) for r in kit.readings(serial)]


def test_reading_past_end_proposes_close(env, ddb):
    kit = Kit(today=date(2026, 11, 2))
    _apartment(kit)
    kit.send_photo(electricity())
    result = kit.choose("confirm")
    card = result.snapshot.card
    assert card["kind"] == "propose_close"
    assert options(card) == ["close", "keep_open", "vacate"]
    assert "31/10/2026" in result.replies[-1].text
    assert len(kit.readings("E-392")) == 1


def test_close_flags_readings_and_reopens_next_day(env, ddb):
    kit = Kit(today=date(2026, 11, 2))
    apartment = _apartment(kit)
    kit.send_photo(electricity())
    kit.choose("confirm")
    result = kit.choose("close")
    assert "W-1250" in result.replies[-1].text
    assert result.snapshot.card is None
    assert len(kit.store.pending(USER.email)) == 1
    kit.send_photo(water())
    result = kit.choose("confirm")
    card = result.snapshot.card
    assert card["kind"] == "close"
    assert "E-392" in card_text(card) and "W-1250" in card_text(card)
    assert _flags(kit, "E-392")[-1] == ("2026-11-02", None, None)
    result = kit.choose("confirm")
    assert result.snapshot.card is None
    assert kit.store.pending(USER.email) == []
    assert _flags(kit, "E-392")[-1] == ("2026-11-02", "2026-11-03", START)
    assert _flags(kit, "W-1250")[-1] == ("2026-11-02", "2026-11-03", START)
    assert [(p["start"], p["months"], p["closed_on"]) for p in kit.periods(apartment)] == [
        (START, 2, "2026-11-02"),
        ("2026-11-03", 2, None),
    ]


def test_vacate_closes_without_reopen_and_next_photo_opens_period(env, ddb):
    kit = Kit(today=date(2026, 11, 2))
    apartment = _apartment(kit, [reading("2026-10-15", 1100)])
    kit.send_photo(electricity())
    kit.choose("confirm")
    result = kit.choose("vacate")
    card = result.snapshot.card
    assert (card["kind"], options(card)) == ("close", ["vacate", "reject"])
    assert "1,100" in card_text(card)
    assert kit.periods(apartment)[0]["closed_on"] is None
    kit.choose("vacate")
    assert [(p["start"], p["closed_on"]) for p in kit.periods(apartment)] == [(START, "2026-11-02")]
    assert _flags(kit, "E-392")[-1] == ("2026-11-02", None, START)
    assert _flags(kit, "W-1250")[-1] == ("2026-10-15", None, START)
    kit.today = date(2026, 12, 1)
    kit.send_photo(electricity(top=392800))
    result = kit.choose("confirm")
    assert result.snapshot.card["kind"] == "open_period"
    kit.choose("confirm")
    assert [(p["start"], p["closed_on"]) for p in kit.periods(apartment)] == [
        (START, "2026-11-02"),
        ("2026-12-01", None),
    ]
    assert _flags(kit, "E-392")[-1] == ("2026-12-01", "2026-12-01", None)
    kit.send_photo(water(1300))
    kit.choose("confirm")
    assert _flags(kit, "W-1250")[-1] == ("2026-12-01", "2026-12-01", None)
    assert kit.store.pending(USER.email) == []


def test_close_period_intent_with_missing_meter_asks_for_photo(env, ddb):
    kit = Kit([IntentResult(intent="close_period")], today=date(2026, 10, 20))
    apartment = _apartment(kit)
    kit.store.put_reading(ACCOUNT, "E-392", reading("2026-10-15", 392500))
    result = kit.say("סגור את התקופה")
    assert "W-1250" in result.replies[-1].text
    assert result.snapshot.card is None
    kit.send_photo(water(1100))
    result = kit.choose("confirm")
    assert result.snapshot.card["kind"] == "close"
    assert options(result.snapshot.card) == ["confirm", "vacate", "reject"]
    kit.choose("confirm")
    assert _flags(kit, "E-392")[-1] == ("2026-10-15", "2026-10-21", START)
    assert _flags(kit, "W-1250")[-1] == ("2026-10-20", "2026-10-21", START)
    assert [(p["start"], p["closed_on"]) for p in kit.periods(apartment)] == [
        (START, "2026-10-20"),
        ("2026-10-21", None),
    ]


def test_reclose_within_window_replaces_closing_reading(env, ddb):
    kit = Kit(today=date(2026, 11, 4))
    apartment = kit.apartment(start=START, closed_on="2026-10-31")
    kit.period(apartment, "2026-11-01")
    kit.meter(
        apartment,
        "E-392",
        "electricity",
        [
            reading(START, 392000, opens=START),
            reading("2026-10-31", 392600, opens="2026-11-01", closes=START),
        ],
        display_choice=0,
    )
    kit.send_photo(electricity())
    result = kit.choose("confirm")
    assert result.snapshot.card["kind"] == "propose_reclose"
    assert options(result.snapshot.card) == ["reclose", "keep_open"]
    assert "31/10/2026" in result.replies[-1].text
    kit.choose("reclose")
    assert _flags(kit, "E-392") == [
        (START, START, None),
        ("2026-10-31", None, None),
        ("2026-11-04", "2026-11-05", START),
    ]
    assert [(p["start"], p["closed_on"]) for p in kit.periods(apartment)] == [
        (START, "2026-11-04"),
        ("2026-11-05", None),
    ]


def test_vacated_apartment_photo_within_window_proposes_opening(env, ddb):
    kit = Kit(today=date(2026, 11, 5))
    apartment = kit.apartment(start=START, closed_on="2026-11-02")
    kit.meter(
        apartment,
        "E-392",
        "electricity",
        [reading(START, 392000, opens=START), reading("2026-11-02", 392600, closes=START)],
        display_choice=0,
    )
    kit.send_photo(electricity())
    result = kit.choose("confirm")
    assert result.snapshot.card["kind"] == "open_period"
    kit.choose("confirm")
    assert _flags(kit, "E-392")[-1] == ("2026-11-05", "2026-11-05", None)
    assert [(p["start"], p["closed_on"]) for p in kit.periods(apartment)] == [
        (START, "2026-11-02"),
        ("2026-11-05", None),
    ]


def test_change_period_intent_opens_vacant_apartment_on_date(env, ddb):
    kit = Kit(
        [IntentResult(intent="change_period", name="עורף", date="2026-12-15")],
        today=date(2026, 12, 1),
    )
    apartment = kit.apartment(start=START, closed_on="2026-11-02")
    result = kit.say("השוכר החדש מ-15/12")
    card = result.snapshot.card
    assert (card["kind"], options(card)) == ("change_period", ["confirm", "reject"])
    assert "15/12/2026" in card_text(card)
    assert len(kit.periods(apartment)) == 1
    result = kit.choose("confirm")
    assert "15/12/2026" in result.replies[-1].text
    assert [(p["start"], p["months"], p["closed_on"]) for p in kit.periods(apartment)] == [
        (START, 2, "2026-11-02"),
        ("2026-12-15", 2, None),
    ]


def test_change_period_intent_moves_open_period_start(env, ddb):
    kit = Kit([IntentResult(intent="change_period", name="עורף", date="2026-09-15", months=3)])
    apartment = _apartment(kit)
    result = kit.say("השוכר החדש מ-15/9, לשלושה חודשים")
    card = result.snapshot.card
    assert card["kind"] == "change_period"
    assert "E-392" in card_text(card) and "W-1250" in card_text(card)
    assert [p["start"] for p in kit.periods(apartment)] == [START]
    assert _flags(kit, "E-392") == [(START, START, None)]
    kit.choose("confirm")
    assert [(p["start"], p["months"]) for p in kit.periods(apartment)] == [("2026-09-15", 3)]
    assert _flags(kit, "E-392") == [(START, "2026-09-15", None)]
    assert _flags(kit, "W-1250") == [(START, "2026-09-15", None)]


def test_load_greets_once_a_day_for_apartments_past_end(env, ddb):
    kit = Kit(today=date(2026, 10, 20))
    _apartment(kit)
    assert kit.engine.load(USER)[0] == []
    kit.today = date(2026, 11, 2)
    messages, snapshot = kit.engine.load(USER)
    assert [m["text"] for m in messages] == [texts.greeting([("עורף", date(2026, 10, 31))])]
    assert snapshot.card is None
    assert len(kit.engine.load(USER)[0]) == 1
    kit.today = date(2026, 11, 3)
    assert len(kit.engine.load(USER)[0]) == 2


def test_second_confirm_on_a_vacant_apartment_photo_opens_no_period(env, ddb):
    kit = Kit(today=date(2026, 11, 5))
    apartment = kit.apartment(start=START, closed_on="2026-11-02")
    kit.meter(
        apartment,
        "E-392",
        "electricity",
        [reading(START, 392000, opens=START), reading("2026-11-02", 392600, closes=START)],
        display_choice=0,
    )
    kit.send_photo(electricity())
    reading_card = kit.card()
    kit.choose_on(reading_card, "confirm")
    assert kit.card()["kind"] == "open_period"
    with pytest.raises(AlreadyHandled):
        kit.choose_on(reading_card, "confirm")
    assert [(p["start"], p["closed_on"]) for p in kit.periods(apartment)] == [(START, "2026-11-02")]
    assert len(kit.readings("E-392")) == 2


def test_asking_again_for_a_parked_close_offers_to_cancel_it(env, ddb):
    close = IntentResult(intent="close_period")
    kit = Kit([close, close], today=date(2026, 10, 20))
    apartment = _apartment(kit)
    kit.store.put_reading(ACCOUNT, "E-392", reading("2026-10-15", 392500))
    kit.say("סגור את התקופה")
    assert kit.card() is None
    result = kit.say("סגור את התקופה")
    card = result.snapshot.card
    assert (card["kind"], options(card)) == ("close_waiting", ["reject"])
    assert "W-1250" in card_text(card)
    result = kit.choose("reject")
    assert result.replies[-1].text == texts.close_cancelled()
    assert kit.store.pending(USER.email) == []
    assert [p["closed_on"] for p in kit.periods(apartment)] == [None]


def test_a_photo_after_the_cancel_offer_still_resumes_the_close(env, ddb):
    close = IntentResult(intent="close_period")
    kit = Kit([close, close], today=date(2026, 10, 20))
    _apartment(kit)
    kit.store.put_reading(ACCOUNT, "E-392", reading("2026-10-15", 392500))
    kit.say("סגור את התקופה")
    kit.say("סגור את התקופה")
    result = kit.send_photo(water(1100))
    assert result.snapshot.card["kind"] == "reading"
    result = kit.choose("confirm")
    assert result.snapshot.card["kind"] == "close"
