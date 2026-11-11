"""Replays the owner's story against a deployed api Lambda with its real model: the logic behind
scripts/replay.sh, which passes the app stack's name, the account's address and the photos.

The account is reset first, only with --yes (without it the replay prints what the reset would
delete and stops), then the Lambda is invoked directly with the JWT claims the API
Gateway authorizer would pass for that address, so no sign-in is needed. Every turn's replies
and card are printed; a card of another kind than the story expects, or a status other than
200, stops the replay.

- Api: one turn or chat load as an invocation event, its answer checked for status 200
- lambda_invoker: invokes the deployed function once per turn, never retried, since a retried
  turn would act twice
- start_for: the period start the replay types, which puts today in the period's last week
- walk: the story, step by step, with the card kind each step expects
- run: resolve the function, walk the story
- main: reset when confirmed, then run; exit 1 unconfirmed or on the first unexpected answer, 2
  with one line naming the cause when an AWS call fails
"""

import argparse
import base64
import json
import mimetypes
import sys
from datetime import date, timedelta
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import app_stack
from owner_story import CANCEL, PLACE, TYPED_PRICES
from reset_account import NOT_CONFIRMED, account_address, reset

from common import dates
from common.dates import add_months, fmt
from common.files import EXTENSIONS

# Longer than any api Lambda timeout, so the replay never gives up on a turn still running.
INVOKE_READ_TIMEOUT_SECONDS = 900


class Unexpected(Exception):
    """The deployed app answered a step with a status or card the story does not expect."""


def _event(route, email, body) -> dict:
    claims = {"sub": "replay", "email": email, "email_verified": "true"}
    return {
        "routeKey": route,
        "requestContext": {"authorizer": {"jwt": {"claims": claims}}},
        "pathParameters": None,
        "body": None if body is None else json.dumps(body, ensure_ascii=False),
    }


class Api:
    """The api routes the replay uses, over invoke, which takes an event and returns the
    handler's response."""

    def __init__(self, invoke, email: str):
        self._invoke = invoke
        self._email = email

    def call(self, route, body=None) -> dict:
        response = self._invoke(_event(route, self._email, body))
        answer = json.loads(response["body"])
        if response["statusCode"] != 200:
            raise Unexpected(f"{route} answered {response['statusCode']}: {answer}")
        return answer

    def chat(self) -> dict:
        return self.call("GET /chat")

    def text(self, text) -> dict:
        return self.call("POST /turn", {"text": text})

    def photo(self, path: Path) -> dict:
        media_type = mimetypes.guess_type(path.name)[0]
        image = base64.b64encode(path.read_bytes()).decode()
        return self.call("POST /turn", {"image": image, "media_type": media_type})

    def choose(self, card, option) -> dict:
        choice = {"pending": card["pending"], "version": card["version"], "option": option}
        return self.call("POST /turn", {"choice": choice})


def lambda_invoker(function_name: str):
    config = Config(read_timeout=INVOKE_READ_TIMEOUT_SECONDS, retries={"total_max_attempts": 1})
    client = boto3.client("lambda", config=config)

    def invoke(event) -> dict:
        answer = client.invoke(FunctionName=function_name, Payload=json.dumps(event).encode())
        payload = json.loads(answer["Payload"].read())
        if "FunctionError" in answer:
            raise Unexpected(f"the api Lambda failed: {payload}")
        return payload

    return invoke


def start_for(today: date, months: int) -> date:
    """A period start whose period of months ends at most a day after today, so the closing
    photo taken today is offered as the period's close."""
    return add_months(today + timedelta(days=2), -months)


def _show(step, answer, key):
    """key names the answer's texts: "messages" for a chat load, "replies" for a turn."""
    print(f"--- {step}")
    for message in answer[key]:
        print(message["text"])
    card = answer["snapshot"]["card"]
    if card is not None:
        buttons = " | ".join(o["label"] for o in card["options"])
        lines = " / ".join(card["lines"])
        print(f"[card] {card['kind']} | {card['title']} | {lines} | buttons: {buttons}")
    print(f"queued: {len(answer['snapshot']['queued'])}")


def _expect(step, answer, kind, key="replies") -> dict | None:
    """Prints the step's answer and returns its card, which must be of kind (None: no card)."""
    _show(step, answer, key)
    card = answer["snapshot"]["card"]
    got = None if card is None else card["kind"]
    if got != kind:
        raise Unexpected(f"{step}: expected a {kind} card, got {got}")
    return card


def walk(api: Api, open_photo: Path, close_photo: Path, statement_photo, today, months):
    """statement_photo is a Path, or None to end after the charge."""
    _expect("load the empty account", api.chat(), None, key="messages")
    _expect("send the opening photo", api.photo(open_photo), "property")
    _expect(f"type {PLACE}", api.text(PLACE), "period")
    start = start_for(today, months)
    card = _expect(f"type the start {fmt(start)}", api.text(fmt(start)), "link")
    # The default saves on either card: a number on a two-number display, else confirm.
    option = card["default"]
    _expect(f"save the link by {option}", api.choose(card, option), None)
    card = _expect("send the closing photo", api.photo(close_photo), "reading")
    card = _expect("confirm the reading", api.choose(card, "confirm"), "propose_close")
    _expect("close the period", api.choose(card, "close"), "tariffs_ask")
    card = _expect(f"type {TYPED_PRICES}", api.text(TYPED_PRICES), "tariffs")
    answer = api.choose(card, "confirm")
    _expect("save the tariffs", answer, None)
    if not any("₪" in reply["text"] for reply in answer["replies"]):
        raise Unexpected("save the tariffs: no reply names the closed period's charge")
    if statement_photo is not None:
        _expect("send the statement photo", api.photo(statement_photo), "tariffs")
        _expect(f"type {CANCEL}", api.text(CANCEL), None)


def run(stack, address, open_photo, close_photo, statement_photo):
    """Walks the story for an account that reset has just emptied."""
    function_name = app_stack.physical_ids(stack)["ApiFunction"]
    with open(ROOT / "config" / "app.json") as f:
        months = json.load(f)["default_period_months"]
    api = Api(lambda_invoker(function_name), address)
    walk(api, open_photo, close_photo, statement_photo, dates.today(), months)
    print("Replay done: every step answered as the story expects.")


def _photo(text) -> Path:
    path = Path(text)
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"{path} is not a file")
    if mimetypes.guess_type(path.name)[0] not in EXTENSIONS:
        raise argparse.ArgumentTypeError(f"{path.name} is not a JPEG or PNG")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="replay.sh",
        usage=("%(prog)s <email> --open <photo> --close <photo> [--statement <photo>] [--yes]"),
    )
    parser.add_argument("stack")
    parser.add_argument("email")
    parser.add_argument("--open", required=True, type=_photo)
    parser.add_argument("--close", required=True, type=_photo)
    parser.add_argument("--statement", type=_photo)
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    address = account_address(args.email)
    try:
        if not reset(args.stack, address, args.yes):
            print(NOT_CONFIRMED, file=sys.stderr)
            sys.exit(1)
        run(args.stack, address, args.open, args.close, args.statement)
    except Unexpected as error:
        print(f"Replay stopped: {error}", file=sys.stderr)
        sys.exit(1)
    except (BotoCoreError, ClientError) as error:
        fix = "run scripts/init-env.sh or check scripts/lib/aws-config.sh"
        print(f"AWS call failed: {error}; {fix}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
