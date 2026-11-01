"""HTTP API handler in front of the conversation engine: the chat history with its snapshot, one
turn (text, photo, card choice or retry of a failed message), and presigned URLs for the
account's own photos.

The caller's identity comes exclusively from the JWT claims the API Gateway authorizer verified;
the request body never names a user, and an address Google has not verified is refused before it
is resolved to an account. The engine is built per invocation from the environment.
Photos are validated here, before the engine, so a rejected upload writes nothing to S3 or the
pending table."""

import base64
import binascii
import json
import os
from dataclasses import asdict
from datetime import date, timedelta
from urllib.parse import unquote

import boto3

from common import access
from common.engine import Action, AlreadyHandled, Choice, Engine, MessageFailed
from common.files import Files
from common.log import get_logger
from common.model import api_key_from_ssm, build_model
from common.notify import Mailer
from common.store import Store
from common.webapi import response

logger = get_logger(__name__)

MAX_IMAGE_BYTES = 4 * 1024 * 1024
IMAGE_TYPES = {"image/jpeg", "image/png"}


class _Rejected(Exception):
    def __init__(self, status, body):
        self.response = response(status, body)


def handler(event, context):
    claims = event["requestContext"]["authorizer"]["jwt"]["claims"]
    route = event["routeKey"]
    logger.info("request route=%s sub=%s", route, claims["sub"])
    if claims["email_verified"] != "true":
        return response(403, {"error": "email_unverified"})
    store = Store.from_env()
    user = access.resolve(store, claims["email"])
    if route == "GET /chat":
        try:
            return _chat(_engine(store), user, event.get("queryStringParameters"))
        except _Rejected as rejected:
            return rejected.response
    if route == "POST /turn":
        try:
            return _turn(store, user, event.get("body"))
        except _Rejected as rejected:
            return rejected.response
    if route == "GET /files/{key+}":
        return _file_url(user, unquote(event["pathParameters"]["key"]))
    raise ValueError(f"unhandled route {route!r}")


def _model():
    return build_model(os.environ["LLM_MODEL"], api_key_from_ssm(os.environ["LLM_KEY_PARAM"]))


class _LazyModel:
    """Builds the model on first use, so a request that never reads a photo or classifies text
    does not fetch the API key."""

    def __init__(self):
        self._built = None

    def __getattr__(self, name):
        if self._built is None:
            self._built = _model()
        return getattr(self._built, name)


def _config():
    with open(os.environ["APP_CONFIG_PATH"]) as f:
        return json.load(f)


def _files():
    return Files(os.environ["FILES_BUCKET"])


def _engine(store):
    lambda_timeout = timedelta(seconds=int(os.environ["LAMBDA_TIMEOUT_SECONDS"]))
    mailer = Mailer(boto3.client("ses"), os.environ["SES_SENDER"])
    return Engine(
        store,
        _LazyModel(),
        _files(),
        mailer,
        _config(),
        lambda_timeout,
        os.environ["LLM_MODEL"],
    )


def _chat(engine, user, query):
    # The HTTP API leaves queryStringParameters out of a request with no query string.
    day = None if query is None else query["day"]
    if day is not None:
        try:
            date.fromisoformat(day)
        except ValueError:
            raise _invalid()
    messages, snapshot = engine.load(user, day)
    return response(200, {"messages": messages, "snapshot": asdict(snapshot)})


def _invalid():
    return _Rejected(400, {"error": "invalid_request"})


def _is_str(value):
    return isinstance(value, str)


def _turn(store, user, raw_body):
    if raw_body is None:
        raise _invalid()
    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError:
        raise _invalid()
    action = _action(body)
    engine = _engine(store)
    try:
        result = engine.turn(user, action)
    except AlreadyHandled:
        raise _Rejected(
            409, {"error": "already_handled", "snapshot": asdict(engine.snapshot(user))}
        )
    except MessageFailed:
        logger.exception("handling a stored message failed")
        raise _Rejected(503, {"error": "message_failed"})
    return response(200, asdict(result))


def _action(body):
    if not isinstance(body, dict):
        raise _invalid()
    keys = set(body)
    if keys == {"text"} and _is_str(body["text"]):
        return Action(text=body["text"])
    if {"image", "media_type"} <= keys <= {"image", "media_type", "note"}:
        return _image_action(body)
    if keys == {"choice"}:
        return _choice_action(body["choice"])
    if keys == {"retry"} and _is_str(body["retry"]):
        return Action(retry=body["retry"])
    raise _invalid()


def _image_action(body):
    note = body.get("note")
    if not (
        _is_str(body["image"]) and _is_str(body["media_type"]) and (note is None or _is_str(note))
    ):
        raise _invalid()
    if not body["image"].isascii():
        raise _Rejected(400, {"error": "invalid_image"})
    try:
        data = base64.b64decode(body["image"], validate=True)
    except binascii.Error:
        raise _Rejected(400, {"error": "invalid_image"})
    if len(data) > MAX_IMAGE_BYTES:
        raise _Rejected(400, {"error": "image_too_large"})
    if body["media_type"] not in IMAGE_TYPES:
        raise _Rejected(400, {"error": "unsupported_image_type"})
    return Action(image=data, media_type=body["media_type"], note=note)


def _choice_action(choice):
    if not (
        isinstance(choice, dict)
        and set(choice) == {"pending", "version", "option"}
        and _is_str(choice["pending"])
        and type(choice["version"]) is int
        and _is_str(choice["option"])
    ):
        raise _invalid()
    return Action(choice=Choice(choice["pending"], choice["version"], choice["option"]))


def _file_url(user, key):
    if key.split("/")[0] != user.account:
        return response(403, {"error": "forbidden"})
    minutes = _config()["file_url_minutes"]
    return response(200, {"url": _files().url(key, minutes)})
