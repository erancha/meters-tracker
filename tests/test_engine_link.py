"""Linking a new meter serial to a property, an apartment and a period, including replacing a
meter of the same type and two requests answering one link card."""

import pytest
from engine_kit import ACCOUNT, USER, Kit, choice, electricity, options, reading, water

from common import texts
from common.engine import AlreadyHandled, Engine
from common.model import IntentResult


def _choose_property(name, is_new):
    return IntentResult(intent="choose_property", name=name, is_new=is_new)


def _choose_apartment(name, is_new):
    return IntentResult(intent="choose_apartment", name=name, is_new=is_new)


def test_new_serial_asks_property_then_apartment_then_period(env, ddb):
    kit = Kit([_choose_property("הגפן 7", True), _choose_apartment("עורף", True)])
    result = kit.send_photo(electricity())
    assert result.snapshot.card["kind"] == "property"
    result = kit.say("הגפן 7")
    assert result.snapshot.card["kind"] == "apartment"
    result = kit.say("עורף")
    assert result.snapshot.card["kind"] == "period"
    assert options(result.snapshot.card) == ["confirm", "reject"]
    result = kit.choose("confirm")
    assert result.snapshot.card["kind"] == "link"
    assert options(result.snapshot.card) == ["top", "bottom", "confirm", "reject"]
    kit.choose("confirm")
    [prop] = kit.store.properties(ACCOUNT)
    [apartment] = kit.store.apartments(ACCOUNT)
    assert (prop["name"], apartment["name"], apartment["property_id"]) == (
        "הגפן 7",
        "עורף",
        prop["id"],
    )
    assert kit.periods(apartment) == [
        {"start": "2026-10-29", "months": 2, "closed_on": None, "last_reminded_on": None}
    ]
    meter = kit.store.meter(ACCOUNT, "E-392")
    assert (meter["apartment_id"], meter["type"], meter["display_choice"]) == (
        apartment["id"],
        "electricity",
        0,
    )


def test_second_meter_of_apartment_skips_period_question(env, ddb):
    kit = Kit([_choose_property("הגפן 7", False), _choose_apartment("עורף", False)])
    apartment = kit.apartment(start="2026-10-01")
    kit.meter(
        apartment,
        "E-392",
        "electricity",
        [reading("2026-10-01", 392000, opens="2026-10-01")],
        display_choice=0,
    )
    kit.send_photo(water())
    kit.say("הגפן 7")
    result = kit.say("עורף")
    assert result.snapshot.card["kind"] == "link"
    assert options(result.snapshot.card) == ["confirm", "reject"]
    kit.choose("confirm")
    assert kit.store.meter(ACCOUNT, "W-1250")["apartment_id"] == apartment["id"]
    assert kit.readings("W-1250")[0]["opens_period"] == "2026-10-01"
    assert [p["start"] for p in kit.periods(apartment)] == ["2026-10-01"]


def test_same_type_meter_proposes_replacement_and_retires_old(env, ddb):
    kit = Kit([_choose_property("הגפן 7", False), _choose_apartment("עורף", False)])
    apartment = kit.apartment(start="2026-10-01")
    kit.meter(apartment, "W-0999", "water", [reading("2026-10-01", 900, opens="2026-10-01")])
    kit.send_photo(water())
    kit.say("הגפן 7")
    result = kit.say("עורף")
    card = result.snapshot.card
    assert card["kind"] == "replaced"
    assert "W-0999" in "\n".join([card["title"], *card["lines"]])
    kit.choose("confirm")
    assert kit.store.meter(ACCOUNT, "W-0999")["retired_at"] == "2026-10-29"
    assert kit.store.meter(ACCOUNT, "W-1250")["retired_at"] is None
    assert kit.readings("W-1250")[0]["opens_period"] == "2026-10-01"


