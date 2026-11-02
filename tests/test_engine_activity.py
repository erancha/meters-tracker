"""The reading field on a photo's message: which meter the photo read and whether its reading was
saved, set as the photo moves through its flows, so the browser can list the day's work."""

from datetime import date, timedelta

import pytest
from engine_kit import UNKNOWN, USER, Kit, edits, electricity, reading, water

from common import dates
from common.engine import Action, MessageFailed
from common.flows import READING, new_item
from common.model import IntentResult, ModelRefused


def _photos(kit):
    return [m for m in kit.store.messages(USER.email, "2026") if m["image_key"] is not None]


def _photo(kit):
    return _photos(kit)[-1]


def _known_electric_meter(kit):
    apartment = kit.apartment(start="2026-10-01")
    kit.meter(
        apartment,
        "E-392",
        "electricity",
        [reading("2026-10-01", 392000, opens="2026-10-01")],
        display_choice=0,
    )


def _down(*args):
    raise ModelRefused("refused")


def test_a_read_photo_of_a_known_meter_is_awaiting_with_its_place(env, ddb):
    kit = Kit()
    _known_electric_meter(kit)
    kit.send_photo(electricity())
    assert _photo(kit)["reading"] == {
        "state": "awaiting",
        "meter_type": "electricity",
        "apartment": "עורף",
        "property": "הגפן 7",
    }


def test_a_confirmed_reading_is_saved(env, ddb):
    kit = Kit()
    _known_electric_meter(kit)
    kit.send_photo(electricity())
    kit.choose("confirm")
    assert _photo(kit)["reading"]["state"] == "saved"


def test_a_photo_of_an_unknown_meter_is_awaiting_through_the_link_questions(env, ddb):
    both = edits(("property", "לאונר 18"), ("apartment", "דירה קדמית"))
    kit = Kit([both])
    kit.send_photo(water())
    assert _photo(kit)["reading"] == {
        "state": "awaiting",
        "meter_type": "water",
        "apartment": None,
        "property": None,
    }
    kit.say("לאונר 18, דירה קדמית")
    kit.choose("start_today")
    kit.choose("confirm")
    assert _photo(kit)["reading"] == {
        "state": "saved",
        "meter_type": "water",
        "apartment": "דירה קדמית",
        "property": "לאונר 18",
    }


def test_a_rejected_photo_is_dropped(env, ddb):
    kit = Kit([IntentResult(intent="reject")])
    _known_electric_meter(kit)
    kit.send_photo(electricity())
    kit.say("לא")
    assert _photo(kit)["reading"]["state"] == "dropped"


def test_a_photo_that_is_not_a_meter_is_dropped(env, ddb):
    kit = Kit()
    kit.send_photo(UNKNOWN)
    assert _photo(kit)["reading"]["state"] == "dropped"


def test_a_statement_photo_is_dropped_before_its_tariffs_card(env, ddb):
    kit = Kit()
    kit.send_statement()
    assert _photo(kit)["reading"]["state"] == "dropped"
    assert kit.card()["kind"] == "tariffs"


def test_an_unreadable_photo_replaced_by_its_retake_is_dropped(env, ddb):
    kit = Kit()
    apartment = kit.apartment(start="2026-10-01")
    kit.meter(apartment, "W-1250", "water", [reading("2026-10-01", 1000, opens="2026-10-01")])
    kit.send_photo(water(None, unreadable=True))
    kit.send_photo(water(1250))
    first, retake = _photos(kit)
    assert first["reading"]["state"] == "dropped"
    assert retake["reading"]["state"] == "awaiting"


def test_a_failed_photo_has_no_reading_until_a_retry_reads_it(env, ddb):
    kit = Kit()
    _known_electric_meter(kit)
    kit.model.read_meter = _down
    with pytest.raises(MessageFailed):
        kit.send_photo(electricity())
    del kit.model.read_meter
    failed = _photo(kit)
    assert "reading" not in failed and failed["failed"] is True

    kit.today = date(2026, 10, 30)
    kit.engine.turn(USER, Action(retry=failed["at"]))

    assert _photo(kit)["reading"]["state"] == "awaiting"
    assert _photo(kit)["at"] == failed["at"]


def test_a_killed_reading_is_awaiting_once_the_snapshot_shows_its_retake_card(env, ddb):
    kit = Kit()
    at = "2026-10-29T08:00:00.000000_bbbb"
    key = "owner@x.com/images/a.jpg"
    kit.store.append_message(
        USER.email,
        {
            "at": at,
            "role": "user",
            "text": None,
            "card": None,
            "image_key": key,
            "pdf_key": None,
            "choice": None,
        },
    )
    timeout = kit.engine.lambda_timeout
    data = {
        "image_key": key,
        "message_at": at,
        "read_started": (dates.now() - timeout - timedelta(seconds=1)).isoformat(),
        "failed_photos": [],
    }
    seq = "2026-10-29T08:00:00.000000_aaaa"
    kit.store.put_pending(USER.email, new_item(seq, "meter_photo", READING, None, data))
    kit.engine.snapshot(USER)
    assert _photo(kit)["reading"]["state"] == "awaiting"
