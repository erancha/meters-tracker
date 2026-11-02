"""Runs the recorded chat messages through the model named by LLM_MODEL and checks the intent it
classifies each into. The logic behind scripts/eval-intents.sh, which passes the case file.

A case is a message, the card it answers (by name, or null for a message with no card), the day
it is typed on, the expected intent and the expected fields; only the fields a case lists are
compared. The cards are rendered by texts.py, as the engine renders them, so a wording change
reaches the evaluation too. Every message is sent as the engine sends it: the card with its
fields, the field it asked for again and its button labels, then a records block, which lists the
card as a card|<seq> line. The block is the named account a case lists under records, or else an
account holding only the apartments the case lists, which go only with a request no card takes
(no card, or a standby card).

- CARDS / FORMS / ASKED: the cards a case may name, from the owner's story, their fields, and the
  field a card asked for again
- RECORDS: the accounts a case may name, each with the cards waiting behind the case's card and
  the dropped photos it lists
- load_cases: reads and validates the case file, naming the case that is wrong; an expected
  operation must target the card or an id the case's records block lists, and a set must name a
  field that target has, with a value that fits the field
- context / mismatches / evaluate: one case's classifier context, its differences from the
  expectation (operations in any order), and the run over all cases, yielding each as soon as the
  model answers
- main: prints one PASS or FAIL line per case and the total; exits 1 when any case fails, 2
  with one line naming the cause when a model call fails
"""

import json
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import get_args

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from owner_story import (
    APARTMENT,
    BOTTOM_OFFSET,
    CLOSING,
    LATER,
    NEXT_START,
    OPENING,
    PERIOD_START,
    PRICES,
    PROPERTY,
    SERIAL,
    SERIAL_READ,
    TODAY,
    WATER_OPENING,
    WATER_SERIAL,
)

from common import account, texts
from common.account import Account
from common.fields import FIELDS as FIELD_TABLE
from common.fields import Field, InvalidValue, parse
from common.flows import classify_context, make_card, message_context
from common.flows.meter_photo import PHOTO_FIELDS
from common.model import PROVIDER_ERRORS, IntentResult, ModelRefused, Operation, model_from_env
from common.periods import PRICES as PRICE_KEYS

TWO_NUMBERS = {"top": OPENING, "bottom": OPENING + BOTTOM_OFFSET}


def _card(text, options=(), labels=None) -> dict:
    return make_card({"seq": "eval", "version": 0}, "eval", text, list(options), labels)


