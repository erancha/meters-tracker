"""Retrying a failed message: a text or photo whose handling failed stays stored and marked, and
a retry handles that same message again from its stored copy."""

from datetime import date

import pytest
from engine_kit import USER, Kit, water

from common.engine import Action, AlreadyHandled, MessageFailed
from common.model import IntentResult, ModelRefused


def _down(*args):
    raise ModelRefused("refused")


def _user_messages(kit):
    return [m for m in kit.store.messages(USER.email, "2026") if m["role"] == "user"]


def _failed_photo(kit):
    kit.model.read_meter = _down
    with pytest.raises(MessageFailed):
        kit.send_photo(water(1250))
    del kit.model.read_meter
    return _user_messages(kit)[-1]


def _failed_text(kit):
    kit.model.classify = _down
    with pytest.raises(MessageFailed):
        kit.say("שלום")
    del kit.model.classify
    return _user_messages(kit)[-1]


def _retry(kit, message):
    return kit.engine.turn(USER, Action(retry=message["at"]))


def test_a_failed_photo_stays_stored_and_marked(env, ddb):
    kit = Kit()
    message = _failed_photo(kit)
    assert message["failed"] is True
    assert message["image_key"] is not None
    assert kit.store.pending(USER.email) == []


def test_a_failed_text_stays_stored_and_marked(env, ddb):
    kit = Kit()
    message = _failed_text(kit)
    assert message["failed"] is True
    assert message["text"] == "שלום"


def test_the_failure_keeps_its_cause(env, ddb):
    kit = Kit()
    kit.model.classify = _down
    with pytest.raises(MessageFailed) as failure:
        kit.say("שלום")
    assert isinstance(failure.value.__cause__, ModelRefused)


def test_a_retried_photo_is_read_from_its_stored_copy(env, ddb):
    kit = Kit()
    message = _failed_photo(kit)

    result = _retry(kit, message)

    assert result.snapshot.card is not None
    [retried] = _user_messages(kit)
    assert retried["at"] == message["at"]
    assert "failed" not in retried


def test_a_retried_photo_keeps_the_day_it_was_sent(env, ddb):
    kit = Kit()
    message = _failed_photo(kit)
    kit.today = date(2026, 10, 30)

    _retry(kit, message)

    [item] = kit.store.pending(USER.email)
    assert item["data"]["reading_date"] == "2026-10-29"
    assert _user_messages(kit)[0]["retried_at"].startswith("2026-10-30")


def test_a_retried_text_gets_its_reply_without_a_second_message(env, ddb):
    kit = Kit()
    message = _failed_text(kit)
    kit.model.queue(IntentResult(intent="unknown"))

    result = _retry(kit, message)

    assert len(result.replies) == 1
    assert len(_user_messages(kit)) == 1


def test_a_failed_retry_is_marked_again_with_its_time(env, ddb):
    kit = Kit()
    message = _failed_photo(kit)
    kit.model.read_meter = _down

    with pytest.raises(MessageFailed):
        _retry(kit, message)

    [retried] = _user_messages(kit)
    assert retried["failed"] is True
    assert retried["retried_at"].startswith("2026-10-29")


def test_a_second_retry_of_one_failure_is_already_handled(env, ddb):
    kit = Kit()
    message = _failed_photo(kit)
    _retry(kit, message)
    with pytest.raises(AlreadyHandled):
        _retry(kit, message)


def test_a_retry_of_an_unknown_message_is_already_handled(env, ddb):
    kit = Kit()
    with pytest.raises(AlreadyHandled):
        kit.engine.turn(USER, Action(retry="2026-10-29T08:00:00.000000_aaaa"))
