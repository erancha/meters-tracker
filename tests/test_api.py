"""The HTTP handler's contract: routes, status codes and error bodies, the photo size and type
limits, file URL authorization, the verified-email requirement, and the admin flag on the
chat."""

import base64
import json
from urllib.parse import unquote

import anthropic
import boto3
import pytest
from conftest import ADMIN_EMAIL
from engine_kit import ACCOUNT, Kit, electricity, options, reading

from common import dates
from handlers import api

USER_EMAIL = ACCOUNT
TOO_LARGE = 4 * 1024 * 1024 + 1
EMPTY_SNAPSHOT = {"card": None, "waiting": 0, "tariffs_missing": False}


def event(route, body=None, key=None, email=USER_EMAIL, email_verified="true", query=None):
    claims = {"sub": "s-1", "email": email, "email_verified": email_verified}
    request = {
        "routeKey": route,
        "requestContext": {"authorizer": {"jwt": {"claims": claims}}},
        "pathParameters": None if key is None else {"key": key},
        "body": None if body is None else json.dumps(body),
    }
    return request if query is None else {**request, "queryStringParameters": query}


@pytest.fixture
def kit(env, ddb, monkeypatch):
    kit = Kit()
    monkeypatch.setattr(api, "_model", lambda: kit.model)
    return kit


def _body(result):
    return json.loads(result["body"])


def _post_photo(kit, data=b"photo-0", media_type="image/jpeg"):
    kit.register_photo(data, electricity())
    body = {"image": base64.b64encode(data).decode(), "media_type": media_type}
    return api.handler(event("POST /turn", body), None)


def _choice(card, option):
    return {"choice": {"pending": card["pending"], "version": card["version"], "option": option}}


def _stored_keys():
    listed = boto3.client("s3").list_objects_v2(Bucket="files")
    return [o["Key"] for o in listed.get("Contents", [])]


def _seed_known_meter(kit):
    apartment = kit.apartment(start="2026-10-01")
    kit.meter(
        apartment,
        "E-392",
        "electricity",
        [reading("2026-10-01", 392000, opens="2026-10-01")],
        display_choice=0,
    )


def test_get_chat_returns_history_and_snapshot(kit):
    _seed_known_meter(kit)
    _post_photo(kit)
    result = api.handler(event("GET /chat"), None)
    assert result["statusCode"] == 200
    body = _body(result)
    assert body["messages"][0]["role"] == "user"
    assert body["snapshot"]["card"]["kind"] == "reading"
    assert body["snapshot"]["waiting"] == 0
    assert body["admin"] is False


def test_get_chat_takes_a_day(kit):
    _seed_known_meter(kit)
    _post_photo(kit)
    other = api.handler(event("GET /chat", query={"day": "2026-10-01"}), None)
    assert _body(other)["messages"] == []
    today = api.handler(event("GET /chat", query={"day": dates.today().isoformat()}), None)
    assert _body(today)["messages"][0]["role"] == "user"


def test_get_chat_rejects_a_malformed_day(kit):
    result = api.handler(event("GET /chat", query={"day": "yesterday"}), None)
    assert (result["statusCode"], _body(result)["error"]) == (400, "invalid_request")


def test_post_turn_image_creates_reading_card(kit):
    _seed_known_meter(kit)
    result = _post_photo(kit)
    assert result["statusCode"] == 200
    body = _body(result)
    assert options(body["snapshot"]["card"]) == ["confirm"]
    assert body["replies"][-1]["card"] == body["snapshot"]["card"]
    assert _stored_keys() == [body["image_key"]]


def test_choice_confirms_the_reading(kit):
    _seed_known_meter(kit)
    card = _body(_post_photo(kit))["snapshot"]["card"]
    result = api.handler(event("POST /turn", _choice(card, "confirm")), None)
    assert result["statusCode"] == 200
    assert _body(result)["snapshot"]["card"] is None
    assert _body(result)["image_key"] is None
    assert kit.readings("E-392")[-1]["value"] == 392691
    history = _body(api.handler(event("GET /chat"), None))["messages"]
    assert [m["choice"] for m in history if m["role"] == "user"] == [
        None,
        {"pending": card["pending"], "version": card["version"]},
    ]