CARDS = {
    "unreadable_serial": _card(texts.unreadable([texts.UNREADABLE_SERIAL])),
    "property": _card(texts.link_property("electricity", SERIAL_READ, [])),
    "property_existing": _card(texts.link_property("water", WATER_SERIAL, [PROPERTY])),
    "apartment": _card(texts.link_apartment(PROPERTY, [])),
    "period": _card(
        texts.link_period(APARTMENT, PROPERTY, 2),
        ["start_today", "start_month"],
        texts.start_labels({"start_today": "2026-10-29", "start_month": "2026-11-01"}),
    ),
    # The period question after a mistyped apartment name, which the next message corrects.
    "period_typo": _card(
        texts.link_period("קדמיץ", PROPERTY, 2),
        ["start_today", "start_month"],
        texts.start_labels({"start_today": "2026-10-29", "start_month": "2026-11-01"}),
    ),
    "link": _card(
        texts.link_confirm(
            "electricity",
            SERIAL_READ,
            PROPERTY,
            True,
            APARTMENT,
            True,
            TWO_NUMBERS["top"],
            PERIOD_START,
            TWO_NUMBERS,
            "top",
            PERIOD_START,
            2,
            None,
        ),
        ["top", "bottom", "confirm"],
    ),
    "reading": _card(
        texts.reading_card("electricity", SERIAL, APARTMENT, PROPERTY, CLOSING, TODAY, None, "top"),
        ["confirm"],
    ),
    "propose_close": _card(texts.propose_close(date(2026, 10, 31), TODAY), ["close", "keep_open"]),
    "change_period": _card(texts.change_card(APARTMENT, NEXT_START, 2), ["confirm"]),
    "tariffs_ask": _card(texts.tariffs_ask()),
    "tariffs": _card(
        texts.tariffs_card(PRICES, TODAY, True, False, None),
        ["confirm"],
        {"confirm": texts.SAVE_TARIFFS_LABEL},
    ),
    "close": _card(
        texts.close_card(APARTMENT, NEXT_START, [("electricity", SERIAL, 39500, LATER)]),
        ["confirm", "vacate"],
    ),
    "duplicate": _card(texts.duplicate_reading(CLOSING, TODAY), ["confirm"]),
    "lower_reading": _card(
        texts.lower_reading({"value": CLOSING, "date": TODAY.isoformat()}, 3926),
        ["misread", "replaced", "reset"],
    ),
    "swap": _card(texts.propose_swap(TODAY), ["confirm", "keep_open"]),
    "propose_reclose": _card(texts.propose_reclose(TODAY), ["reclose", "keep_open"]),
    "open_period": _card(texts.propose_open(LATER), ["confirm"]),
    # The reading card after it asked again for the serial.
    "reading_asked_serial": _card(
        texts.reading_card("electricity", SERIAL, APARTMENT, PROPERTY, CLOSING, TODAY, None, "top"),
        ["confirm"],
    ),
    "correction": _card(
        texts.correction_card(
            [
                texts.change_line(
                    texts.reading_label("water", "4410290", PERIOD_START),
                    texts.FIELD_LABELS["value"],
                    "820",
                    "843",
                )
            ]
        ),
        ["confirm"],
    ),
    "question": _card(
        (
            texts.FIELD_QUESTIONS["value"].format(current="820"),
            [texts.FIELD_HOW_TO["value"]],
        )
    ),
}

_READ = {"value": OPENING, "date": TODAY.isoformat(), "meter_type": "electricity"}
_PLACE = {"property": PROPERTY, "apartment": APARTMENT}
_QUESTION = {"period_start": None, "months": None}
# The fields each card is sent with, as the flow's form lists them for that card.
FORMS = {
    "unreadable_serial": {
        "value": OPENING,
        "date": TODAY.isoformat(),
        "serial": None,
        "meter_type": "electricity",
    },
    "property": {"property": None, "apartment": None, **_READ, "serial": SERIAL_READ, **_QUESTION},
    "property_existing": {
        "property": None,
        "apartment": None,
        **_READ,
        "meter_type": "water",
        "serial": WATER_SERIAL,
        **_QUESTION,
    },
    "apartment": {
        "property": PROPERTY,
        "apartment": None,
        **_READ,
        "serial": SERIAL_READ,
        **_QUESTION,
    },
    "period": {**_PLACE, **_READ, "serial": SERIAL_READ, "period_start": None, "months": 2},
    "period_typo": {
        "property": PROPERTY,
        "apartment": "קדמיץ",
        **_READ,
        "serial": SERIAL_READ,
        "period_start": None,
        "months": 2,
    },
    "link": {
        **_PLACE,
        **_READ,
        "date": PERIOD_START.isoformat(),
        "serial": SERIAL_READ,
        "period_start": PERIOD_START.isoformat(),
        "months": 2,
    },
    "reading": {"value": CLOSING, "date": TODAY.isoformat(), "serial": SERIAL},
    "propose_close": {"value": CLOSING, "date": TODAY.isoformat(), "serial": SERIAL},
    "change_period": {"period_start": NEXT_START.isoformat(), "months": 2},
    "tariffs_ask": {
        "electricity_price": None,
        "water_price": None,
        "effective_date": TODAY.isoformat(),
    },
    "tariffs": {
        "electricity_price": PRICES["electricity"],
        "water_price": PRICES["water"],
        "effective_date": TODAY.isoformat(),
    },
    "close": {f"reading|{SERIAL}|{LATER.isoformat()}#{LATER.isoformat()}T09:00:00": 39500},
    "duplicate": {"value": 39280, "date": TODAY.isoformat(), "serial": SERIAL},
    "lower_reading": {"value": 3926, "date": TODAY.isoformat(), "serial": SERIAL},
    "swap": {"value": 39250, "date": TODAY.isoformat(), "serial": SERIAL},
    "propose_reclose": {"value": 39300, "date": LATER.isoformat(), "serial": SERIAL},
    "open_period": {"value": 39600, "date": LATER.isoformat(), "serial": SERIAL},
    "reading_asked_serial": {"value": CLOSING, "date": TODAY.isoformat(), "serial": SERIAL},
    "correction": {},
    "question": {"value": None},
}
# Key: card name; value: the field that card asked for again
ASKED = {"reading_asked_serial": "serial"}
# The seq the case's own card is listed under in the records block, as the engine lists it.
CARD_SEQ = "2026-10-29T15:00:00.000000_a1b2"


