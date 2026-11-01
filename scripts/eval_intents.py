"""Runs the recorded chat messages through the model named by LLM_MODEL and checks the intent it
classifies each into. The logic behind scripts/eval-intents.sh, which passes the case file.

A case is a message, the card it answers (by name, or null for a message with no card), the day
it is typed on, the expected intent and the expected fields; only the fields a case lists are
compared. The cards are rendered by texts.py, as the engine renders them, and sent the way the
engine sends them, so a wording change reaches the evaluation too: a card awaiting an answer with
its button labels; a request no card takes (no card, or a standby card) with the account's
apartment names the case lists.

- CARDS: the cards a case may name, from the owner's story
- load_cases: reads and validates the case file, naming the case that is wrong
- context / mismatches / evaluate: one case's classifier context, its differences from the
  expectation, and the run over all cases, yielding each as soon as the model answers
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
    WATER_SERIAL,
)

from common import texts
from common.flows import classify_context, make_card, request_context
from common.model import PROVIDER_ERRORS, IntentResult, ModelRefused, model_from_env

TWO_NUMBERS = {"top": OPENING, "bottom": OPENING + BOTTOM_OFFSET}


def _card(text, options=(), labels=None) -> dict:
    return make_card({"seq": "eval", "version": 0}, "eval", text, list(options), labels)


CARDS = {
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
}

# The cards that wait on standby: typed text reaches them as a request, sent with the apartments.
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


def _case(raw: dict) -> Case:
    if raw["card"] is not None and raw["card"] not in CARDS:
        raise ValueError(f"card {raw['card']!r} is not one of {', '.join(CARDS)}")
    if raw["intent"] not in INTENTS:
        raise ValueError(f"intent {raw['intent']!r} is not one the model returns")
    fields = raw.get("fields", {})
    unknown = set(fields) - FIELDS
    if unknown:
        raise ValueError(f"fields {', '.join(sorted(unknown))} are not IntentResult fields")
    apartments = raw.get("apartments", [])
    if apartments and not _is_request(raw["card"]):
        raise ValueError(f"apartments go with no card or a standby card, not {raw['card']!r}")
    today = date.fromisoformat(raw["today"])
    return Case(raw["message"], raw["card"], today, raw["intent"], fields, apartments)


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


def context(case: Case) -> str:
    if not _is_request(case.card):
        return classify_context(CARDS[case.card])
    return request_context(case.apartments, None if case.card is None else CARDS[case.card])


def mismatches(case: Case, result: IntentResult) -> list[str]:
    got = result.model_dump()
    expected = {"intent": case.intent, **case.fields}
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