def test_choice_on_handled_item_is_409(kit):
    _seed_known_meter(kit)
    card = _body(_post_photo(kit))["snapshot"]["card"]
    choice = _choice(card, "confirm")
    api.handler(event("POST /turn", choice), None)
    result = api.handler(event("POST /turn", choice), None)
    assert result["statusCode"] == 409
    assert _body(result) == {"error": "already_handled", "snapshot": EMPTY_SNAPSHOT}
    assert len(kit.readings("E-392")) == 2


def test_oversized_image_is_400(kit):
    result = _post_photo(kit, data=b"x" * TOO_LARGE)
    assert result["statusCode"] == 400
    assert _body(result) == {"error": "image_too_large"}
    assert _stored_keys() == []
    assert kit.store.pending(ACCOUNT) == []


def test_gif_is_400(kit):
    result = _post_photo(kit, media_type="image/gif")
    assert result["statusCode"] == 400
    assert _body(result) == {"error": "unsupported_image_type"}
    assert _stored_keys() == []
    assert kit.store.pending(ACCOUNT) == []


def test_files_url_of_own_account_is_served(kit):
    result = api.handler(event("GET /files/{key+}", key=f"{ACCOUNT}/images/a.jpg"), None)
    assert result["statusCode"] == 200
    assert f"{ACCOUNT}/images/a.jpg" in unquote(_body(result)["url"])


def test_files_url_of_other_account_is_403(kit):
    result = api.handler(event("GET /files/{key+}", key="other%40x.com/images/a.jpg"), None)
    assert result["statusCode"] == 403
    assert _body(result) == {"error": "forbidden"}


def test_unknown_route_raises(kit):
    with pytest.raises(ValueError):
        api.handler(event("GET /nope"), None)


def test_choice_on_parked_item_is_409(kit):
    kit.store.put_pending(
        ACCOUNT,
        {
            "seq": "2026-10-29T08:00:00_aaaa",
            "kind": "close_period",
            "status": "parked",
            "step": "x",
            "version": 1,
            "data": {},
        },
    )
    card = {"pending": "2026-10-29T08:00:00_aaaa", "version": 1}
    result = api.handler(event("POST /turn", _choice(card, "confirm")), None)
    assert result["statusCode"] == 409
    assert _body(result)["error"] == "already_handled"


def test_invalid_base64_is_400(kit):
    result = api.handler(
        event("POST /turn", {"image": "!!not base64!!", "media_type": "image/jpeg"}), None
    )
    assert result["statusCode"] == 400
    assert _body(result) == {"error": "invalid_image"}


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"image": "aGk="},
        {"text": "a", "image": "aGk="},
        {"choice": {"pending": "x"}},
        {"choice": {"pending": "x", "option": "confirm"}},
        {"choice": {"pending": "x", "version": "1", "option": "confirm"}},
        {"choice": {"pending": "x", "version": True, "option": "confirm"}},
        {"text": 5},
        [],
    ],
)
def test_malformed_body_is_400(kit, body):
    result = api.handler(event("POST /turn", body), None)
    assert result["statusCode"] == 400
    assert _body(result) == {"error": "invalid_request"}
    assert _stored_keys() == []


def test_get_chat_does_not_build_the_model(kit, monkeypatch):
    def refuse():
        raise AssertionError("model built")

    monkeypatch.setattr(api, "_model", refuse)
    assert api.handler(event("GET /chat"), None)["statusCode"] == 200


def test_a_model_with_no_listed_price_stops_every_request(kit, monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "anthropic:claude-unlisted")
    with pytest.raises(ValueError, match="PRICE_PER_MILLION in src/common/model.py"):
        api.handler(event("GET /chat"), None)


@pytest.mark.parametrize(
    "key",
    ["owner@x.com.evil%2Fimages%2Fa.jpg", "other%40x.com%2F..%2Fa.jpg", "%2Fowner%40x.com%2Fa.jpg"],
)
def test_files_url_with_lookalike_key_is_403(kit, key):
    result = api.handler(event("GET /files/{key+}", key=key), None)
    assert result["statusCode"] == 403