def _reading(day: str, value, opens=None, closes=None, hour="09") -> dict:
    return {
        "date": day,
        "seq": f"{day}T{hour}:00:00",
        "value": value,
        "image_key": f"k-{day}-{value}",
        "recorded_by": "owner@x.com",
        "opens_period": opens,
        "closes_period": closes,
        "resets": False,
    }


def _meter(serial, apartment_id, meter_type, display_choice=None, retired_at=None, replaces=None):
    return {
        "serial": serial,
        "apartment_id": apartment_id,
        "type": meter_type,
        "display_choice": display_choice,
        "label_hint": None,
        "retired_at": retired_at,
        "former_serials": [],
        "replaces": replaces,
    }


def _period(start, closed_on=None) -> dict:
    return {"start": start, "months": 2, "closed_on": closed_on, "last_reminded_on": None}


_START, _CLOSED, _NEXT = PERIOD_START.isoformat(), TODAY.isoformat(), NEXT_START.isoformat()
# The owner's story after the 2026-11-01 sessions: the front apartment's first period closed,
# its electricity meter linked under the misread serial and then as a replacement of it, its water
# meter read once more before the close, the back apartment's water meter read twice, and a
# second property with one apartment.
_STORY = Account(
    properties=[{"id": "p1", "name": PROPERTY}, {"id": "p2", "name": "לאונר 16"}],
    apartments=[
        {"id": "a1", "property_id": "p1", "name": APARTMENT, "period_months": 2},
        {"id": "a2", "property_id": "p1", "name": "עורף", "period_months": 2},
        {"id": "a3", "property_id": "p2", "name": "דירה 3", "period_months": 2},
    ],
    meters=[
        _meter(SERIAL_READ, "a1", "electricity", 0, retired_at=_CLOSED),
        _meter(SERIAL, "a1", "electricity", 0, replaces=SERIAL_READ),
        _meter(WATER_SERIAL, "a1", "water"),
        _meter("4410290", "a2", "water"),
        _meter("5500123", "a3", "electricity", 0),
    ],
    readings={
        SERIAL_READ: [_reading(_START, OPENING, opens=_START)],
        SERIAL: [_reading(_CLOSED, CLOSING, opens=_NEXT, closes=_START)],
        WATER_SERIAL: [
            _reading(_START, WATER_OPENING, opens=_START),
            _reading("2026-10-25", 1270),
            _reading(_CLOSED, 1274, opens=_NEXT, closes=_START),
        ],
        "4410290": [_reading(_START, 820, opens=_START), _reading(_CLOSED, 850, hour="12")],
        "5500123": [_reading("2026-10-01", 12000, opens="2026-10-01")],
    },
    periods={
        "a1": [_period(_START, _CLOSED), _period(_NEXT)],
        "a2": [_period(_START)],
        "a3": [_period("2026-10-01")],
    },
    tariffs=[
        {
            "effective_date": _START,
            PRICE_KEYS["electricity"]: PRICES["electricity"],
            PRICE_KEYS["water"]: PRICES["water"],
            "source_image_key": None,
        }
    ],
    delegates=[{"email": "helper@gmail.com", "account": "owner@x.com", "role": "delegate"}],
)


@dataclass
class Records:
    state: Account
    # Key: seq of a card waiting behind the case's card; value: its form
    behind: dict
    # Key: message at of a dropped photo; value: its reading tag
    photos: dict


