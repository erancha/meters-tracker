"""The table of fields a correction may edit, and the typed parsing around it.

Core pieces:
- FIELDS: one Field per (record, field name), with its value kind and its Hebrew label, question
  and how-to text (the wording lives in texts.py)
- parse: turns the string an Operation carries into the field's typed value, or raises
  InvalidValue
- field_for: looks a (record, field) pair up, or raises UnknownField
- parse_target / resolve: split an Operation target into a Target and check it against the ids
  the model was given, or raise UnknownTarget
"""

import math
import re
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date

from common import texts
from common.dates import parse_iso

METER_TYPES = ("water", "electricity")
# Which of the two numbers on a meter's display is its reading.
DISPLAY_POSITIONS = ("top", "bottom")
# How a photo is read: as a previous statement, or as a meter.
PHOTO_KINDS = ("statement", "meter")
FLAGS = {"true": True, "false": False}
MIN_MONTHS, MAX_MONTHS = 1, 24

# Number of `|`-separated parts after the record name, per record. A card alone means the card
# on screen, so it also allows zero.
TARGET_PARTS = {
    "card": (0, 1),
    "property": (1,),
    "apartment": (1,),
    "meter": (1,),
    "reading": (2,),
    "period": (2,),
    "tariff": (1,),
    "photo": (1,),
    "delegate": (1,),
}


class InvalidValue(Exception):
    """A value that does not fit its field's kind. current is the value on record as the field's
    question shows it, and target the operation's target, when the raiser knows them."""

    def __init__(
        self,
        field: "Field",
        raw: str | None,
        current: str | None = None,
        target: str | None = None,
    ):
        super().__init__(f"{field.record}.{field.name}: {raw!r}")
        self.field = field
        self.raw = raw
        self.current = current
        self.target = target


class UnknownField(Exception):
    """A (record, field) pair that is not in the table."""


class UnknownTarget(Exception):
    """A target that is malformed or names an id the model was not given."""


@dataclass(frozen=True)
class Field:
    record: str
    name: str
    kind: str
    key: bool  # True when the value is part of the record's own id, so editing it moves the record
    label: str
    question: str
    how_to: str
    owner_only: bool  # True when a delegate is refused setting the field


@dataclass(frozen=True)
class Target:
    """record is the target's first part; id is everything after it ("" for a bare card);
    parts is id split on "|" (a reading's serial and date#seq, a period's apartment and start)."""

    record: str
    id: str
    parts: tuple[str, ...]


# (record, field name) -> (kind, is key, text concept)
_SPEC = {
    ("card", "value"): ("whole", False, "value"),
    ("card", "date"): ("date", False, "date"),
    ("card", "serial"): ("serial", False, "serial"),
    ("card", "meter_type"): ("meter_type", False, "meter_type"),
    ("card", "property"): ("ref", False, "property"),
    ("card", "apartment"): ("ref", False, "apartment"),
    ("card", "period_start"): ("future_date", False, "period_start"),
    ("card", "months"): ("months", False, "months"),
    ("card", "electricity_price"): ("price", False, "electricity_price"),
    ("card", "water_price"): ("price", False, "water_price"),
    ("card", "effective_date"): ("future_date", False, "effective_date"),
    ("card", "delegate_address"): ("email", False, "delegate_address"),
    ("reading", "value"): ("whole", False, "value"),
    ("reading", "date"): ("date", True, "date"),
    ("reading", "serial"): ("serial", True, "serial"),
    ("reading", "resets"): ("flag", False, "resets"),
    ("meter", "serial"): ("serial", True, "serial"),
    ("meter", "type"): ("meter_type", False, "meter_type"),
    ("meter", "apartment"): ("ref", False, "apartment"),
    ("meter", "display"): ("display", False, "display"),
    ("meter", "retired_at"): ("optional_date", False, "retired_at"),
    ("property", "name"): ("name", False, "property_name"),
    ("apartment", "name"): ("name", False, "apartment_name"),
    ("apartment", "property"): ("ref", False, "property"),
    ("apartment", "months"): ("months", False, "months"),
    ("period", "start"): ("future_date", True, "period_start"),
    ("period", "months"): ("months", False, "months"),
    ("period", "closed_on"): ("optional_date", False, "closed_on"),
    ("period", "closing"): ("ref", False, "closing"),
    ("period", "vacant"): ("flag", False, "vacant"),
    ("tariff", "electricity_price"): ("price", False, "electricity_price"),
    ("tariff", "water_price"): ("price", False, "water_price"),
    ("tariff", "effective_date"): ("future_date", True, "effective_date"),
    ("delegate", "address"): ("email", True, "delegate_address"),
    ("photo", "kind"): ("photo_kind", False, "photo_kind"),
}