def test_non_ascii_image_is_400(kit):
    result = api.handler(event("POST /turn", {"image": "é", "media_type": "image/jpeg"}), None)
    assert result["statusCode"] == 400
    assert _body(result) == {"error": "invalid_image"}


@pytest.mark.parametrize("without_body", ["absent", "none"])
def test_turn_without_body_is_400(kit, without_body):
    request = event("POST /turn")
    if without_body == "absent":
        del request["body"]
    result = api.handler(request, None)
    assert result["statusCode"] == 400
    assert _body(result) == {"error": "invalid_request"}


def test_option_not_on_the_current_card_is_409(kit):
    _seed_known_meter(kit)
    card = _body(_post_photo(kit))["snapshot"]["card"]
    result = api.handler(event("POST /turn", _choice(card, "reset")), None)
    assert result["statusCode"] == 409
    snapshot = {**EMPTY_SNAPSHOT, "card": card}
    assert _body(result) == {"error": "already_handled", "snapshot": snapshot}
    assert len(kit.readings("E-392")) == 1


@pytest.mark.parametrize("route", ["GET /chat", "POST /turn"])
def test_unverified_email_is_403_and_creates_no_account(kit, route):
    result = api.handler(event(route, {"text": "שלום"}, email_verified="false"), None)
    assert result["statusCode"] == 403
    assert _body(result) == {"error": "email_unverified"}
    assert kit.store.access_get(USER_EMAIL) is None


def _provider_down(*args):
    raise anthropic.APIError("credit balance is too low", None, body=None)


def _assert_message_failed(result, caplog):
    assert result["statusCode"] == 503
    assert _body(result) == {"error": "message_failed"}
    assert any(r.levelname == "ERROR" and r.exc_info for r in caplog.records)


def _post_failing_photo(kit):
    kit.model.read_meter = _provider_down
    body = {"image": base64.b64encode(b"photo-0").decode(), "media_type": "image/jpeg"}
    return api.handler(event("POST /turn", body), None)


def _chat_messages():
    return _body(api.handler(event("GET /chat"), None))["messages"]


def test_failed_photo_turn_reports_the_failure_and_marks_the_message(kit, caplog):
    result = _post_failing_photo(kit)

    _assert_message_failed(result, caplog)
    assert kit.store.pending(USER_EMAIL) == []
    [photo] = [m for m in _chat_messages() if m["image_key"]]
    assert photo["failed"] is True


def test_failed_text_turn_reports_the_failure(kit, caplog):
    kit.model.classify = _provider_down

    result = api.handler(event("POST /turn", {"text": "מה המצב"}), None)

    _assert_message_failed(result, caplog)


def test_a_turn_failing_for_any_other_reason_reports_the_same_failure(kit, caplog):
    def crash(*args):
        raise RuntimeError("crash")

    kit.model.classify = crash

    result = api.handler(event("POST /turn", {"text": "מה המצב"}), None)

    _assert_message_failed(result, caplog)


def test_retry_answers_a_failed_photo(kit, caplog):
    _post_failing_photo(kit)
    kit.register_photo(b"photo-0", electricity())
    del kit.model.read_meter
    [photo] = [m for m in _chat_messages() if m["image_key"]]

    result = api.handler(event("POST /turn", {"retry": photo["at"]}), None)

    assert result["statusCode"] == 200
    assert _body(result)["snapshot"]["card"] is not None


def test_retry_of_a_message_not_failed_is_409_with_the_snapshot(kit):
    result = api.handler(event("POST /turn", {"retry": "2026-10-29T08:00:00.0_aaaa"}), None)

    assert result["statusCode"] == 409
    assert _body(result)["error"] == "already_handled"
    assert "snapshot" in _body(result)


def test_retry_naming_no_message_is_invalid(kit):
    result = api.handler(event("POST /turn", {"retry": 5}), None)

    assert result["statusCode"] == 400
    assert _body(result) == {"error": "invalid_request"}


def test_admin_chat_is_flagged_in_any_letter_case(kit):
    body = _body(api.handler(event("GET /chat", email=ADMIN_EMAIL.upper()), None))
    assert body["admin"] is True
    assert [m["role"] for m in body["messages"]] == ["assistant"]