RECORDS = {
    "story": Records(
        _STORY,
        {"2026-10-29T10:00:00.000000_b2c3": {"value": 1300, "date": _CLOSED, "serial": "4410290"}},
        {
            # An electricity photo cancelled by mistake.
            "2026-10-29T13:00:00.000000_c3d4": {
                "state": "dropped",
                "meter_type": "electricity",
                "apartment": "עורף",
                "property": PROPERTY,
            },
            # A photo read as a previous statement.
            "2026-10-29T14:00:00.000000_d4e5": {
                "state": "dropped",
                "meter_type": None,
                "apartment": None,
                "property": None,
            },
        },
    )
}
_CONFIG = Path(__file__).resolve().parent.parent / "config" / "app.json"
# How many of a meter's latest readings the records block lists, as the engine lists them.
RECENT_READINGS = json.loads(_CONFIG.read_text())["correction_recent_readings"]

# The cards that wait on standby: typed text reaches them as a request, sent with the records.
STANDBY_CARDS = {"tariffs_ask"}
INTENTS = get_args(IntentResult.model_fields["intent"].annotation)
FIELDS = set(IntentResult.model_fields) - {"intent"}


@dataclass
class Case:
    message: str
    card: str | None
    today: date
    intent: str
    # Key: an IntentResult field; value: what the model must set it to
    fields: dict
    # The account's apartment names a request is sent with; only for no card or a standby card
    apartments: list[str]
    # The name of the RECORDS account the case is sent with
    records: str | None = None


def _case(raw: dict) -> Case:
    if raw["card"] is not None and raw["card"] not in CARDS:
        raise ValueError(f"card {raw['card']!r} is not one of {', '.join(CARDS)}")
    if raw["intent"] not in INTENTS:
        raise ValueError(f"intent {raw['intent']!r} is not one the model returns")
    fields = raw.get("fields", {})
    unknown = set(fields) - FIELDS
    if unknown:
        raise ValueError(f"fields {', '.join(sorted(unknown))} are not IntentResult fields")
    if raw["intent"] == "edit" and "operations" not in fields:
        raise ValueError("an edit case must list its operations")
    for operation in fields.get("operations", []):
        Operation.model_validate(operation)
    apartments = raw.get("apartments", [])
    if apartments and not _is_request(raw["card"]):
        raise ValueError(f"apartments go with no card or a standby card, not {raw['card']!r}")
    records = raw.get("records")
    if records is not None and records not in RECORDS:
        raise ValueError(f"records {records!r} is not one of {', '.join(RECORDS)}")
    if records is not None and apartments:
        raise ValueError("a case lists records or apartments, not both")
    today = date.fromisoformat(raw["today"])
    case = Case(raw["message"], raw["card"], today, raw["intent"], fields, apartments, records)
    known = _listed_ids(case)
    for operation in fields.get("operations", []):
        if operation["target"] not in known:
            raise ValueError(f"target {operation['target']!r} is not in the case's records block")
        if operation["action"] == "set":
            _check_value(case, operation)
    return case


def _card_form(case: Case, target: str) -> dict:
    """The form of the card a card target names: the case's own, or one waiting behind it."""
    seq = target.partition("|")[2]
    if seq in ("", CARD_SEQ):
        return {} if case.card is None else FORMS[case.card]
    return RECORDS[case.records].behind[seq]


def _field(case: Case, operation: dict) -> Field | None:
    """The field table's entry for what a set names; None for a bare value, which the card's
    default field takes. Raises ValueError for a field the target cannot take."""
    record = operation["target"].partition("|")[0]
    name = operation["field"]
    if record == "card":
        form = _card_form(case, operation["target"])
        if name is None:
            return None
        if name not in form:
            raise ValueError(f"field {name!r} is not on the card")
        # A card lists a saved reading under its id; setting it is setting that reading's value.
        return FIELD_TABLE[("reading", "value")] if "|" in name else FIELD_TABLE[("card", name)]
    if record == "photo" and name in PHOTO_FIELDS:
        record = "card"
    if (record, name) not in FIELD_TABLE:
        raise ValueError(f"field {name!r} is not one a {record} has")
    return FIELD_TABLE[(record, name)]