# Who works in an account is the owner's to decide.
_OWNER_ONLY = {("delegate", "address")}

FIELDS = {
    (record, name): Field(
        record,
        name,
        kind,
        key,
        texts.FIELD_LABELS[concept],
        texts.FIELD_QUESTIONS[concept],
        texts.FIELD_HOW_TO[concept],
        (record, name) in _OWNER_ONLY,
    )
    for (record, name), (kind, key, concept) in _SPEC.items()
}


def field_for(record: str, name: str) -> Field:
    if (record, name) not in FIELDS:
        raise UnknownField(f"{record}.{name}")
    return FIELDS[(record, name)]


def _whole(raw: str) -> int:
    text = raw.strip()
    if not re.fullmatch(r"[0-9]+", text):
        raise ValueError(raw)
    return int(text)


def _non_blank(raw: str) -> str:
    text = raw.strip()
    if not text:
        raise ValueError(raw)
    return text


def normalize_serial(raw: str) -> str:
    """A serial as stored: trimmed, each run of whitespace as one underscore, so a label typed
    with spaces and the same label read from a photo name one meter."""
    return "_".join(_non_blank(raw).split())


def _iso_date(raw: str) -> date:
    text = raw.strip()
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", text):
        raise ValueError(raw)
    return parse_iso(text)


def _future_date(raw: str) -> str:
    return _iso_date(raw).isoformat()


def _date(raw: str, today: date) -> str:
    parsed = _iso_date(raw)
    if parsed > today:
        raise ValueError(raw)
    return parsed.isoformat()


def _months(raw: str) -> int:
    months = _whole(raw)
    if not MIN_MONTHS <= months <= MAX_MONTHS:
        raise ValueError(raw)
    return months


def _price(raw: str) -> float:
    text = raw.strip()
    if not re.fullmatch(r"[0-9]+(\.[0-9]+)?", text):
        raise ValueError(raw)
    price = float(text)
    if not 0 < price < math.inf:
        raise ValueError(raw)
    return price


# The longest address mail delivery accepts.
MAX_ADDRESS_LENGTH = 254


def _email(raw: str) -> str:
    """Lowercase, as sign-in looks an address up. A target's `|` and a list's comma cannot appear
    in one."""
    text = raw.strip().lower()
    if len(text) > MAX_ADDRESS_LENGTH or not re.fullmatch(r"[^@\s|,]+@[^@\s|,]+\.[^@\s|,]+", text):
        raise ValueError(raw)
    return text


def _member(raw: str, options) -> str:
    text = raw.strip()
    if text not in options:
        raise ValueError(raw)
    return text


_PARSERS = {
    "whole": lambda raw, today: _whole(raw),
    "serial": lambda raw, today: normalize_serial(raw),
    "date": _date,
    "future_date": lambda raw, today: _future_date(raw),
    "months": lambda raw, today: _months(raw),
    "name": lambda raw, today: _non_blank(raw),
    "price": lambda raw, today: _price(raw),
    "meter_type": lambda raw, today: _member(raw, METER_TYPES),
    "display": lambda raw, today: _member(raw, DISPLAY_POSITIONS),
    "photo_kind": lambda raw, today: _member(raw, PHOTO_KINDS),
    "flag": lambda raw, today: FLAGS[_member(raw, FLAGS)],
    "ref": lambda raw, today: _non_blank(raw),
    "email": lambda raw, today: _email(raw),
}


def parse(field: Field, raw: str | None, today: date):
    """Return raw as the field's typed value. Only optional_date accepts None or a blank
    value, meaning unset. A date field rejects days after today; future_date accepts any day."""
    if field.kind == "optional_date":
        if raw is None or raw.strip() == "":
            return None
        kind_parser = _PARSERS["date"]
    elif raw is None:
        raise InvalidValue(field, raw)
    else:
        kind_parser = _PARSERS[field.kind]
    try:
        return kind_parser(raw, today)
    except ValueError as error:
        raise InvalidValue(field, raw) from error


def parse_target(raw: str) -> Target:
    record, _, rest = raw.partition("|")
    if record not in TARGET_PARTS:
        raise UnknownTarget(raw)
    allowed = TARGET_PARTS[record]
    if rest == "" and raw.endswith("|"):
        raise UnknownTarget(raw)
    parts = tuple(rest.split("|")) if rest else ()
    if len(parts) not in allowed or any(part == "" for part in parts):
        raise UnknownTarget(raw)
    return Target(record, rest, parts)


def resolve(target: Target, known_ids: Collection[str]) -> Target:
    """Return target when its id is one the model was given; a bare card needs none."""
    if target.id == "" and target.record == "card":
        return target
    if target.id not in known_ids:
        raise UnknownTarget(f"{target.record}|{target.id}")
    return target
