"""The correction matrix, through the real Engine with the fake model. Its crossing, exactly:
- records: every operation the field table and the delete table offer, in every account state,
  with every answer (a valid value then confirm, cancel, or the corrected record changed before
  the confirm; an invalid value; none; an unknown id; an unknown field); a delete takes the
  answers that carry no value or field
- waiting cards: every card kind, in every account state, with every record operation and a
  valid value confirmed; the card fields with every card kind, a valid, an invalid and no value,
  in one state per card
- the reverse pass of every set in every state, and fixed-seed sequences of several corrections
Each case runs in an account of its own inside one mocked store, emptied after it.

Core pieces:
- STATES: the seeded accounts, each with the record every operation targets in it
- matrix_cases: the cases, generated from FIELDS and DELETES, so a new field or delete joins the
  matrix with no edit here; MATRIX_SLICES, the marker slices test.sh runs side by side
- EXPECTED and the listings after it: every case whose outcome is not its answer's default,
  with the outcome it must have; a combination that is not legal is listed with its refusal,
  never skipped
- the invariants every confirmed correction must leave, units conserved among them: each stretch
  between two consecutive readings of a meter is billed in exactly one period, or in none when
  a reset or a vacate lies between them
- carry_on: past each correction, a later photo of every live meter and the close, each checked
  to have happened, so a fault that shows only on the next photo is caught
"""

import copy
import itertools
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

import boto3
import pytest
from conftest import (
    FILES_BUCKET,
    KEYED_BY_PK_AND_SK,
    KEYED_BY_PK_ONLY,
    REGION,
    mocked_aws,
    set_environment,
)
from engine_kit import Kit, choice, electricity, options, statement, water
from moto.core import DEFAULT_ACCOUNT_ID
from moto.ses.models import ses_backends

from common import account, corrections, report, texts
from common.access import DELEGATE, OWNER, User
from common.account import Account
from common.corrections import DELETES
from common.dates import next_day
from common.fields import FIELDS
from common.fields import parse_target as fields_target
from common.flows import FAILURE_FIELDS, CardGone, card_text
from common.flows import correction as correction_flow
from common.model import IntentResult, Operation
from common.paging import scan_all
from common.periods import figures, flags_in_order, period_end, period_readings
from common.store import Store

pytestmark = pytest.mark.matrix

TODAY = date(2026, 8, 20)
# Past the end of every seeded open period, so the photos there offer the close.
LATER = date(2026, 9, 5)
NOT_UNDERSTOOD = "לא הבנתי"


# ---------------------------------------------------------------------------------------------
# Account states