def _check_value(case: Case, operation: dict):
    """A value left out stays unchecked: the expectation is then that the app asks for it."""
    field = _field(case, operation)
    if field is None or operation["value"] is None:
        return
    try:
        parse(field, operation["value"], case.today)
    except InvalidValue as error:
        raise ValueError(f"value {operation['value']!r} does not fit {field.name}") from error


def _is_request(card: str | None) -> bool:
    return card is None or card in STANDBY_CARDS


def load_cases(path: Path) -> list[Case]:
    cases = []
    for number, raw in enumerate(json.loads(path.read_text()), start=1):
        try:
            cases.append(_case(raw))
        except (KeyError, ValueError) as error:
            raise ValueError(f"{path.name} case {number} ({raw.get('message')!r}): {error}")
    return cases


def _apartments_only(apartments: list[str]) -> Records:
    """An account whose one property holds these apartments, and no more."""
    rows = [
        {"id": f"a{number}", "property_id": "p1", "name": name, "period_months": 2}
        for number, name in enumerate(apartments, start=1)
    ]
    properties = [{"id": "p1", "name": PROPERTY}] if apartments else []
    return Records(Account(properties, rows, [], {}, {}, [], []), {}, {})


def _records(case: Case) -> str:
    """The case's records block, listing its card first, then the cards behind it."""
    held = _apartments_only(case.apartments) if case.records is None else RECORDS[case.records]
    own = {} if case.card is None else {CARD_SEQ: FORMS[case.card]}
    cards = {**own, **held.behind}
    return account.context(held.state, RECENT_READINGS, cards, held.photos)


def _listed_ids(case: Case) -> set[str]:
    """The targets an operation may name: the card, which is also how a bare value with no card
    is named, every id the block lists, and a record id the card lists as a field."""
    ids = {line.partition(": ")[0] for line in _records(case).splitlines()[1:]}
    fields = {} if case.card is None else FORMS[case.card]
    return {"card", *ids, *(name for name in fields if "|" in name)}


def context(case: Case) -> str:
    card = None
    if case.card is not None:
        card = classify_context(CARDS[case.card], FORMS[case.card], ASKED.get(case.card))
    return message_context(_records(case), card)


def _in_field_order(operations: list[dict]) -> list[dict]:
    return sorted(operations, key=lambda operation: str(operation["field"]))


def mismatches(case: Case, result: IntentResult) -> list[str]:
    got = result.model_dump()
    got["operations"] = _in_field_order(got["operations"])
    expected = {"intent": case.intent, **case.fields}
    if "operations" in expected:
        expected["operations"] = _in_field_order(expected["operations"])
    return [
        f"{name} {got[name]}, expected {value}"
        for name, value in expected.items()
        if got[name] != value
    ]


def evaluate(cases: list[Case], model) -> Iterator[tuple[Case, list[str]]]:
    """Each case with its mismatches; a refusal is the case's one mismatch."""
    for case in cases:
        try:
            result = model.classify(case.message, context(case), case.today)
        except ModelRefused as error:
            yield case, [f"refused: {error}"]
        else:
            yield case, mismatches(case, result)


def case_line(case: Case, problems: list[str]) -> str:
    where = case.card or "no card"
    head = f"{'FAIL' if problems else 'PASS'} {case.message!r} on {where}"
    return f"{head}: {'; '.join(problems)}" if problems else head


def report(all_problems: list[list[str]]) -> str:
    passed = sum(1 for problems in all_problems if not problems)
    return f"passed {passed}/{len(all_problems)}"


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: eval_intents.py <cases.json>", file=sys.stderr)
        sys.exit(2)
    cases = load_cases(Path(sys.argv[1]))
    try:
        model = model_from_env()
    except ValueError as error:
        print(error, file=sys.stderr)
        sys.exit(2)
    all_problems = []
    try:
        for case, problems in evaluate(cases, model):
            print(case_line(case, problems), flush=True)
            all_problems.append(problems)
    except PROVIDER_ERRORS as error:
        print(
            f"model call failed: {error.message}; check LLM_API_KEY and the provider account",
            file=sys.stderr,
        )
        sys.exit(2)
    print(report(all_problems))
    if any(all_problems):
        sys.exit(1)


if __name__ == "__main__":
    main()