def test_link_saves_opening_reading_with_opens_period(env, ddb):
    kit = Kit(
        [
            _choose_property("הגפן 7", True),
            _choose_apartment("עורף", True),
            IntentResult(intent="set_period", date="2026-11-01"),
        ]
    )
    kit.send_photo(electricity())
    kit.say("הגפן 7")
    kit.say("עורף")
    result = kit.say("השכירות מתחילה ב-1/11")
    assert result.snapshot.card["kind"] == "period"
    assert "01/11/2026" in "\n".join(result.snapshot.card["lines"])
    kit.choose("confirm")
    kit.choose("bottom")
    result = kit.choose("confirm")
    [saved] = kit.readings("E-392")
    assert (saved["date"], saved["value"], saved["opens_period"]) == (
        "2026-10-29",
        392693,
        "2026-11-01",
    )
    [apartment] = kit.store.apartments(ACCOUNT)
    assert [(p["start"], p["months"]) for p in kit.periods(apartment)] == [("2026-11-01", 2)]
    assert kit.store.meter(ACCOUNT, "E-392")["display_choice"] == 1
    assert texts.no_previous_reading() in result.replies[-1].text


def test_link_after_a_retake_saves_the_chain(env, ddb):
    kit = Kit(
        [
            _choose_property("הגפן 7", True),
            _choose_apartment("עורף", True),
            IntentResult(intent="set_period", date="2026-11-01"),
        ]
    )
    kit.send_photo(water(None, serial=None, unreadable=True))
    kit.send_photo(electricity())
    kit.say("הגפן 7")
    kit.say("עורף")
    kit.say("השכירות מתחילה ב-1/11")
    kit.choose("confirm")
    kit.choose("bottom")
    kit.choose("confirm")
    [saved] = kit.readings("E-392")
    assert saved["failed_photos"] == kit.photo_keys()[:1]


class _QueueReadBefore:
    """A store whose pending queue reads as it stood before another request answered it, as
    when two requests carrying the same card read the queue before either claims the item."""

    def __init__(self, store, queue):
        self._store = store
        self._queue = queue

    def pending(self, user):
        return self._queue

    def __getattr__(self, name):
        return getattr(self._store, name)


def test_concurrent_confirms_of_one_link_card_create_one_property(env, ddb):
    kit = Kit([_choose_property("הגפן 7", True), _choose_apartment("עורף", True)])
    kit.send_photo(water())
    kit.say("הגפן 7")
    kit.say("עורף")
    kit.choose("confirm")
    card = kit.card()
    assert card["kind"] == "link"
    queue_before = kit.store.pending(USER.email)
    late = Engine(
        _QueueReadBefore(kit.store, queue_before),
        kit.model,
        kit.engine.files,
        kit.engine.config,
        kit.engine.lambda_timeout,
        kit.engine.model_name,
        clock=kit.engine.clock,
    )
    kit.choose_on(card, "confirm")
    with pytest.raises(AlreadyHandled):
        late.turn(USER, choice(card, "confirm"))
    assert len(kit.store.properties(ACCOUNT)) == 1
    assert len(kit.store.apartments(ACCOUNT)) == 1
    assert len(kit.readings("W-1250")) == 1


def test_second_photo_of_a_serial_linked_meanwhile_becomes_a_reading_card(env, ddb):
    kit = Kit(
        [
            _choose_property("הגפן 7", True),
            _choose_apartment("עורף", True),
            _choose_property("הגפן 7", False),
        ]
    )
    kit.send_photo(water())
    kit.send_photo(water())
    kit.say("הגפן 7")
    kit.say("עורף")
    kit.choose("confirm")
    kit.choose("confirm")
    assert kit.card()["kind"] == "property"
    result = kit.say("הגפן 7")
    assert result.snapshot.card["kind"] == "reading"
    kit.choose("confirm")
    assert kit.card()["kind"] == "duplicate"
    assert kit.store.meter(ACCOUNT, "W-1250")["retired_at"] is None
    [opening] = kit.readings("W-1250")
    assert opening["opens_period"] == "2026-10-29"