def _reading(serial, day, value, opens=None, closes=None):
    return {
        "date": day,
        "seq": f"{day}T08:00:00",
        "value": value,
        "image_key": f"seed/{serial}/{day}",
        "recorded_by": "seed",
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


def _period(start, closed_on=None, months=2):
    return {"start": start, "months": months, "closed_on": closed_on, "last_reminded_on": None}


def _apartment(apartment_id, property_id, name, start):
    return {
        "id": apartment_id,
        "property_id": property_id,
        "name": name,
        "period_start": start,
        "period_months": 2,
    }


TARIFF = {
    "effective_date": "2026-01-01",
    "electricity_per_kwh": 0.5,
    "water_per_m3": 10.0,
    "source_image_key": None,
}


def _base(periods, readings, meters=None) -> Account:
    """One property and apartment with a water meter W and an electricity meter E."""
    return Account(
        properties=[{"id": "p1", "name": "הגפן 7"}],
        apartments=[_apartment("a1", "p1", "עורף", periods[0]["start"])],
        meters=meters or [_meter("W", "a1", "water"), _meter("E", "a1", "electricity", 0)],
        readings=readings,
        periods={"a1": periods},
        tariffs=[dict(TARIFF)],
        delegates=[],
    )


def _open_readings(serial, base):
    return [
        _reading(serial, "2026-07-01", base, opens="2026-07-01"),
        _reading(serial, "2026-07-20", base + 30),
        _reading(serial, "2026-08-10", base + 60),
    ]


def _history_readings(serial, base, vacated=False):
    """Two periods: May-June closed on 2026-06-30, whose closing row opens July-August unless the
    apartment was vacated."""
    shared = None if vacated else "2026-07-01"
    rows = [
        _reading(serial, "2026-05-01", base, opens="2026-05-01"),
        _reading(serial, "2026-05-20", base + 30),
        _reading(serial, "2026-06-30", base + 60, opens=shared, closes="2026-05-01"),
    ]
    if not vacated:
        rows += [
            _reading(serial, "2026-07-20", base + 90),
            _reading(serial, "2026-08-10", base + 120),
        ]
    return rows


def _open(_) -> Account:
    return _base(
        [_period("2026-07-01")],
        {"W": _open_readings("W", 100), "E": _open_readings("E", 5000)},
    )


def _closed_next(_) -> Account:
    return _base(
        [_period("2026-05-01", "2026-06-30"), _period("2026-07-01")],
        {"W": _history_readings("W", 10), "E": _history_readings("E", 5000)},
    )


def _vacant(_) -> Account:
    return _base(
        [_period("2026-05-01", "2026-06-30")],
        {"W": _history_readings("W", 10, True), "E": _history_readings("E", 5000, True)},
    )


def _replaced(_) -> Account:
    """O was replaced by N on 2026-07-20, within the open period."""
    return _base(
        [_period("2026-07-01")],
        {
            "O": [
                _reading("O", "2026-07-01", 100, opens="2026-07-01"),
                _reading("O", "2026-07-15", 120),
            ],
            "N": [
                _reading("N", "2026-07-20", 5, opens="2026-07-01"),
                _reading("N", "2026-08-10", 25),
            ],
            "E": _open_readings("E", 5000),
        },
        [
            _meter("O", "a1", "water", retired_at="2026-07-20"),
            _meter("N", "a1", "water", replaces="O"),
            _meter("E", "a1", "electricity", 0),
        ],
    )


def _merge(_) -> Account:
    """Beside the open state's apartment, whose W replaced R, another property's apartment with an
    electricity meter: a serial, an apartment, a property and names to merge into."""
    state = _base(
        [_period("2026-07-01")],
        {
            "R": [
                _reading("R", "2026-07-01", 100, opens="2026-07-01"),
                _reading("R", "2026-07-08", 110),
            ],
            "W": [
                _reading("W", "2026-07-12", 0, opens="2026-07-01"),
                _reading("W", "2026-07-20", 30),
                _reading("W", "2026-08-10", 60),
            ],
            "E": _open_readings("E", 5000),
            "E2": _open_readings("E2", 7000),
        },
        [
            _meter("R", "a1", "water", retired_at="2026-07-12"),
            _meter("W", "a1", "water", replaces="R"),
            _meter("E", "a1", "electricity", 0),
            _meter("E2", "a2", "electricity", 0),
        ],
    )
    state.properties.append({"id": "p2", "name": "לאונר 18"})
    state.apartments.append(_apartment("a2", "p2", "קדמית", "2026-07-01"))
    state.periods["a2"] = [_period("2026-07-01")]
    return state


def _no_tariff(index) -> Account:
    state = _open(index)
    state.tariffs.clear()
    return state


def delegate_address(index) -> str:
    """Unique per case: the access table is shared by every account in the mocked store."""
    return f"helper-{index}@x.com"


def _with_delegate(index) -> Account:
    state = _open(index)
    state.delegates.append(
        {"email": delegate_address(index), "account": owner(index).account, "role": DELEGATE}
    )
    return state


def _rid(serial, day) -> str:
    return f"{serial}|{day}#{day}T08:00:00"


@dataclass(frozen=True)
class State:
    build: Callable[[int], Account]
    # Key: record kind; value: the id the state's operations target, the delegate's from the case
    targets: dict
    # The meter a reading moves to
    other_meter: str


def _targets(meter, reading, period, apartment="a1"):
    return {
        "property": "p1",
        "apartment": apartment,
        "meter": meter,
        "reading": reading,
        "period": period,
        "tariff": TARIFF["effective_date"],
    }


STATES = {
    "open": State(_open, _targets("W", _rid("W", "2026-07-20"), "a1|2026-07-01"), "E"),
    "closed_next": State(
        _closed_next, _targets("E", _rid("W", "2026-05-20"), "a1|2026-05-01"), "E"
    ),
    "shared_row": State(_closed_next, _targets("W", _rid("W", "2026-06-30"), "a1|2026-07-01"), "E"),
    "replaced": State(_replaced, _targets("N", _rid("N", "2026-07-20"), "a1|2026-07-01"), "O"),
    "merge_target": State(_merge, _targets("W", _rid("W", "2026-07-20"), "a1|2026-07-01"), "R"),
    "vacant": State(_vacant, _targets("E", _rid("W", "2026-05-20"), "a1|2026-05-01"), "E"),
    "no_tariff": State(_no_tariff, _targets("E", _rid("W", "2026-07-20"), "a1|2026-07-01"), "E"),
    "delegate": State(_with_delegate, _targets("W", _rid("W", "2026-08-10"), "a1|2026-07-01"), "E"),
}


def owner(index) -> User:
    address = f"owner-{index}@x.com"
    return User(email=address, account=address, role=OWNER)


# ---------------------------------------------------------------------------------------------
# Cases


@dataclass(frozen=True)
class Op:
    action: str  # "set" or "delete"
    record: str
    field: str | None

    @property
    def name(self) -> str:
        return f"{self.record}.{self.field}" if self.action == "set" else f"delete-{self.record}"


RECORD_OPS = [Op("set", r, f) for r, f in FIELDS if r != "card"] + [
    Op("delete", r, None) for r in DELETES
]
CARD_OPS = [Op("set", r, f) for r, f in FIELDS if r == "card"]

# The answers a correction can meet. confirm, cancel and changed (the records change between the
# card and its confirmation) follow a valid value; the others are the message itself.
VALUE_ANSWERS = (
    "confirm",
    "cancel",
    "changed",
    "invalid",
    "missing",
    "unknown_id",
    "unknown_field",
)
DELETE_ANSWERS = ("confirm", "cancel", "changed", "unknown_id")

# Waiting cards: none; each card kind; a card behind the first; another person's card.
CARDS = (
    "none",
    "reading",
    "unreadable",
    "link",
    "property_near",
    "apartment_near",
    "period_change",
    "tariffs",
    "tariffs_ask",
    "close",
    "question",
    "correction",
    "behind",
    "other_person",
)


@dataclass(frozen=True)
class Case:
    op: Op
    state: str
    card: str
    answer: str

    @property
    def id(self) -> str:
        return f"{self.op.name}/{self.state}/{self.card}/{self.answer}"


def _record_cases():
    for op in RECORD_OPS:
        answers = VALUE_ANSWERS if op.action == "set" else DELETE_ANSWERS
        for state in STATES:
            for answer in answers:
                yield Case(op, state, "none", answer)


def matrix_cases() -> list[Case]:
    """Every case: each record operation in each state with each answer and no waiting card;
    each waiting card with each operation; and the reverse pass of each set in each state."""
    return [*_record_cases(), *card_cases(), *reverse_cases()]


# ---------------------------------------------------------------------------------------------
# Values


def _day_before(day: str) -> str:
    return (date.fromisoformat(day) - timedelta(days=1)).isoformat()


def _target_record(state: Account, kind, target_id) -> dict | None:
    return account.records(state, kind).get(target_id)


def _value_for(op: Op, state_name: str, seeded: Account, index) -> str | None:
    """A value the field's kind accepts, chosen to change the record in this state; whether the
    change is allowed is the case's outcome."""
    state = STATES[state_name]
    key = (op.record, op.field)
    if key == ("photo", "kind"):
        return "statement"
    target = _target_id(op, state_name, index)
    record = _target_record(seeded, op.record, target)
    if key == ("reading", "value"):
        return str(record["value"] - 3)
    if key == ("reading", "date"):
        return _day_before(record["date"])
    if key == ("reading", "serial"):
        return state.other_meter
    if key == ("reading", "resets"):
        return "true"
    if key == ("meter", "serial"):
        return "R" if state_name == "merge_target" else "NEW1"
    if key == ("meter", "type"):
        return "electricity" if record["type"] == "water" else "water"
    if key == ("meter", "apartment"):
        return "a2" if state_name == "merge_target" else "a1"
    if key == ("meter", "display"):
        return "bottom"
    if key == ("meter", "retired_at"):
        if record["retired_at"] is not None:
            return None
        return seeded.readings[target][-1]["date"]
    if key == ("property", "name"):
        return "לאונר 18" if state_name == "merge_target" else "הגפן 9"
    if key == ("apartment", "name"):
        return "קדמית" if state_name == "merge_target" else "סלון"
    if key == ("apartment", "property"):
        return "p2" if state_name == "merge_target" else "p1"
    if key in (("apartment", "months"), ("period", "months")):
        return "3"
    if key == ("period", "start"):
        return next_day(record["start"])
    if key == ("period", "closed_on"):
        return None if record["closed_on"] is not None else "2026-08-15"
    if key == ("period", "closing"):
        serial = target_reading_serial(state_name)
        if serial not in seeded.readings:
            return None
        start = record["start"]
        rows = seeded.readings[serial]
        inside = [r for r in rows if r["date"] >= start and r["closes_period"] != start]
        return account.reading_id(serial, inside[min(1, len(inside) - 1)]) if inside else None
    if key == ("period", "vacant"):
        return "false" if state_name == "vacant" else "true"
    if key == ("tariff", "electricity_price"):
        return "0.7"
    if key == ("tariff", "water_price"):
        return "12"
    if key == ("tariff", "effective_date"):
        return "2026-02-01"
    if key == ("delegate", "address"):
        return f"new-{index}@x.com"
    raise AssertionError(f"no value for {key}")


# One value per kind that its parser rejects.
INVALID = {
    "whole": "שלושים",
    "serial": " ",
    "date": "2026-02-30",
    "future_date": "מחר",
    "optional_date": "31/12",
    "months": "99",
    "name": " ",
    "price": "-1",
    "meter_type": "gas",
    "display": "middle",
    "photo_kind": "video",
    "flag": "maybe",
    "ref": " ",
    "email": "not-an-address",
}

UNKNOWN_IDS = {
    "property": "zz",
    "apartment": "zz",
    "meter": "ZZ",
    "reading": _rid("W", "2001-01-01"),
    "period": "a1|2001-01-01",
    "tariff": "2001-01-01",
    "delegate": "nobody@x.com",
    "photo": "2001-01-01T00:00:00.000000_0000",
}


def target_reading_serial(state_name: str) -> str:
    """The meter of the reading the state's operations target: a period's other closing reading
    is chosen among its readings."""
    return STATES[state_name].targets["reading"].partition("|")[0]


def _target_id(op: Op, state_name: str, index, photo_at=None) -> str:
    """photo_at is the message time of the case's dropped photo, which the case seeds itself."""
    if op.record == "delegate":
        return delegate_address(index)
    if op.record == "photo":
        return photo_at
    return STATES[state_name].targets[op.record]


# ---------------------------------------------------------------------------------------------
# Expected outcomes
#
# An answer's default outcome: a valid value shows the correction card, a cancel drops it, a value
# missing or unfitting asks the field's question, an unknown id or field names what can be done.
# Every case with another outcome is listed in EXPECTED.

DEFAULTS = {
    "confirm": "card",
    "cancel": "card",
    "changed": "card",
    "invalid": "question",
    "missing": "question",
    "unknown_id": "unknown_record",
    "unknown_field": "unknown_record",
}

# Key: (operation name, state, answer); "*" in the answer stands for every answer with a valid
# value. Value: the outcome, a texts function's name for a refusal.
EXPECTED: dict[tuple[str, str, str], str] = {
    ("apartment.property", "closed_next", "*"): "nothing_to_correct",
    ("apartment.property", "delegate", "*"): "nothing_to_correct",
    ("apartment.property", "no_tariff", "*"): "nothing_to_correct",
    ("apartment.property", "open", "*"): "nothing_to_correct",
    ("apartment.property", "replaced", "*"): "nothing_to_correct",
    ("apartment.property", "shared_row", "*"): "nothing_to_correct",
    ("apartment.property", "vacant", "*"): "nothing_to_correct",
    ("delegate.address", "closed_next", "*"): "unknown_delegate",
    ("delegate.address", "closed_next", "invalid"): "unknown_delegate",
    ("delegate.address", "closed_next", "missing"): "unknown_delegate",
    ("delegate.address", "closed_next", "unknown_id"): "unknown_delegate",
    ("delegate.address", "closed_next", "unknown_field"): "unknown_delegate",
    ("delegate.address", "delegate", "unknown_id"): "unknown_delegate",
    ("delegate.address", "merge_target", "*"): "unknown_delegate",
    ("delegate.address", "merge_target", "invalid"): "unknown_delegate",
    ("delegate.address", "merge_target", "missing"): "unknown_delegate",
    ("delegate.address", "merge_target", "unknown_id"): "unknown_delegate",
    ("delegate.address", "merge_target", "unknown_field"): "unknown_delegate",
    ("delegate.address", "no_tariff", "*"): "unknown_delegate",
    ("delegate.address", "no_tariff", "invalid"): "unknown_delegate",
    ("delegate.address", "no_tariff", "missing"): "unknown_delegate",
    ("delegate.address", "no_tariff", "unknown_id"): "unknown_delegate",
    ("delegate.address", "no_tariff", "unknown_field"): "unknown_delegate",
    ("delegate.address", "open", "*"): "unknown_delegate",
    ("delegate.address", "open", "invalid"): "unknown_delegate",
    ("delegate.address", "open", "missing"): "unknown_delegate",
    ("delegate.address", "open", "unknown_id"): "unknown_delegate",
    ("delegate.address", "open", "unknown_field"): "unknown_delegate",
    ("delegate.address", "replaced", "*"): "unknown_delegate",
    ("delegate.address", "replaced", "invalid"): "unknown_delegate",
    ("delegate.address", "replaced", "missing"): "unknown_delegate",
    ("delegate.address", "replaced", "unknown_id"): "unknown_delegate",
    ("delegate.address", "replaced", "unknown_field"): "unknown_delegate",
    ("delegate.address", "shared_row", "*"): "unknown_delegate",
    ("delegate.address", "shared_row", "invalid"): "unknown_delegate",
    ("delegate.address", "shared_row", "missing"): "unknown_delegate",
    ("delegate.address", "shared_row", "unknown_id"): "unknown_delegate",
    ("delegate.address", "shared_row", "unknown_field"): "unknown_delegate",
    ("delegate.address", "vacant", "*"): "unknown_delegate",
    ("delegate.address", "vacant", "invalid"): "unknown_delegate",
    ("delegate.address", "vacant", "missing"): "unknown_delegate",
    ("delegate.address", "vacant", "unknown_id"): "unknown_delegate",
    ("delegate.address", "vacant", "unknown_field"): "unknown_delegate",
    ("delete-apartment", "closed_next", "*"): "apartment_holds_meters",
    ("delete-apartment", "delegate", "*"): "apartment_holds_meters",
    ("delete-apartment", "merge_target", "*"): "apartment_holds_meters",
    ("delete-apartment", "no_tariff", "*"): "apartment_holds_meters",
    ("delete-apartment", "open", "*"): "apartment_holds_meters",
    ("delete-apartment", "replaced", "*"): "apartment_holds_meters",
    ("delete-apartment", "shared_row", "*"): "apartment_holds_meters",
    ("delete-apartment", "vacant", "*"): "apartment_holds_meters",
    ("delete-property", "closed_next", "*"): "property_holds_apartments",
    ("delete-property", "delegate", "*"): "property_holds_apartments",
    ("delete-property", "merge_target", "*"): "property_holds_apartments",
    ("delete-property", "no_tariff", "*"): "property_holds_apartments",
    ("delete-property", "open", "*"): "property_holds_apartments",
    ("delete-property", "replaced", "*"): "property_holds_apartments",
    ("delete-property", "shared_row", "*"): "property_holds_apartments",
    ("delete-property", "vacant", "*"): "property_holds_apartments",
    ("delete-tariff", "no_tariff", "*"): "unknown_record",
    ("meter.apartment", "closed_next", "*"): "nothing_to_correct",
    ("meter.apartment", "delegate", "*"): "nothing_to_correct",
    ("meter.apartment", "no_tariff", "*"): "nothing_to_correct",
    ("meter.apartment", "open", "*"): "nothing_to_correct",
    ("meter.apartment", "replaced", "*"): "nothing_to_correct",
    ("meter.apartment", "shared_row", "*"): "nothing_to_correct",
    ("meter.apartment", "vacant", "*"): "nothing_to_correct",
    ("meter.display", "delegate", "*"): "single_counter_display",
    ("meter.display", "merge_target", "*"): "single_counter_display",
    ("meter.display", "open", "*"): "single_counter_display",
    ("meter.display", "replaced", "*"): "single_counter_display",
    ("meter.display", "shared_row", "*"): "single_counter_display",
    ("meter.retired_at", "closed_next", "missing"): "nothing_to_correct",
    ("meter.retired_at", "delegate", "missing"): "nothing_to_correct",
    ("meter.retired_at", "merge_target", "missing"): "nothing_to_correct",
    ("meter.retired_at", "no_tariff", "missing"): "nothing_to_correct",
    ("meter.retired_at", "open", "missing"): "nothing_to_correct",
    ("meter.retired_at", "replaced", "missing"): "nothing_to_correct",
    ("meter.retired_at", "shared_row", "missing"): "nothing_to_correct",
    ("meter.retired_at", "vacant", "missing"): "nothing_to_correct",
    ("meter.type", "closed_next", "*"): "live_meter_exists",
    ("meter.type", "delegate", "*"): "live_meter_exists",
    ("meter.type", "merge_target", "*"): "live_meter_exists",
    ("meter.type", "no_tariff", "*"): "live_meter_exists",
    ("meter.type", "open", "*"): "live_meter_exists",
    ("meter.type", "replaced", "*"): "live_meter_exists",
    ("meter.type", "shared_row", "*"): "live_meter_exists",
    ("meter.type", "vacant", "*"): "live_meter_exists",
    ("period.closed_on", "closed_next", "missing"): "card",
    ("period.closed_on", "delegate", "*"): "closed_on_follows_readings",
    ("period.closed_on", "delegate", "missing"): "nothing_to_correct",
    ("period.closed_on", "merge_target", "*"): "closed_on_follows_readings",
    ("period.closed_on", "merge_target", "missing"): "nothing_to_correct",
    ("period.closed_on", "no_tariff", "*"): "closed_on_follows_readings",
    ("period.closed_on", "no_tariff", "missing"): "nothing_to_correct",
    ("period.closed_on", "open", "*"): "closed_on_follows_readings",
    ("period.closed_on", "open", "missing"): "nothing_to_correct",
    ("period.closed_on", "replaced", "*"): "closed_on_follows_readings",
    ("period.closed_on", "replaced", "missing"): "nothing_to_correct",
    ("period.closed_on", "shared_row", "*"): "closed_on_follows_readings",
    ("period.closed_on", "shared_row", "missing"): "nothing_to_correct",
    ("period.closed_on", "vacant", "missing"): "card",
    # E's earlier reading closes May-June, which leaves its 30/06 reading counted in July-August.
    ("period.closing", "closed_next", "*"): "reading_outside_period",
    ("period.closing", "delegate", "*"): "no_closing_reading",
    ("period.closing", "merge_target", "*"): "no_closing_reading",
    ("period.closing", "no_tariff", "*"): "no_closing_reading",
    ("period.closing", "open", "*"): "no_closing_reading",
    ("period.closing", "replaced", "*"): "no_closing_reading",
    ("period.closing", "shared_row", "*"): "no_closing_reading",
    ("period.closing", "vacant", "*"): "reading_stops_counting",
    ("period.vacant", "closed_next", "*"): "vacate_next_holds_readings",
    ("period.vacant", "delegate", "*"): "vacate_open_period",
    ("period.vacant", "merge_target", "*"): "vacate_open_period",
    ("period.vacant", "no_tariff", "*"): "vacate_open_period",
    ("period.vacant", "open", "*"): "vacate_open_period",
    ("period.vacant", "replaced", "*"): "vacate_open_period",
    ("period.vacant", "shared_row", "*"): "vacate_open_period",
    ("photo.kind", "closed_next", "*"): "tariffs",
    ("photo.kind", "closed_next", "unknown_id"): "photo_not_reopenable",
    ("photo.kind", "closed_next", "unknown_field"): "photo_help",
    ("photo.kind", "delegate", "*"): "tariffs",
    ("photo.kind", "delegate", "unknown_id"): "photo_not_reopenable",
    ("photo.kind", "delegate", "unknown_field"): "photo_help",
    ("photo.kind", "merge_target", "*"): "tariffs",
    ("photo.kind", "merge_target", "unknown_id"): "photo_not_reopenable",
    ("photo.kind", "merge_target", "unknown_field"): "photo_help",
    ("photo.kind", "no_tariff", "*"): "tariffs",
    ("photo.kind", "no_tariff", "unknown_id"): "photo_not_reopenable",
    ("photo.kind", "no_tariff", "unknown_field"): "photo_help",
    ("photo.kind", "open", "*"): "tariffs",
    ("photo.kind", "open", "unknown_id"): "photo_not_reopenable",
    ("photo.kind", "open", "unknown_field"): "photo_help",
    ("photo.kind", "replaced", "*"): "tariffs",
    ("photo.kind", "replaced", "unknown_id"): "photo_not_reopenable",
    ("photo.kind", "replaced", "unknown_field"): "photo_help",
    ("photo.kind", "shared_row", "*"): "tariffs",
    ("photo.kind", "shared_row", "unknown_id"): "photo_not_reopenable",
    ("photo.kind", "shared_row", "unknown_field"): "photo_help",
    ("photo.kind", "vacant", "*"): "tariffs",
    ("photo.kind", "vacant", "unknown_id"): "photo_not_reopenable",
    ("photo.kind", "vacant", "unknown_field"): "photo_help",
    ("property.name", "merge_target", "*"): "duplicate_property",
    ("reading.serial", "closed_next", "*"): "same_day_clash",
    ("reading.serial", "delegate", "*"): "same_day_clash",
    ("reading.serial", "no_tariff", "*"): "same_day_clash",
    ("reading.serial", "open", "*"): "same_day_clash",
    ("reading.serial", "shared_row", "*"): "reading_outside_period+same_day_clash",
    ("reading.serial", "vacant", "*"): "same_day_clash",
    ("tariff.effective_date", "no_tariff", "*"): "unknown_record",
    ("tariff.effective_date", "no_tariff", "invalid"): "unknown_record",
    ("tariff.effective_date", "no_tariff", "missing"): "unknown_record",
    ("tariff.electricity_price", "no_tariff", "*"): "unknown_record",
    ("tariff.electricity_price", "no_tariff", "invalid"): "unknown_record",
    ("tariff.electricity_price", "no_tariff", "missing"): "unknown_record",
    ("tariff.water_price", "no_tariff", "*"): "unknown_record",
    ("tariff.water_price", "no_tariff", "invalid"): "unknown_record",
    ("tariff.water_price", "no_tariff", "missing"): "unknown_record",
}


def expected_outcome(case: Case) -> str:
    for answer in (case.answer, "*"):
        if answer == "*" and case.answer not in ("confirm", "cancel", "changed"):
            continue
        key = (case.op.name, case.state, answer)
        if key in EXPECTED:
            return EXPECTED[key]
    return DEFAULTS[case.answer]


# ---------------------------------------------------------------------------------------------
# The world: one mocked store for the module, and the texts each turn rendered


@dataclass
class World:
    """The module's mocked store and SES client, and the texts each turn rendered, as (function
    name, text) pairs."""

    store: Store
    ses: object
    rendered: list


@pytest.fixture(scope="module")
def world():
    """Every texts function records what it renders, so a reply is traced to the rule that worded
    it. The report's PDF is not drawn: the report is still built and mailed, which is all a case
    looks at, and drawing it costs more than the rest of a case."""
    with pytest.MonkeyPatch.context() as patch:
        set_environment(patch)
        rendered = []
        for name in dir(texts):
            function = getattr(texts, name)
            if callable(function) and getattr(function, "__module__", None) == texts.__name__:
                patch.setattr(texts, name, _recording(name, function, rendered))
        patch.setattr(report, "render", lambda built: b"%PDF-")
        with mocked_aws():
            yield World(Store.from_env(), boto3.client("ses"), rendered)


@pytest.fixture(autouse=True)
def released(world):
    """Empties every table and the bucket after each case. Each transaction of the mocked store
    copies its whole tables, so data kept from earlier cases would make every later case slower
    and the run's memory grow with the case count."""
    yield
    resource = boto3.resource("dynamodb")
    for name in KEYED_BY_PK_ONLY + KEYED_BY_PK_AND_SK:
        table = resource.Table(name)
        keys = [k["AttributeName"] for k in table.key_schema]
        with table.batch_writer() as batch:
            for item in scan_all(table):
                batch.delete_item(Key={k: item[k] for k in keys})
    boto3.resource("s3").Bucket(FILES_BUCKET).objects.all().delete()


def _recording(name, function, rendered):
    def recorded(*args, **kwargs):
        result = function(*args, **kwargs)
        rendered.append((name, result))
        return result

    return recorded


def _sources(rendered, replies) -> str:
    """The names of the texts functions whose words make up the replies, outermost only. A reply
    carrying a card is a waiting card shown again, unless every reply carries one."""
    plain = [r for r in replies if r.card is None] or replies
    shown = "\n".join(r.text for r in plain)
    found = {(n, t) for n, t in rendered if isinstance(t, str) and t and t in shown}
    outer = {n for n, t in found if not any(t != u and t in u for _, u in found)}
    return "+".join(sorted(outer)) or "none"


def _sent() -> int:
    return len(ses_backends[DEFAULT_ACCOUNT_ID][REGION].sent_messages)


@dataclass(frozen=True)
class Outbox:
    """The mail state a confirmation starts from: the emails sent so far and the periods whose
    report is due, as (apartment id, period start)."""

    sent: int
    due: frozenset[tuple[str, str]]


def _outbox(run: "Run") -> Outbox:
    stored = run.load()
    due = frozenset(
        (apartment_id, p["start"])
        for apartment_id, periods in stored.periods.items()
        for p in periods
        if not report.missing(run.kit.engine, run.user, apartment_id, p)
    )
    return Outbox(_sent(), due)


# ---------------------------------------------------------------------------------------------
# Running a case


class Run:
    """One case's account: the kit speaking for its owner, its seeded records and the operation."""

    counter = 0

    def __init__(self, world, case: Case):
        Run.counter += 1
        self.index = Run.counter
        self.case = case
        self.rendered = world.rendered
        self.user = owner(self.index)
        self.kit = Kit(today=TODAY, user=self.user, store=world.store, ses=world.ses)
        self.seeded = STATES[case.state].build(self.index)
        empty = Account([], [], [], {}, {}, [], [])
        self.kit.store.apply(self.user.account, account.diff(empty, self.seeded))
        for d in self.seeded.delegates:
            self.kit.store.access_put_if_absent(d["email"], d["account"], DELEGATE)

    def load(self) -> Account:
        return account.load(self.kit.store, self.user.account, OWNER)

    def say(self, intent: IntentResult):
        del self.rendered[:]
        self.kit.model.queue(intent)
        result = self.kit.say("טקסט")
        _assert_sane(self.case, result.replies)
        return result

    def choose(self, option):
        return self.choose_on(self.kit.card(), option)

    def choose_on(self, card, option):
        del self.rendered[:]
        result = self.kit.choose_on(card, option)
        _assert_sane(self.case, result.replies)
        return result

    def outcome(self, result, waiting: set) -> str:
        """What the message led to: a card it added to the queue, by SHOWN, or the texts of its
        reply. waiting holds the pending seqs from before the message."""
        new = [
            i
            for i in self.kit.store.pending(self.user.email)
            if i["seq"] not in waiting and i["kind"] in SHOWN
        ]
        if new:
            (self.shown,) = new
            return SHOWN[self.shown["kind"]]
        return _sources(self.rendered, result.replies)

    def confirm(self):
        """Confirms the card the message showed, wherever it waits in the queue."""
        item = {i["seq"]: i for i in self.kit.store.pending(self.user.email)}[self.shown["seq"]]
        del self.rendered[:]
        result = self.kit.choose_on(self.kit.engine.flow(item).card(item), "confirm")
        _assert_sane(self.case, result.replies)
        return result

    def pending_seqs(self) -> set:
        return {i["seq"] for i in self.kit.store.pending(self.user.email)}


# The pending item kinds an operation's message may add, by the outcome each stands for: a
# correction, a question for a value, and the tariffs card a dropped photo read as a statement
# becomes.
SHOWN = {"correction": "card", "question": "question", "tariffs": "tariffs"}


def _assert_sane(case, replies):
    assert replies, case.id
    for reply in replies:
        assert reply.text.strip(), case.id
        assert NOT_UNDERSTOOD not in reply.text, case.id


def _operation(run: Run, answer: str) -> Operation:
    op, case = run.case.op, run.case
    if answer == "unknown_id":
        return Operation(
            action=op.action,
            target=f"{op.record}|{UNKNOWN_IDS[op.record]}",
            field=op.field,
            value=None if op.action == "delete" else "1",
        )
    target = f"{op.record}|{_target_id(op, case.state, run.index, run.photo_at)}"
    if op.action == "delete":
        return Operation(action="delete", target=target, field=None, value=None)
    if answer == "unknown_field":
        return Operation(action="set", target=target, field="colour", value="1")
    if answer == "invalid":
        value = INVALID[FIELDS[(op.record, op.field)].kind]
    elif answer == "missing":
        value = None
    else:
        value = _value_for(op, case.state, run.seeded, run.index)
    return Operation(action="set", target=target, field=op.field, value=value)


def _edit(*operations) -> IntentResult:
    return IntentResult(intent="edit", operations=list(operations))


# ---------------------------------------------------------------------------------------------
# Invariants


def _same(a: Account, b: Account) -> bool:
    return account.diff(a, b).size() == 0


def _vacate_between(periods: list, first: str, second: str) -> bool:
    """True when a period of the list closes on a day from first up to but not including second,
    and no period starts the day after that close: a vacate between the two days, across which
    units bill to nobody."""
    starts = {p["start"] for p in periods}
    return any(
        p["closed_on"] is not None
        and first <= p["closed_on"] < second
        and next_day(p["closed_on"]) not in starts
        for p in periods
    )


def unbilled_stretches(state: Account) -> list[str]:
    """Each stretch between consecutive readings of a meter billed in no period or in more than
    one; none is billed across a reset or a vacate gap, by design."""
    faults = []
    for meter in state.meters:
        rows = state.readings[meter["serial"]]
        periods = state.periods[meter["apartment_id"]]
        slices = [period_readings(rows, p) for p in periods]
        for a, b in itertools.pairwise(rows):
            billed = sum(
                1 for inside in slices for x, y in itertools.pairwise(inside) if x is a and y is b
            )
            if b["resets"] and billed <= 1:
                continue
            if billed == 0 and _vacate_between(periods, a["date"], b["date"]):
                continue
            if billed != 1:
                faults.append(f"{meter['serial']} {a['date']}->{b['date']} billed {billed}x")
    return faults


def invariant_faults(state: Account) -> list[str]:
    faults = []
    for apartment_id, periods in state.periods.items():
        if sum(p["closed_on"] is None for p in periods) > 1:
            faults.append(f"{apartment_id}: two open periods")
        for p, following in itertools.pairwise(periods):
            if p["closed_on"] is None or following["start"] < p["closed_on"]:
                faults.append(f"{apartment_id}: {p['start']} overlaps {following['start']}")
        for p in periods:
            if p["closed_on"] is not None and p["closed_on"] < p["start"]:
                faults.append(f"{apartment_id}: {p['start']} closes before it starts")
    serials = [m["serial"] for m in state.meters]
    former = [s for m in state.meters for s in m["former_serials"]]
    if len(set(serials + former)) != len(serials) + len(former):
        faults.append(f"former serials do not resolve to one meter: {serials} {former}")
    for meter in state.meters:
        starts = {p["start"] for p in state.periods[meter["apartment_id"]]}
        rows = state.readings[meter["serial"]]
        for r in rows:
            for flag in ("opens_period", "closes_period"):
                if r[flag] is not None and r[flag] not in starts:
                    faults.append(f"{meter['serial']} {r['date']} {flag} names no period")
        for p in state.periods[meter["apartment_id"]]:
            if not flags_in_order(rows, p):
                faults.append(f"{meter['serial']} flags out of order in {p['start']}")
    if not faults:
        faults += unbilled_stretches(state)
    return faults


def _shown_figures(f) -> tuple | None:
    """The figures a card shows. A reading row also carries its failed photos, which the
    confirmation adds after the plan, and which no figure reads."""
    if f is None:
        return None
    c = f.consumption
    ends = [(r["date"], r["value"]) for r in (c.opening, c.last)]
    return (*ends, c.units, c.final, f.price, f.amount)


def _figures_faults(planned, stored: Account, today) -> list[str]:
    """Each figure on the card against the same figure computed from the stored rows."""
    faults = []
    meters = account.records(stored, "meter")
    for f in planned.figures:
        if f.after is None:
            continue
        meter = meters[f.serial]
        period = {p["start"]: p for p in stored.periods[meter["apartment_id"]]}[f.start]
        recomputed = figures(
            meter["type"], stored.readings[f.serial], period, stored.tariffs, today
        )
        if _shown_figures(recomputed) != _shown_figures(f.after):
            faults.append(f"{f.serial} {f.start}: card {f.after} stored {recomputed}")
    return faults


# ---------------------------------------------------------------------------------------------
# Past the correction: a later photo of every live meter, then a close


def _photo(meter, value):
    if meter["type"] == "water":
        return water(value, serial=meter["serial"])
    return electricity(value, value, serial=meter["serial"])


def _answer_photo(run: Run, card, last: bool) -> str:
    """The answer a meter photo's card gets on its way to a saved reading: the close is taken
    only on the apartment's last meter, so every meter has its later reading first."""
    offered = options(card)
    if last and "close" in offered:
        return "close"
    for option in ("keep_open", "confirm"):
        if option in offered:
            return option
    raise AssertionError(f"{run.case.id}: photo card {card['kind']} offers {offered}")


PHOTO_STEPS = (
    "reading",
    "duplicate",
    "swap",
    "propose_reclose",
    "open_period",
    "propose_close",
)


def _photograph(run: Run, meter, value, last: bool) -> bool:
    """Sends the meter's photo and answers its own cards until it is saved; True when the close
    was taken. A close parked for want of a reading resumes once a photo is saved: one chosen on
    a reading's card closes then with no card, any other as a close card, which is confirmed. A
    card of any other kind on the photo's way fails the case."""
    waiting = run.pending_seqs()
    parked = {
        i["seq"] for i in run.kit.store.pending(run.user.email) if i["kind"] == "close_period"
    }
    run.kit.send_photo(_photo(meter, value))
    (seq,) = run.pending_seqs() - waiting
    closed = False
    while seq in run.pending_seqs():
        item = {i["seq"]: i for i in run.kit.store.pending(run.user.email)}[seq]
        card = run.kit.engine.flow(item).card(item)
        assert card["kind"] in PHOTO_STEPS, f"{run.case.id}: later photo met {card['kind']}"
        answer = _answer_photo(run, card, last)
        closed = closed or answer == "close"
        run.choose_on(card, answer)
    card = run.kit.card()
    if card is not None and card["kind"] == "close":
        run.choose_on(card, "confirm")
        closed = True
    elif parked - run.pending_seqs():
        closed = True
    return closed


def _close(run: Run, apartment):
    """Asks for the apartment's close and confirms its card."""
    result = run.say(IntentResult(intent="close_period", name=apartment["name"]))
    card = result.snapshot.card
    shown = [r.text for r in result.replies]
    assert card is not None and card["kind"] == "close", f"{run.case.id}: close gave {shown}"
    run.choose("confirm")


def carry_on(run: Run, state: Account, day: date = LATER):
    """From the stored state, apartment by apartment, a photo of every live meter dated day, or
    its open period's last day when that is later, so the last photo offers the close; the close
    is taken, or asked for when not offered. A meter whose photo opened the period has nothing to
    close it on, so the close waits for it: then a second round of photos a day later closes it.
    Then each live meter's latest reading is its last photo, those periods are closed, and the
    invariants hold, units conserved."""
    sent, carried = {}, set()
    for apartment in state.apartments:
        live = [
            m
            for m in state.meters
            if m["apartment_id"] == apartment["id"] and m["retired_at"] is None
        ]
        if not live:
            continue
        carried.add(apartment["id"])
        current = [p for p in state.periods[apartment["id"]] if p["closed_on"] is None]
        run.kit.today = max([day, *(period_end(p) for p in current)])
        _carry_round(run, apartment, live, state, sent, bool(current))
        now = run.load()
        day_sent = run.kit.today.isoformat()
        if any(_open_before(p, day_sent) for p in now.periods[apartment["id"]]):
            run.kit.today += timedelta(days=1)
            _carry_round(run, apartment, live, now, sent, True)
    stored = run.load()
    latest = {s: (stored.readings[s][-1]["date"], stored.readings[s][-1]["value"]) for s in sent}
    assert latest == sent, run.case.id
    unclosed = [
        (apartment_id, p["start"])
        for apartment_id in carried
        for p in stored.periods[apartment_id]
        if _open_before(p, sent_day(sent, stored, apartment_id))
    ]
    assert not unclosed, f"{run.case.id}: open after the close: {unclosed}"
    faults = invariant_faults(stored)
    assert not faults, f"{run.case.id} after the later photos: {faults}"


def _carry_round(run: Run, apartment, live: list, state: Account, sent: dict, close: bool):
    """A photo of each live meter, dated the kit's today; the close when close is set."""
    closed = False
    for position, meter in enumerate(live):
        value = state.readings[meter["serial"]][-1]["value"] + 10
        sent[meter["serial"]] = (run.kit.today.isoformat(), value)
        last = position == len(live) - 1
        closed = _photograph(run, meter, value, last) or closed
    if close and not closed:
        _close(run, apartment)


def _open_before(period, day: str) -> bool:
    return period["closed_on"] is None and period["start"] < day


def sent_day(sent: dict, state: Account, apartment_id) -> str:
    """The day the apartment's later photos were dated."""
    return next(
        day
        for m in state.meters
        if m["apartment_id"] == apartment_id and m["serial"] in sent
        for day, _ in [sent[m["serial"]]]
    )


# ---------------------------------------------------------------------------------------------
# The answers


def _seed_dropped_photo(run: Run) -> str:
    """A meter photo cancelled on its card: a dropped photo a correction may read again, which
    the model reads as a statement when told to."""
    kit = run.kit
    image = f"dropped-{run.index}".encode()
    kit.register_photo(image, water(999, serial="W"), statement())
    kit.engine.turn(run.user, _photo_action(image))
    run.say(IntentResult(intent="reject"))
    (message,) = [m for m in kit.store.photo_messages(run.user.email)]
    run.photo_key = message["image_key"]
    return message["at"]


def _statement_tariff(run: Run) -> dict:
    """The row the seeded dropped photo saves when read as engine_kit's statement, but for its
    effective date, which a waiting request for tariffs sets."""
    read = statement()
    return {
        "effective_date": None,
        "electricity_per_kwh": read.electricity_per_kwh,
        "water_per_m3": read.water_per_m3,
        "source_image_key": run.photo_key,
    }


def _photo_action(image):
    from common.flows import Action

    return Action(image=image, media_type="image/jpeg")


def _prepare(run: Run):
    """Seeds what the case's operation needs beyond its state."""
    run.photo_at = _seed_dropped_photo(run) if run.case.op.record == "photo" else None


def _ops(run: Run, answer: str) -> list[Operation]:
    return [_operation(run, answer)]


def _planned(run: Run, before: Account, operations):
    return corrections.plan(before, operations, run.kit.today, OWNER)


def _expected_after(run: Run, before: Account, planned) -> Account:
    after = planned.after.copy()
    correction_flow._record_failed_photos(run.kit.engine, before, after)
    return after


def _check_saved(run: Run, before: Account, operations, outbox: Outbox):
    """After a confirmation: the tables are the plan's after, the invariants hold, the figures
    are the stored rows', and no report went out. A dropped photo read as a statement saves the
    statement's tariff instead of a plan, and that tariff sends the report of each period it
    makes due."""
    stored = run.load()
    if run.case.op.record == "photo":
        added = [t for t in stored.tariffs if t not in before.tariffs]
        assert len(added) == 1, run.case.id
        assert {**added[0], "effective_date": None} == _statement_tariff(run), run.case.id
        expected = before.copy()
        expected.tariffs.append(added[0])
        assert _same(stored, expected), run.case.id
    else:
        planned = _planned(run, before, operations)
        assert _same(stored, _expected_after(run, before, planned)), run.case.id
        assert not _figures_faults(planned, stored, run.kit.today), run.case.id
    assert not invariant_faults(stored), f"{run.case.id}: {invariant_faults(stored)}"
    completed = _outbox(run).due - outbox.due if run.case.op.record == "photo" else frozenset()
    assert _sent() == outbox.sent + len(completed), f"{run.case.id}: reports {completed} expected"
    return stored


def _show(run: Run, before: Account, operations):
    """Sends the operations; returns the result and the outcome, having checked that nothing was
    written."""
    waiting = run.pending_seqs()
    result = run.say(_edit(*operations))
    assert _same(run.load(), before), f"{run.case.id}: written before confirmation"
    return result, run.outcome(result, waiting)


def run_record_case(world, case: Case):
    run = Run(world, case)
    _prepare(run)
    answer = case.answer
    message = "missing" if answer == "missing" else answer
    if answer in ("confirm", "cancel", "changed"):
        message = "valid"
    operations = _ops(run, message)
    before = run.seeded
    _result, outcome = _show(run, before, operations)
    expected = expected_outcome(case)
    assert outcome == expected, f"{case.id}: {outcome} != {expected}"
    if outcome not in ("card", "tariffs"):
        if outcome == "question":
            run.say(IntentResult(intent="reject"))
        assert _same(run.load(), before), case.id
        return run
    outbox = _outbox(run)
    if answer == "cancel":
        run.say(IntentResult(intent="reject"))
        assert _same(run.load(), before), case.id
        return run
    if answer == "changed":
        before = _change_target(run, before)
        del run.rendered[:]
        replies = run.confirm().replies
        found = _confirmed(run, replies)
        expected = EXPECTED_CHANGED.get((case.op.name, case.state), "reshown")
        assert found == expected, f"{case.id}: {found} != {expected}"
        if found not in ("saved", "reshown"):
            assert _same(run.load(), before), f"{case.id}: written after {found}"
            carry_on(run, before)
            return run
        if found == "reshown":
            assert _same(run.load(), before), f"{case.id}: written on a changed card"
            run.confirm()
    else:
        run.confirm()
    carry_on(run, _check_saved(run, before, operations, outbox))
    return run


def _confirmed(run: Run, replies) -> str:
    """What a confirmation did: saved, shown again on changed records, or dropped as gone."""
    text = replies[0].text
    if texts.correction_changed() in text:
        return "reshown"
    if texts.correction_gone() in text:
        return "gone"
    if texts.correction_saved() in text or run.case.op.record == "photo":
        return "saved"
    return _sources(run.rendered, replies)


def _change_target(run: Run, before: Account) -> Account:
    """Changes the corrected record itself behind the card's back, as another person's correction
    would. A field that is part of the record's id moves the record (a reading's date, a meter's
    serial, a period's start, a tariff's date, a delegate's address), so the card's record is
    gone. Any other field changes what the card shows: the field's own value where it is stored
    (a reading's value, a meter's display or retirement, a name, a length, a price), else the
    value of the reading the card reads (a reset, a period's closing reading or vacancy). A
    dropped photo holds no saved record, so nothing changes."""
    changed = before.copy()
    op = run.case.op
    target_id = _target_id(op, run.case.state, run.index, run.photo_at)
    if op.record == "photo":
        return changed
    record = account.records(changed, op.record)[target_id]
    key = op.action == "set" and FIELDS[(op.record, op.field)].key
    if op.record == "reading" and key:
        record["date"] = _day_before(_day_before(record["date"]))
    elif op.record == "reading":
        record["value"] += 1
    elif op.record == "meter" and key:
        changed.readings[f"{target_id}-2"] = changed.readings.pop(target_id)
        record["serial"] = f"{target_id}-2"
    elif op.record == "meter" and op.field == "display":
        record["display_choice"] = 1 - record["display_choice"]
    elif op.record == "meter":
        record["retired_at"] = _day_before(changed.readings[target_id][-1]["date"])
    elif op.record in ("property", "apartment") and op.field != "months":
        record["name"] += " ב"
    elif op.record == "apartment":
        for p in changed.periods[target_id]:
            if p["closed_on"] is None:
                p["months"] = 4
        record["period_months"] = 4
    elif op.record == "period" and key:
        apartment_id = target_id.partition("|")[0]
        account.move_period(changed, apartment_id, record, next_day(next_day(record["start"])))
    elif op.record == "period":
        record["months"] = 5
    elif op.record == "tariff" and key:
        record["effective_date"] = "2026-01-02"
    elif op.record == "tariff":
        if op.field == "electricity_price":
            record["electricity_per_kwh"] = 0.55
        else:
            record["water_per_m3"] = 11.0
    elif op.record == "delegate":
        changed.delegates.remove(record)
    run.kit.store.apply(run.user.account, account.diff(before, changed))
    return changed


# Key: (operation name, state); value: what confirming does once the corrected record changed,
# where it is not "reshown" (the card shown again with the current records, nothing written):
# "gone" when the change moved the record, "saved" when the card the change would show is the
# same one, so the replanned change is written, or the refusal the replan meets.
EXPECTED_CHANGED: dict[tuple[str, str], str] = {
    ("delegate.address", "delegate"): "unknown_delegate",
    ("delete-meter", "closed_next"): "saved",
    ("delete-meter", "delegate"): "saved",
    ("delete-meter", "no_tariff"): "saved",
    ("delete-meter", "open"): "saved",
    ("delete-meter", "shared_row"): "saved",
    ("delete-meter", "vacant"): "saved",
    ("meter.apartment", "merge_target"): "saved",
    ("meter.display", "closed_next"): "nothing_to_correct",
    ("meter.display", "no_tariff"): "nothing_to_correct",
    ("meter.display", "vacant"): "nothing_to_correct",
    ("meter.serial", "closed_next"): "gone",
    ("meter.serial", "delegate"): "gone",
    ("meter.serial", "merge_target"): "gone",
    ("meter.serial", "no_tariff"): "gone",
    ("meter.serial", "open"): "gone",
    ("meter.serial", "replaced"): "gone",
    ("meter.serial", "shared_row"): "gone",
    ("meter.serial", "vacant"): "gone",
    ("period.closed_on", "closed_next"): "saved",
    ("period.closed_on", "vacant"): "saved",
    ("period.start", "closed_next"): "gone",
    ("period.start", "delegate"): "gone",
    ("period.start", "merge_target"): "gone",
    ("period.start", "no_tariff"): "gone",
    ("period.start", "open"): "gone",
    ("period.start", "replaced"): "gone",
    ("period.start", "shared_row"): "gone",
    ("period.start", "vacant"): "gone",
    ("period.vacant", "vacant"): "saved",
    ("photo.kind", "closed_next"): "saved",
    ("photo.kind", "delegate"): "saved",
    ("photo.kind", "merge_target"): "saved",
    ("photo.kind", "no_tariff"): "saved",
    ("photo.kind", "open"): "saved",
    ("photo.kind", "replaced"): "saved",
    ("photo.kind", "shared_row"): "saved",
    ("photo.kind", "vacant"): "saved",
    ("reading.date", "closed_next"): "gone",
    ("reading.date", "delegate"): "gone",
    ("reading.date", "merge_target"): "gone",
    ("reading.date", "no_tariff"): "gone",
    ("reading.date", "open"): "gone",
    ("reading.date", "replaced"): "gone",
    ("reading.date", "shared_row"): "gone",
    ("reading.date", "vacant"): "gone",
    ("reading.resets", "replaced"): "saved",
    ("reading.serial", "merge_target"): "gone",
    ("reading.serial", "replaced"): "gone",
    ("tariff.effective_date", "closed_next"): "gone",
    ("tariff.effective_date", "delegate"): "gone",
    ("tariff.effective_date", "merge_target"): "gone",
    ("tariff.effective_date", "open"): "gone",
    ("tariff.effective_date", "replaced"): "gone",
    ("tariff.effective_date", "shared_row"): "gone",
    ("tariff.effective_date", "vacant"): "gone",
}


# Slices: one per state for the record grid; CARD_PARTS per state for the waiting-card grid;
# FIELD_PARTS for card fields; REVERSE_PARTS for the reverse pass; one for the sequences. Each
# is sized to finish within a few minutes on its own, and test.sh runs them side by side.
CARD_PARTS, FIELD_PARTS, REVERSE_PARTS = 5, 8, 3
# The quick matrix, run by test.sh --matrix-records: every operation and answer in every state,
# with no waiting card.
QUICK_SLICES = tuple(f"matrix_records_{state}" for state in STATES)
MATRIX_SLICES = (
    *QUICK_SLICES,
    *(f"matrix_cards_{state}_{n}" for state in STATES for n in range(1, CARD_PARTS + 1)),
    *(f"matrix_card_fields_{n}" for n in range(1, FIELD_PARTS + 1)),
    *(f"matrix_reverse_{n}" for n in range(1, REVERSE_PARTS + 1)),
    "matrix_sequences",
)


def slice_of(case: Case) -> str:
    """The marker slice a case runs in."""
    if case.answer == "reverse":
        return f"matrix_reverse_{REVERSIBLE.index(case.op) % REVERSE_PARTS + 1}"
    if case.op.record == "card":
        pair = CARD_OPS.index(case.op) * len(CARDS) + CARDS.index(case.card)
        return f"matrix_card_fields_{pair % FIELD_PARTS + 1}"
    if case.card != "none":
        return f"matrix_cards_{case.state}_{RECORD_OPS.index(case.op) % CARD_PARTS + 1}"
    return f"matrix_records_{case.state}"


def _params(cases) -> list:
    params = []
    for case in cases:
        name = slice_of(case)
        assert name in MATRIX_SLICES, name
        params.append(pytest.param(case, id=case.id, marks=getattr(pytest.mark, name)))
    return params


# ---------------------------------------------------------------------------------------------
# Waiting cards
#
# Every card kind meets every record operation in every state, with a valid value confirmed: the
# correction comes first, and the waiting card shown after it, to the owner at once and to
# another person when their chat loads, must be the one its flow builds from the stored records.
# The card fields meet every card kind, with a valid, an invalid and no value, in one state per
# card (FIELD_STATES), since a card field edits the card and not the records. A card a
# state cannot hold (a period-change card needs a vacant apartment, a request for tariffs a close
# with no tariff in force, a close card an open period) is checked to be refused at its setup.
# Past either, the waiting card is answered, which for a meter photo is a photo dated past the
# period's end, so it offers the close; then the carry, a week later.

FIELD_STATES = {"period_change": "vacant", "tariffs_ask": "no_tariff"}
AFTER = LATER + timedelta(days=7)


def field_state(card: str) -> str:
    return FIELD_STATES.get(card, "delegate")


def _delegate(run: Run) -> User:
    return User(email=delegate_address(run.index), account=run.user.account, role=DELEGATE)


def _photo_turn(run: Run, user: User, result):
    image = f"card-{run.index}-{len(run.kit._photos)}".encode()
    run.kit.register_photo(image, result)
    return run.kit.engine.turn(user, _photo_action(image))


def _water_meter(state: Account) -> dict:
    """The live water meter of the first apartment, which the photo cards photograph."""
    return next(
        m
        for m in state.meters
        if m["apartment_id"] == "a1" and m["type"] == "water" and m["retired_at"] is None
    )


# Per card, the pending item kind its setup leaves and the step it waits on, None for any step.
WAITING = {
    "reading": ("meter_photo", "reading"),
    "unreadable": ("meter_photo", "unreadable"),
    "link": ("link_meter", None),
    "property_near": ("link_meter", "property_near"),
    "apartment_near": ("link_meter", "apartment_near"),
    "period_change": ("period_change", None),
    "tariffs": ("tariffs", "confirm"),
    "tariffs_ask": ("tariffs", "ask"),
    "close": ("close_period", None),
    "question": ("question", None),
    "correction": ("correction", None),
    "behind": ("link_meter", None),
    "other_person": ("meter_photo", "reading"),
}


def card_possible(card: str, state: Account) -> bool:
    """Whether the state can hold the card, from its records."""
    open_periods = [p for p in state.periods["a1"] if p["closed_on"] is None]
    if card == "period_change":
        return not open_periods
    if card == "tariffs_ask":
        return not state.tariffs and bool(open_periods)
    if card == "close":
        return bool(open_periods)
    return True


def _set_up_card(run: Run, card: str):
    """Leaves the case's waiting card in place, or checks that the state cannot hold it; the seq
    of a card behind the first is kept for a card field's operation to name."""
    say, owner_user = run.say, run.user
    meter = _water_meter(run.seeded)
    value = run.seeded.readings[meter["serial"]][-1]["value"] + 15
    if card in ("reading", "behind"):
        _photo_turn(run, owner_user, water(value, serial=meter["serial"]))
    if card == "unreadable":
        _photo_turn(run, owner_user, water(value, serial=meter["serial"], unreadable=True))
    if card in ("link", "behind", "property_near", "apartment_near"):
        _photo_turn(run, owner_user, water(40, serial="X1"))
    # Prefixes of the seeded "הגפן 7" and "עורף", which every state holds.
    if card == "property_near":
        say(_edit(Operation(action="set", target="card", field="property", value="הגפן")))
    if card == "apartment_near":
        say(_edit(Operation(action="set", target="card", field="property", value="הגפן 7")))
        say(_edit(Operation(action="set", target="card", field="apartment", value="עור")))
    if card == "period_change":
        say(IntentResult(intent="change_period", name="עורף", date=run.kit.today.isoformat()))
    if card == "tariffs":
        say(IntentResult(intent="set_tariffs", electricity_price=0.66, water_price=13))
    if card in ("close", "tariffs_ask"):
        say(IntentResult(intent="close_period", name="עורף"))
    if card == "tariffs_ask" and card_possible(card, run.seeded):
        run.choose("confirm")
    if card == "question":
        target = f"reading|{STATES[run.case.state].targets['reading']}"
        say(_edit(Operation(action="set", target=target, field="value", value="שלושים")))
    if card == "correction":
        target = "property|p1"
        say(_edit(Operation(action="set", target=target, field="name", value="הגפן 11")))
    if card == "other_person":
        _photo_turn(run, _delegate(run), water(value, serial=meter["serial"]))
    user = _delegate(run) if card == "other_person" else owner_user
    pending = run.kit.store.pending(user.email)
    kind, step = WAITING[card]
    held = [i for i in pending if i["kind"] == kind and step in (None, i["step"])]
    assert bool(held) == card_possible(card, run.seeded), f"{run.case.id}: set up {pending}"
    run.behind = pending[-1]["seq"] if card == "behind" else None


# A valid value for each card field; whether the waiting card takes it is the case's outcome.
CARD_VALUES = {
    "value": "176",
    "date": "2026-08-19",
    "serial": "W",
    "meter_type": "water",
    "property": "הגפן 7",
    "apartment": "עורף",
    "period_start": "2026-08-19",
    "months": "3",
    "electricity_price": "0.67",
    "water_price": "14",
    "effective_date": "2026-08-01",
}

CARD_ANSWERS = ("valid", "invalid", "missing")


def card_cases() -> list[Case]:
    cases = [
        Case(op, state, card, "confirm")
        for card in CARDS[1:]
        for state in STATES
        for op in RECORD_OPS
    ]
    cases += [
        Case(op, field_state(card), card, answer)
        for card in CARDS[1:]
        for op in CARD_OPS
        for answer in CARD_ANSWERS
    ]
    return cases


def _card_operation(run: Run, answer: str) -> Operation:
    target = "card" if run.behind is None else f"card|{run.behind}"
    field = run.case.op.field
    value = {"valid": CARD_VALUES[field], "invalid": INVALID[FIELDS[("card", field)].kind]}.get(
        answer
    )
    return Operation(action="set", target=target, field=field, value=value)


def _card_outcome(run: Run, result) -> str:
    card = result.snapshot.card
    kind = "none" if card is None else card["kind"]
    return f"{kind}:{_sources(run.rendered, result.replies)}"


def _waiting_answer(card) -> str | None:
    """The option that moves a waiting card on, or None for a card answered by a typed cancel."""
    offered = options(card)
    for option in ("close", "keep_open", "confirm"):
        if option in offered and card["kind"] not in ("correction",):
            return option
    return None


def _answer_waiting(run: Run, user: User):
    """Answers every card waiting for user until none is left: a meter photo's card is saved,
    so its photo is the later photo past the correction; a card that cannot be confirmed is
    cancelled. A stale card is shown again with the records as they now stand, and answered
    again."""
    kit = run.kit
    for _ in range(12):
        card = kit.engine.snapshot(user).card
        if card is None:
            return
        option = _waiting_answer(card)
        del run.rendered[:]
        if option is None:
            kit.model.queue(IntentResult(intent="reject"))
            result = kit.engine.turn(user, _text_action("בטל"))
        else:
            result = kit.engine.turn(user, choice(card, option))
        _assert_sane(run.case, result.replies)
    raise AssertionError(f"{run.case.id}: cards still waiting for {user.email}")


def _text_action(text):
    from common.flows import Action

    return Action(text=text)


def _shown(card) -> tuple:
    return card_text(card), card["options"]


def _staleness(run: Run, user: User, card) -> str | None:
    """Why card, shown to user after the confirmation, differs from the one its flow builds from
    the stored records, or None when it does not."""
    if card is None:
        return None
    item = {i["seq"]: i for i in run.kit.store.pending(user.email)}[card["pending"]]
    fresh = copy.deepcopy(item)
    try:
        run.kit.engine.flow(fresh).refresh(run.kit.engine, user, fresh)
    except CardGone:
        return f"{card['kind']} card shown though its records are gone"
    built = run.kit.engine.flow(fresh).card(fresh)
    if _shown(card) != _shown(built):
        return f"{card['kind']} card shown with records as they were"
    return None


def run_card_case(world, case: Case):
    run = Run(world, case)
    if case.op.record != "card":
        run.kit.today = LATER
    _prepare(run)
    if case.card == "other_person":
        run.kit.store.access_put_if_absent(delegate_address(run.index), run.user.account, DELEGATE)
    _set_up_card(run, case.card)
    if not card_possible(case.card, run.seeded):
        return run
    before = run.load()
    if case.op.record == "card":
        operations = [_card_operation(run, case.answer)]
        result = run.say(_edit(*operations))
        assert _same(run.load(), before), f"{case.id}: written before confirmation"
        outcome = _card_outcome(run, result)
    else:
        operations = _ops(run, "valid")
        result, outcome = _show(run, before, operations)
    expected = expected_card_outcome(case)
    assert outcome == expected, f"{case.id}: {outcome} != {expected}"
    if outcome in ("card", "tariffs"):
        outbox = _outbox(run)
        returned = run.confirm().snapshot.card
        _check_saved(run, before, operations, outbox)
        if case.card == "other_person":
            # The other person is shown the card when their chat loads.
            returned = run.kit.engine.load(_delegate(run))[1].card
        user = _delegate(run) if case.card == "other_person" else run.user
        stale = _staleness(run, user, returned)
        assert stale is None, f"{case.id}: {stale}"
    _answer_waiting(run, run.user)
    if case.card == "other_person":
        _answer_waiting(run, _delegate(run))
    carry_on(run, run.load(), LATER if case.op.record == "card" else AFTER)
    return run


def expected_card_outcome(case: Case) -> str:
    if case.op.record != "card":
        return EXPECTED_WITH_CARD.get((case.op.name, case.card), expected_outcome(case))
    return EXPECTED_CARDS.get((case.op.field, case.card, case.answer), "?")


# A record operation whose outcome a waiting card's own setup changes. Key: (operation name,
# card); value: the outcome. A request for tariffs waits only after a close, so the state's open
# period is closed by then and its vacancy can be corrected; an earlier closing reading there
# leaves the later one counted in the next period, as in closed_next.
EXPECTED_WITH_CARD = {
    ("period.closing", "tariffs_ask"): "reading_outside_period",
    ("period.vacant", "tariffs_ask"): "card",
    # Another person's card makes that person a delegate in every state.
    ("delegate.address", "other_person"): "card",
}

# Key: (card field, waiting card, answer); value: the kind of the card shown after the message,
# then the texts of its reply. A card takes the fields its form holds; any other is answered with
# what the card takes, and with no card waiting a bare value is asked about.
EXPECTED_CARDS: dict[tuple[str, str, str], str] = {
    ("apartment", "behind", "invalid"): "reading:field_value",
    ("apartment", "behind", "missing"): "reading:field_value",
    ("apartment", "behind", "valid"): "reading:meter_name",
    ("apartment", "close", "invalid"): "close:card_help",
    ("apartment", "close", "missing"): "close:card_help",
    ("apartment", "close", "valid"): "close:card_help",
    ("apartment", "correction", "invalid"): "correction:card_help",
    ("apartment", "correction", "missing"): "correction:card_help",
    ("apartment", "correction", "valid"): "correction:card_help",
    ("apartment", "link", "invalid"): "property:field_value",
    ("apartment", "link", "missing"): "property:field_value",
    ("apartment", "link", "valid"): "property:meter_name",
    ("apartment", "other_person", "invalid"): "none:bare_input",
    ("apartment", "other_person", "missing"): "none:options_reply",
    ("apartment", "other_person", "valid"): "none:bare_input",
    ("apartment", "period_change", "invalid"): "change_period:card_help",
    ("apartment", "period_change", "missing"): "change_period:card_help",
    ("apartment", "period_change", "valid"): "change_period:card_help",
    ("apartment", "question", "invalid"): "question:card_help",
    ("apartment", "question", "missing"): "question:card_help",
    ("apartment", "question", "valid"): "question:card_help",
    ("apartment", "reading", "invalid"): "reading:card_help",
    ("apartment", "reading", "missing"): "reading:card_help",
    ("apartment", "reading", "valid"): "reading:card_help",
    ("apartment", "tariffs", "invalid"): "tariffs:card_help",
    ("apartment", "tariffs", "missing"): "tariffs:card_help",
    ("apartment", "tariffs", "valid"): "tariffs:card_help",
    ("apartment", "tariffs_ask", "invalid"): "tariffs_ask:card_help",
    ("apartment", "tariffs_ask", "missing"): "tariffs_ask:card_help",
    ("apartment", "tariffs_ask", "valid"): "tariffs_ask:card_help",
    ("apartment", "unreadable", "invalid"): "unreadable:card_help",
    ("apartment", "unreadable", "missing"): "unreadable:card_help",
    ("apartment", "unreadable", "valid"): "unreadable:card_help",
    ("date", "behind", "invalid"): "reading:field_value",
    ("date", "behind", "missing"): "reading:field_value",
    ("date", "behind", "valid"): "reading:meter_name",
    ("date", "close", "invalid"): "close:card_help",
    ("date", "close", "missing"): "close:card_help",
    ("date", "close", "valid"): "close:card_help",
    ("date", "correction", "invalid"): "correction:card_help",
    ("date", "correction", "missing"): "correction:card_help",
    ("date", "correction", "valid"): "correction:card_help",
    ("date", "link", "invalid"): "property:field_value",
    ("date", "link", "missing"): "property:field_value",
    ("date", "link", "valid"): "property:meter_name",
    ("date", "other_person", "invalid"): "none:bare_input",
    ("date", "other_person", "missing"): "none:options_reply",
    ("date", "other_person", "valid"): "none:bare_input",
    ("date", "period_change", "invalid"): "change_period:card_help",
    ("date", "period_change", "missing"): "change_period:card_help",
    ("date", "period_change", "valid"): "change_period:card_help",
    ("date", "question", "invalid"): "question:card_help",
    ("date", "question", "missing"): "question:card_help",
    ("date", "question", "valid"): "question:card_help",
    ("date", "reading", "invalid"): "reading:field_value",
    ("date", "reading", "missing"): "reading:field_value",
    ("date", "reading", "valid"): "reading:meter_name+number",
    ("date", "tariffs", "invalid"): "tariffs:card_help",
    ("date", "tariffs", "missing"): "tariffs:card_help",
    ("date", "tariffs", "valid"): "tariffs:card_help",
    ("date", "tariffs_ask", "invalid"): "tariffs_ask:card_help",
    ("date", "tariffs_ask", "missing"): "tariffs_ask:card_help",
    ("date", "tariffs_ask", "valid"): "tariffs_ask:card_help",
    ("date", "unreadable", "invalid"): "unreadable:field_value",
    ("date", "unreadable", "missing"): "unreadable:field_value",
    ("date", "unreadable", "valid"): "unreadable:none",
    ("effective_date", "behind", "invalid"): "reading:card_help",
    ("effective_date", "behind", "missing"): "reading:card_help",
    ("effective_date", "behind", "valid"): "reading:card_help",
    ("effective_date", "close", "invalid"): "close:card_help",
    ("effective_date", "close", "missing"): "close:card_help",
    ("effective_date", "close", "valid"): "close:card_help",
    ("effective_date", "correction", "invalid"): "correction:card_help",
    ("effective_date", "correction", "missing"): "correction:card_help",
    ("effective_date", "correction", "valid"): "correction:card_help",
    ("effective_date", "link", "invalid"): "property:card_help",
    ("effective_date", "link", "missing"): "property:card_help",
    ("effective_date", "link", "valid"): "property:card_help",
    ("effective_date", "other_person", "invalid"): "none:bare_input",
    ("effective_date", "other_person", "missing"): "none:options_reply",
    ("effective_date", "other_person", "valid"): "none:bare_input",
    ("effective_date", "period_change", "invalid"): "change_period:card_help",
    ("effective_date", "period_change", "missing"): "change_period:card_help",
    ("effective_date", "period_change", "valid"): "change_period:card_help",
    ("effective_date", "question", "invalid"): "question:card_help",
    ("effective_date", "question", "missing"): "question:card_help",
    ("effective_date", "question", "valid"): "question:card_help",
    ("effective_date", "reading", "invalid"): "reading:card_help",
    ("effective_date", "reading", "missing"): "reading:card_help",
    ("effective_date", "reading", "valid"): "reading:card_help",
    ("effective_date", "tariffs", "invalid"): "tariffs:field_value",
    ("effective_date", "tariffs", "missing"): "tariffs:field_value",
    ("effective_date", "tariffs", "valid"): "tariffs:price_text",
    ("effective_date", "tariffs_ask", "invalid"): "tariffs_ask:field_value",
    ("effective_date", "tariffs_ask", "missing"): "tariffs_ask:field_value",
    ("effective_date", "tariffs_ask", "valid"): "tariffs:price_text",
    ("effective_date", "unreadable", "invalid"): "unreadable:card_help",
    ("effective_date", "unreadable", "missing"): "unreadable:card_help",
    ("effective_date", "unreadable", "valid"): "unreadable:card_help",
    ("electricity_price", "behind", "invalid"): "reading:card_help",
    ("electricity_price", "behind", "missing"): "reading:card_help",
    ("electricity_price", "behind", "valid"): "reading:card_help",
    ("electricity_price", "close", "invalid"): "close:card_help",
    ("electricity_price", "close", "missing"): "close:card_help",
    ("electricity_price", "close", "valid"): "close:card_help",
    ("electricity_price", "correction", "invalid"): "correction:card_help",
    ("electricity_price", "correction", "missing"): "correction:card_help",
    ("electricity_price", "correction", "valid"): "correction:card_help",
    ("electricity_price", "link", "invalid"): "property:card_help",
    ("electricity_price", "link", "missing"): "property:card_help",
    ("electricity_price", "link", "valid"): "property:card_help",
    ("electricity_price", "other_person", "invalid"): "none:bare_input",
    ("electricity_price", "other_person", "missing"): "none:options_reply",
    ("electricity_price", "other_person", "valid"): "none:bare_input",
    ("electricity_price", "period_change", "invalid"): "change_period:card_help",
    ("electricity_price", "period_change", "missing"): "change_period:card_help",
    ("electricity_price", "period_change", "valid"): "change_period:card_help",
    ("electricity_price", "question", "invalid"): "question:card_help",
    ("electricity_price", "question", "missing"): "question:card_help",
    ("electricity_price", "question", "valid"): "question:card_help",
    ("electricity_price", "reading", "invalid"): "reading:card_help",
    ("electricity_price", "reading", "missing"): "reading:card_help",
    ("electricity_price", "reading", "valid"): "reading:card_help",
    ("electricity_price", "tariffs", "invalid"): "tariffs:field_value",
    ("electricity_price", "tariffs", "missing"): "tariffs:field_value",
    ("electricity_price", "tariffs", "valid"): "tariffs:price_text",
    ("electricity_price", "tariffs_ask", "invalid"): "tariffs_ask:field_value",
    ("electricity_price", "tariffs_ask", "missing"): "tariffs_ask:field_value",
    ("electricity_price", "tariffs_ask", "valid"): "tariffs:price_text",
    ("electricity_price", "unreadable", "invalid"): "unreadable:card_help",
    ("electricity_price", "unreadable", "missing"): "unreadable:card_help",
    ("electricity_price", "unreadable", "valid"): "unreadable:card_help",
    ("meter_type", "behind", "invalid"): "reading:field_value",
    ("meter_type", "behind", "missing"): "reading:field_value",
    ("meter_type", "behind", "valid"): "reading:meter_name",
    ("meter_type", "close", "invalid"): "close:card_help",
    ("meter_type", "close", "missing"): "close:card_help",
    ("meter_type", "close", "valid"): "close:card_help",
    ("meter_type", "correction", "invalid"): "correction:card_help",
    ("meter_type", "correction", "missing"): "correction:card_help",
    ("meter_type", "correction", "valid"): "correction:card_help",
    ("meter_type", "link", "invalid"): "property:field_value",
    ("meter_type", "link", "missing"): "property:field_value",
    ("meter_type", "link", "valid"): "property:meter_name",
    ("meter_type", "other_person", "invalid"): "none:bare_input",
    ("meter_type", "other_person", "missing"): "none:options_reply",
    ("meter_type", "other_person", "valid"): "none:bare_input",
    ("meter_type", "period_change", "invalid"): "change_period:card_help",
    ("meter_type", "period_change", "missing"): "change_period:card_help",
    ("meter_type", "period_change", "valid"): "change_period:card_help",
    ("meter_type", "question", "invalid"): "question:card_help",
    ("meter_type", "question", "missing"): "question:card_help",
    ("meter_type", "question", "valid"): "question:card_help",
    ("meter_type", "reading", "invalid"): "reading:card_help",
    ("meter_type", "reading", "missing"): "reading:card_help",
    ("meter_type", "reading", "valid"): "reading:card_help",
    ("meter_type", "tariffs", "invalid"): "tariffs:card_help",
    ("meter_type", "tariffs", "missing"): "tariffs:card_help",
    ("meter_type", "tariffs", "valid"): "tariffs:card_help",
    ("meter_type", "tariffs_ask", "invalid"): "tariffs_ask:card_help",
    ("meter_type", "tariffs_ask", "missing"): "tariffs_ask:card_help",
    ("meter_type", "tariffs_ask", "valid"): "tariffs_ask:card_help",
    ("meter_type", "unreadable", "invalid"): "unreadable:field_value",
    ("meter_type", "unreadable", "missing"): "unreadable:field_value",
    ("meter_type", "unreadable", "valid"): "unreadable:none",
    ("months", "behind", "invalid"): "reading:field_value",
    ("months", "behind", "missing"): "reading:field_value",
    ("months", "behind", "valid"): "reading:meter_name",
    ("months", "close", "invalid"): "close:card_help",
    ("months", "close", "missing"): "close:card_help",
    ("months", "close", "valid"): "close:card_help",
    ("months", "correction", "invalid"): "correction:card_help",
    ("months", "correction", "missing"): "correction:card_help",
    ("months", "correction", "valid"): "correction:card_help",
    ("months", "link", "invalid"): "property:field_value",
    ("months", "link", "missing"): "property:field_value",
    ("months", "link", "valid"): "property:meter_name",
    ("months", "other_person", "invalid"): "none:bare_input",
    ("months", "other_person", "missing"): "none:options_reply",
    ("months", "other_person", "valid"): "none:bare_input",
    ("months", "period_change", "invalid"): "change_period:field_value",
    ("months", "period_change", "missing"): "change_period:field_value",
    ("months", "period_change", "valid"): "change_period:months_phrase",
    ("months", "question", "invalid"): "question:card_help",
    ("months", "question", "missing"): "question:card_help",
    ("months", "question", "valid"): "question:card_help",
    ("months", "reading", "invalid"): "reading:card_help",
    ("months", "reading", "missing"): "reading:card_help",
    ("months", "reading", "valid"): "reading:card_help",
    ("months", "tariffs", "invalid"): "tariffs:card_help",
    ("months", "tariffs", "missing"): "tariffs:card_help",
    ("months", "tariffs", "valid"): "tariffs:card_help",
    ("months", "tariffs_ask", "invalid"): "tariffs_ask:card_help",
    ("months", "tariffs_ask", "missing"): "tariffs_ask:card_help",
    ("months", "tariffs_ask", "valid"): "tariffs_ask:card_help",
    ("months", "unreadable", "invalid"): "unreadable:card_help",
    ("months", "unreadable", "missing"): "unreadable:card_help",
    ("months", "unreadable", "valid"): "unreadable:card_help",
    ("period_start", "behind", "invalid"): "reading:field_value",
    ("period_start", "behind", "missing"): "reading:field_value",
    ("period_start", "behind", "valid"): "reading:meter_name",
    ("period_start", "close", "invalid"): "close:card_help",
    ("period_start", "close", "missing"): "close:card_help",
    ("period_start", "close", "valid"): "close:card_help",
    ("period_start", "correction", "invalid"): "correction:card_help",
    ("period_start", "correction", "missing"): "correction:card_help",
    ("period_start", "correction", "valid"): "correction:card_help",
    ("period_start", "link", "invalid"): "property:field_value",
    ("period_start", "link", "missing"): "property:field_value",
    ("period_start", "link", "valid"): "property:meter_name",
    ("period_start", "other_person", "invalid"): "none:bare_input",
    ("period_start", "other_person", "missing"): "none:options_reply",
    ("period_start", "other_person", "valid"): "none:bare_input",
    ("period_start", "period_change", "invalid"): "change_period:field_value",
    ("period_start", "period_change", "missing"): "change_period:field_value",
    ("period_start", "period_change", "valid"): "change_period:months_phrase",
    ("period_start", "question", "invalid"): "question:card_help",
    ("period_start", "question", "missing"): "question:card_help",
    ("period_start", "question", "valid"): "question:card_help",
    ("period_start", "reading", "invalid"): "reading:card_help",
    ("period_start", "reading", "missing"): "reading:card_help",
    ("period_start", "reading", "valid"): "reading:card_help",
    ("period_start", "tariffs", "invalid"): "tariffs:card_help",
    ("period_start", "tariffs", "missing"): "tariffs:card_help",
    ("period_start", "tariffs", "valid"): "tariffs:card_help",
    ("period_start", "tariffs_ask", "invalid"): "tariffs_ask:card_help",
    ("period_start", "tariffs_ask", "missing"): "tariffs_ask:card_help",
    ("period_start", "tariffs_ask", "valid"): "tariffs_ask:card_help",
    ("period_start", "unreadable", "invalid"): "unreadable:card_help",
    ("period_start", "unreadable", "missing"): "unreadable:card_help",
    ("period_start", "unreadable", "valid"): "unreadable:card_help",
    ("property", "behind", "invalid"): "reading:field_value",
    ("property", "behind", "missing"): "reading:field_value",
    ("property", "behind", "valid"): "reading:none",
    ("property", "close", "invalid"): "close:card_help",
    ("property", "close", "missing"): "close:card_help",
    ("property", "close", "valid"): "close:card_help",
    ("property", "correction", "invalid"): "correction:card_help",
    ("property", "correction", "missing"): "correction:card_help",
    ("property", "correction", "valid"): "correction:card_help",
    ("property", "link", "invalid"): "property:field_value",
    ("property", "link", "missing"): "property:field_value",
    ("property", "link", "valid"): "apartment:none",
    ("property", "other_person", "invalid"): "none:bare_input",
    ("property", "other_person", "missing"): "none:options_reply",
    ("property", "other_person", "valid"): "none:bare_input",
    ("property", "period_change", "invalid"): "change_period:card_help",
    ("property", "period_change", "missing"): "change_period:card_help",
    ("property", "period_change", "valid"): "change_period:card_help",
    ("property", "question", "invalid"): "question:card_help",
    ("property", "question", "missing"): "question:card_help",
    ("property", "question", "valid"): "question:card_help",
    ("property", "reading", "invalid"): "reading:card_help",
    ("property", "reading", "missing"): "reading:card_help",
    ("property", "reading", "valid"): "reading:card_help",
    ("property", "tariffs", "invalid"): "tariffs:card_help",
    ("property", "tariffs", "missing"): "tariffs:card_help",
    ("property", "tariffs", "valid"): "tariffs:card_help",
    ("property", "tariffs_ask", "invalid"): "tariffs_ask:card_help",
    ("property", "tariffs_ask", "missing"): "tariffs_ask:card_help",
    ("property", "tariffs_ask", "valid"): "tariffs_ask:card_help",
    ("property", "unreadable", "invalid"): "unreadable:card_help",
    ("property", "unreadable", "missing"): "unreadable:card_help",
    ("property", "unreadable", "valid"): "unreadable:card_help",
    ("serial", "behind", "invalid"): "reading:field_value",
    ("serial", "behind", "missing"): "reading:field_value",
    ("serial", "behind", "valid"): "reading:meter_name+number",
    ("serial", "close", "invalid"): "close:card_help",
    ("serial", "close", "missing"): "close:card_help",
    ("serial", "close", "valid"): "close:card_help",
    ("serial", "correction", "invalid"): "correction:card_help",
    ("serial", "correction", "missing"): "correction:card_help",
    ("serial", "correction", "valid"): "correction:card_help",
    ("serial", "link", "invalid"): "property:field_value",
    ("serial", "link", "missing"): "property:field_value",
    ("serial", "link", "valid"): "reading:meter_name+number",
    ("serial", "other_person", "invalid"): "none:bare_input",
    ("serial", "other_person", "missing"): "none:options_reply",
    ("serial", "other_person", "valid"): "none:bare_input",
    ("serial", "period_change", "invalid"): "change_period:card_help",
    ("serial", "period_change", "missing"): "change_period:card_help",
    ("serial", "period_change", "valid"): "change_period:card_help",
    ("serial", "question", "invalid"): "question:card_help",
    ("serial", "question", "missing"): "question:card_help",
    ("serial", "question", "valid"): "question:card_help",
    ("serial", "reading", "invalid"): "reading:field_value",
    ("serial", "reading", "missing"): "reading:field_value",
    ("serial", "reading", "valid"): "reading:meter_name+number",
    ("serial", "tariffs", "invalid"): "tariffs:card_help",
    ("serial", "tariffs", "missing"): "tariffs:card_help",
    ("serial", "tariffs", "valid"): "tariffs:card_help",
    ("serial", "tariffs_ask", "invalid"): "tariffs_ask:card_help",
    ("serial", "tariffs_ask", "missing"): "tariffs_ask:card_help",
    ("serial", "tariffs_ask", "valid"): "tariffs_ask:card_help",
    ("serial", "unreadable", "invalid"): "unreadable:field_value",
    ("serial", "unreadable", "missing"): "unreadable:field_value",
    ("serial", "unreadable", "valid"): "unreadable:none",
    ("value", "behind", "invalid"): "reading:field_value",
    ("value", "behind", "missing"): "reading:field_value",
    ("value", "behind", "valid"): "reading:meter_name",
    ("value", "close", "invalid"): "close:card_help",
    ("value", "close", "missing"): "close:card_help",
    ("value", "close", "valid"): "close:card_help",
    ("value", "correction", "invalid"): "correction:card_help",
    ("value", "correction", "missing"): "correction:card_help",
    ("value", "correction", "valid"): "correction:card_help",
    ("value", "link", "invalid"): "property:field_value",
    ("value", "link", "missing"): "property:field_value",
    ("value", "link", "valid"): "property:meter_name",
    ("value", "other_person", "invalid"): "none:bare_input",
    ("value", "other_person", "missing"): "none:options_reply",
    ("value", "other_person", "valid"): "none:bare_input",
    ("value", "period_change", "invalid"): "change_period:card_help",
    ("value", "period_change", "missing"): "change_period:card_help",
    ("value", "period_change", "valid"): "change_period:card_help",
    ("value", "question", "invalid"): "question:field_value",
    ("value", "question", "missing"): "question:field_value",
    ("value", "question", "valid"): "correction:change_line+figure_line",
    ("value", "reading", "invalid"): "reading:field_value+number",
    ("value", "reading", "missing"): "reading:field_value+number",
    ("value", "reading", "valid"): "reading:meter_name+number",
    ("value", "tariffs", "invalid"): "tariffs:card_help",
    ("value", "tariffs", "missing"): "tariffs:card_help",
    ("value", "tariffs", "valid"): "tariffs:card_help",
    ("value", "tariffs_ask", "invalid"): "tariffs_ask:card_help",
    ("value", "tariffs_ask", "missing"): "tariffs_ask:card_help",
    ("value", "tariffs_ask", "valid"): "tariffs_ask:card_help",
    ("value", "unreadable", "invalid"): "unreadable:field_value",
    ("value", "unreadable", "missing"): "unreadable:field_value",
    ("value", "unreadable", "valid"): "reading:meter_name+number",
    ("water_price", "behind", "invalid"): "reading:card_help",
    ("water_price", "behind", "missing"): "reading:card_help",
    ("water_price", "behind", "valid"): "reading:card_help",
    ("water_price", "close", "invalid"): "close:card_help",
    ("water_price", "close", "missing"): "close:card_help",
    ("water_price", "close", "valid"): "close:card_help",
    ("water_price", "correction", "invalid"): "correction:card_help",
    ("water_price", "correction", "missing"): "correction:card_help",
    ("water_price", "correction", "valid"): "correction:card_help",
    ("water_price", "link", "invalid"): "property:card_help",
    ("water_price", "link", "missing"): "property:card_help",
    ("water_price", "link", "valid"): "property:card_help",
    ("water_price", "other_person", "invalid"): "none:bare_input",
    ("water_price", "other_person", "missing"): "none:options_reply",
    ("water_price", "other_person", "valid"): "none:bare_input",
    ("water_price", "period_change", "invalid"): "change_period:card_help",
    ("water_price", "period_change", "missing"): "change_period:card_help",
    ("water_price", "period_change", "valid"): "change_period:card_help",
    ("water_price", "question", "invalid"): "question:card_help",
    ("water_price", "question", "missing"): "question:card_help",
    ("water_price", "question", "valid"): "question:card_help",
    ("water_price", "reading", "invalid"): "reading:card_help",
    ("water_price", "reading", "missing"): "reading:card_help",
    ("water_price", "reading", "valid"): "reading:card_help",
    ("water_price", "tariffs", "invalid"): "tariffs:field_value",
    ("water_price", "tariffs", "missing"): "tariffs:field_value",
    ("water_price", "tariffs", "valid"): "tariffs:price_text",
    ("water_price", "tariffs_ask", "invalid"): "tariffs_ask:field_value",
    ("water_price", "tariffs_ask", "missing"): "tariffs_ask:field_value",
    ("water_price", "tariffs_ask", "valid"): "tariffs:price_text",
    ("water_price", "unreadable", "invalid"): "unreadable:card_help",
    ("water_price", "unreadable", "missing"): "unreadable:card_help",
    ("water_price", "unreadable", "valid"): "unreadable:card_help",
    # A near-miss question keeps asking until a name settles it: an exact name moves the link on
    # and a known serial makes the photo that meter's reading; a value that does not fit is asked
    # again, a price is not the card's, and any other field is kept with the question shown again.
    ("apartment", "apartment_near", "invalid"): "apartment_near:field_value",
    ("apartment", "apartment_near", "missing"): "apartment_near:field_value",
    ("apartment", "apartment_near", "valid"): "replaced:meter_name+months_phrase+number",
    ("apartment", "property_near", "invalid"): "property_near:field_value",
    ("apartment", "property_near", "missing"): "property_near:field_value",
    ("apartment", "property_near", "valid"): "property_near:none",
    ("date", "apartment_near", "invalid"): "apartment_near:field_value",
    ("date", "apartment_near", "missing"): "apartment_near:field_value",
    ("date", "apartment_near", "valid"): "apartment_near:none",
    ("date", "property_near", "invalid"): "property_near:field_value",
    ("date", "property_near", "missing"): "property_near:field_value",
    ("date", "property_near", "valid"): "property_near:none",
    ("effective_date", "apartment_near", "invalid"): "apartment_near:card_help",
    ("effective_date", "apartment_near", "missing"): "apartment_near:card_help",
    ("effective_date", "apartment_near", "valid"): "apartment_near:card_help",
    ("effective_date", "property_near", "invalid"): "property_near:card_help",
    ("effective_date", "property_near", "missing"): "property_near:card_help",
    ("effective_date", "property_near", "valid"): "property_near:card_help",
    ("electricity_price", "apartment_near", "invalid"): "apartment_near:card_help",
    ("electricity_price", "apartment_near", "missing"): "apartment_near:card_help",
    ("electricity_price", "apartment_near", "valid"): "apartment_near:card_help",
    ("electricity_price", "property_near", "invalid"): "property_near:card_help",
    ("electricity_price", "property_near", "missing"): "property_near:card_help",
    ("electricity_price", "property_near", "valid"): "property_near:card_help",
    ("meter_type", "apartment_near", "invalid"): "apartment_near:field_value",
    ("meter_type", "apartment_near", "missing"): "apartment_near:field_value",
    ("meter_type", "apartment_near", "valid"): "apartment_near:none",
    ("meter_type", "property_near", "invalid"): "property_near:field_value",
    ("meter_type", "property_near", "missing"): "property_near:field_value",
    ("meter_type", "property_near", "valid"): "property_near:none",
    ("months", "apartment_near", "invalid"): "apartment_near:field_value",
    ("months", "apartment_near", "missing"): "apartment_near:field_value",
    ("months", "apartment_near", "valid"): "apartment_near:none",
    ("months", "property_near", "invalid"): "property_near:field_value",
    ("months", "property_near", "missing"): "property_near:field_value",
    ("months", "property_near", "valid"): "property_near:none",
    ("period_start", "apartment_near", "invalid"): "apartment_near:field_value",
    ("period_start", "apartment_near", "missing"): "apartment_near:field_value",
    ("period_start", "apartment_near", "valid"): "apartment_near:none",
    ("period_start", "property_near", "invalid"): "property_near:field_value",
    ("period_start", "property_near", "missing"): "property_near:field_value",
    ("period_start", "property_near", "valid"): "property_near:none",
    ("property", "apartment_near", "invalid"): "apartment_near:field_value",
    ("property", "apartment_near", "missing"): "apartment_near:field_value",
    ("property", "apartment_near", "valid"): "apartment_near:none",
    ("property", "property_near", "invalid"): "property_near:field_value",
    ("property", "property_near", "missing"): "property_near:field_value",
    ("property", "property_near", "valid"): "apartment:none",
    ("serial", "apartment_near", "invalid"): "apartment_near:field_value",
    ("serial", "apartment_near", "missing"): "apartment_near:field_value",
    ("serial", "apartment_near", "valid"): "reading:meter_name+number",
    ("serial", "property_near", "invalid"): "property_near:field_value",
    ("serial", "property_near", "missing"): "property_near:field_value",
    ("serial", "property_near", "valid"): "reading:meter_name+number",
    ("value", "apartment_near", "invalid"): "apartment_near:field_value",
    ("value", "apartment_near", "missing"): "apartment_near:field_value",
    ("value", "apartment_near", "valid"): "apartment_near:none",
    ("value", "property_near", "invalid"): "property_near:field_value",
    ("value", "property_near", "missing"): "property_near:field_value",
    ("value", "property_near", "valid"): "property_near:none",
    ("water_price", "apartment_near", "invalid"): "apartment_near:card_help",
    ("water_price", "apartment_near", "missing"): "apartment_near:card_help",
    ("water_price", "apartment_near", "valid"): "apartment_near:card_help",
    ("water_price", "property_near", "invalid"): "property_near:card_help",
    ("water_price", "property_near", "missing"): "property_near:card_help",
    ("water_price", "property_near", "valid"): "property_near:card_help",
}


@pytest.mark.parametrize("case", _params(card_cases()))
def test_card_case(world, case):
    run_card_case(world, case)


# ---------------------------------------------------------------------------------------------
# The reverse pass: a confirmed set, then the set of the old value on the record as it now is,
# restores every table exactly. A delete has no inverse, and a dropped photo read again is no
# saved record.

# What a correction and its inverse leave behind by design, per (record, field), as (table,
# attribute) pairs: the photo of a reading whose value or serial changed stays among its failed
# photos with the model that failed on it, and a renamed meter keeps the serial it had, its
# readings' photos counted as failed.
FAILED = [("readings", attribute) for attribute in FAILURE_FIELDS]
RESIDUE = {
    ("reading", "value"): FAILED,
    ("reading", "serial"): FAILED,
    ("meter", "serial"): [*FAILED, ("meters", "former_serials")],
}

# Corrections no inverse undoes, by design. Key: (operation name, state); value: why.
NOT_INVERTIBLE = {
    ("meter.serial", "merge_target"): "a serial another meter holds merges the two meters",
}


def _renamed_target(op: Op, target: str, value: str) -> str:
    """The id the record has after a set of its key field to value."""
    parts = target.split("|")
    if (op.record, op.field) == ("reading", "date"):
        serial, key = parts
        seq = key.partition("#")[2]
        return f"{serial}|{value}#{seq}"
    if (op.record, op.field) == ("reading", "serial"):
        return f"{value}|{parts[1]}"
    if (op.record, op.field) == ("period", "start"):
        return f"{parts[0]}|{value}"
    return value.strip().lower() if op.record == "delegate" else value


def _as_value(value) -> str | None:
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def _inverse(run: Run, operation: Operation) -> Operation:
    op = run.case.op
    field = FIELDS[(op.record, op.field)]
    target = fields_target(operation.target)
    record = account.records(run.seeded, target.record)[target.id]
    old = corrections._typed(run.seeded, target, field, record, operation.value)
    new_id = target.id
    if field.key:
        new_id = _renamed_target(op, target.id, operation.value)
    return Operation(
        action="set", target=f"{op.record}|{new_id}", field=op.field, value=_as_value(old)
    )


def _without(state: Account, table: str, attribute: str) -> Account:
    stripped = state.copy()
    rows = account._rows(stripped)[table]
    for _, record in rows:
        record.pop(attribute, None)
    return stripped


REVERSIBLE = [op for op in RECORD_OPS if op.action == "set" and op.record != "photo"]


def reverse_cases() -> list[Case]:
    return [Case(op, state, "none", "reverse") for op in REVERSIBLE for state in STATES]


def run_reverse_case(world, case: Case):
    run = Run(world, case)
    run.photo_at = None
    operation = _operation(run, "valid")
    before = run.seeded
    _, outcome = _show(run, before, [operation])
    if outcome != "card":
        assert outcome == expected_outcome(Case(case.op, case.state, "none", "confirm")), case.id
        return run
    run.confirm()
    inverse = _inverse(run, operation)
    corrected = run.load()
    _result, outcome = _show(run, corrected, [inverse])
    expected = EXPECTED_REVERSE.get((case.op.name, case.state), "card")
    assert outcome == expected, f"{case.id}: inverse {outcome} != {expected}"
    if outcome != "card":
        carry_on(run, corrected)
        return run
    run.confirm()
    stored, seeded = run.load(), run.seeded
    assert not invariant_faults(stored), f"{case.id}: {invariant_faults(stored)}"
    if (case.op.name, case.state) in NOT_INVERTIBLE:
        assert not _same(stored, seeded), f"{case.id} is listed as not invertible"
    else:
        left, right = stored, seeded
        for table, attribute in RESIDUE.get((case.op.record, case.op.field), []):
            left, right = _without(left, table, attribute), _without(right, table, attribute)
        assert _same(left, right), f"{case.id}: {account.diff(right, left)}"
    carry_on(run, stored)
    return run


# Key: (operation name, state); value: the inverse's outcome where it is not a correction card.
# A reopen has no inverse by typing: a closing date follows the closing readings, so the refusal
# names the closing reading as the way to close again.
EXPECTED_REVERSE = {
    ("period.closed_on", "closed_next"): "closed_on_follows_readings",
    ("period.closed_on", "vacant"): "closed_on_follows_readings",
}


@pytest.mark.parametrize("case", _params(reverse_cases()))
def test_reverse_case(world, case):
    run_reverse_case(world, case)


# ---------------------------------------------------------------------------------------------
# Fixed-seed sequences: several corrections in a row on one account, each confirmed one
# followed by the invariants, then the later photos. A failure names its seed, which replays it.

SEEDS = range(16)
STEPS = 5


def run_sequence(world, seed: int):
    rng = random.Random(seed)
    state_name = rng.choice(sorted(STATES))
    ops = [op for op in RECORD_OPS if op.record != "photo"]
    picks = [rng.choice(ops) for _ in range(STEPS)]
    case = Case(picks[0], state_name, "none", f"seed {seed}")
    run = Run(world, case)
    run.photo_at = None
    for step, op in enumerate(picks):
        run.case = Case(op, state_name, "none", f"seed {seed} step {step}")
        current = run.load()
        run.seeded = current
        target = _target_id(op, state_name, run.index)
        if target not in account.records(current, op.record):
            continue
        operation = _operation(run, "valid")
        _result, outcome = _show(run, current, [operation])
        if outcome != "card":
            assert _same(run.load(), current), run.case.id
            continue
        outbox = _outbox(run)
        run.confirm()
        _check_saved(run, current, [operation], outbox)
    carry_on(run, run.load())


@pytest.mark.matrix_sequences
@pytest.mark.parametrize("seed", SEEDS)
def test_sequence(world, seed):
    run_sequence(world, seed)


@pytest.mark.parametrize("case", _params(_record_cases()))
def test_record_case(world, case):
    run_record_case(world, case)
