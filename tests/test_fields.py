"""The field table: typed parsing of operation values and parsing of operation targets."""

from datetime import date

import pytest

from common.fields import (
    FIELDS,
    InvalidValue,
    Target,
    UnknownField,
    UnknownTarget,
    field_for,
    parse,
    parse_target,
    resolve,
)

TODAY = date(2026, 11, 1)

# One field per kind: kind -> (record, field)
BY_KIND = {
    "whole": ("reading", "value"),
    "serial": ("reading", "serial"),
    "date": ("reading", "date"),
    "months": ("period", "months"),
    "name": ("property", "name"),
    "price": ("tariff", "electricity_price"),
    "meter_type": ("meter", "type"),
    "flag": ("period", "vacant"),
    "ref": ("card", "apartment"),
    "optional_date": ("meter", "retired_at"),
    "future_date": ("period", "start"),
    "display": ("meter", "display"),
    "email": ("delegate", "address"),
    "photo_kind": ("photo", "kind"),
}


@pytest.mark.parametrize(
    "kind, raw, expected",
    [
        ("whole", "39269", 39269),
        ("whole", " 0 ", 0),
        ("serial", " 12-345 ", "12-345"),
        ("date", "2026-11-01", "2026-11-01"),
        ("months", "1", 1),
        ("months", "24", 24),
        ("name", " עורף ", "עורף"),
        ("price", "0.65", 0.65),
        ("meter_type", "water", "water"),
        ("meter_type", "electricity", "electricity"),
        ("flag", "true", True),
        ("flag", "false", False),
        ("ref", " apt-1 ", "apt-1"),
        ("optional_date", "2026-10-01", "2026-10-01"),
        ("optional_date", "", None),
        ("optional_date", None, None),
        ("display", "top", "top"),
        ("display", " bottom ", "bottom"),
        ("optional_date", "  ", None),
        ("future_date", "2026-11-02", "2026-11-02"),
        ("email", " New.Person@X.com ", "new.person@x.com"),
        ("future_date", "2026-11-01", "2026-11-01"),
        ("photo_kind", "statement", "statement"),
        ("photo_kind", " meter ", "meter"),
    ],
)
def test_parse_valid(kind, raw, expected):
    assert parse(FIELDS[BY_KIND[kind]], raw, TODAY) == expected


@pytest.mark.parametrize(
    "kind, raw",
    [
        ("whole", "39269.3"),
        ("whole", "-1"),
        ("whole", "abc"),
        ("whole", ""),
        ("whole", None),
        ("serial", "  "),
        ("date", "2026-11-02"),
        ("date", "01/11/2026"),
        ("months", "0"),
        ("months", "25"),
        ("months", "1.5"),
        ("name", " "),
        ("price", "0"),
        ("price", "-1"),
        ("price", "nan"),
        ("price", "inf"),
        ("meter_type", "gas"),
        ("flag", "yes"),
        ("flag", "True"),
        ("ref", " "),
        ("optional_date", "2026-11-02"),
        ("optional_date", "soon"),
        ("display", "middle"),
        ("display", "עליון"),
        ("display", ""),
        ("display", None),
        ("price", "1_000"),
        ("price", "1e3"),
        ("price", "١٢"),
        ("date", "20261031"),
        ("date", "2026-02-30"),
        ("future_date", "20261102"),
        ("future_date", "2026-02-30"),
        ("future_date", "soon"),
        ("photo_kind", "חשבון"),
        ("photo_kind", None),
    ],
)
def test_parse_invalid(kind, raw):
    field = FIELDS[BY_KIND[kind]]
    with pytest.raises(InvalidValue) as raised:
        parse(field, raw, TODAY)
    assert raised.value.field is field
    assert raised.value.raw == raw


def test_every_kind_is_covered():
    assert {f.kind for f in FIELDS.values()} == set(BY_KIND)


def test_every_field_has_texts():
    for (record, name), field in FIELDS.items():
        assert (field.record, field.name) == (record, name)
        assert field.label and field.question and field.how_to


@pytest.mark.parametrize(
    "raw, record, id_, parts",
    [
        ("card", "card", "", ()),
        ("card|2026-10-31T10:00:00#7", "card", "2026-10-31T10:00:00#7", ("2026-10-31T10:00:00#7",)),
        ("property|p1", "property", "p1", ("p1",)),
        ("apartment|a1", "apartment", "a1", ("a1",)),
        ("meter|SN-1", "meter", "SN-1", ("SN-1",)),
        ("reading|SN-1|2026-10-31#09:00:01", "reading", "SN-1|2026-10-31#09:00:01",
         ("SN-1", "2026-10-31#09:00:01")),
        ("period|a1|2026-10-01", "period", "a1|2026-10-01", ("a1", "2026-10-01")),
        ("tariff|2026-10-01", "tariff", "2026-10-01", ("2026-10-01",)),
        ("photo|2026-10-31T10:00:00", "photo", "2026-10-31T10:00:00", ("2026-10-31T10:00:00",)),
    ],
)  # fmt: skip
def test_parse_target(raw, record, id_, parts):
    assert parse_target(raw) == Target(record, id_, parts)


@pytest.mark.parametrize(
    "raw",
    ["", "house|x", "property", "property|", "property|a|b", "reading|SN-1", "period|a1", "card|"],
)
def test_malformed_target_is_rejected(raw):
    with pytest.raises(UnknownTarget):
        parse_target(raw)


def test_resolve_accepts_known_id():
    target = parse_target("apartment|a1")
    assert resolve(target, {"a1", "a2"}) == target


def test_unknown_target_is_rejected():
    with pytest.raises(UnknownTarget):
        resolve(parse_target("apartment|a9"), {"a1"})


def test_bare_card_needs_no_known_id():
    assert resolve(parse_target("card"), set()).record == "card"


def test_unknown_field_is_rejected():
    assert field_for("card", "value") is FIELDS[("card", "value")]
    with pytest.raises(UnknownField):
        field_for("card", "colour")


def test_tomorrow_is_rejected_for_a_reading_and_accepted_for_a_period_start():
    with pytest.raises(InvalidValue):
        parse(FIELDS[("reading", "date")], "2026-11-02", TODAY)
    assert parse(FIELDS[("period", "start")], "2026-11-02", TODAY) == "2026-11-02"


# (record, field) -> (kind, key), pinned for every entry so a wrong kind fails the suite
EXPECTED_KINDS = {
    ("card", "value"): ("whole", False),
    ("card", "date"): ("date", False),
    ("card", "serial"): ("serial", False),
    ("card", "meter_type"): ("meter_type", False),
    ("card", "property"): ("ref", False),
    ("card", "apartment"): ("ref", False),
    ("card", "period_start"): ("future_date", False),
    ("card", "months"): ("months", False),
    ("card", "electricity_price"): ("price", False),
    ("card", "water_price"): ("price", False),
    ("card", "effective_date"): ("future_date", False),
    ("reading", "value"): ("whole", False),
    ("reading", "date"): ("date", True),
    ("reading", "serial"): ("serial", True),
    ("reading", "resets"): ("flag", False),
    ("meter", "serial"): ("serial", True),
    ("meter", "type"): ("meter_type", False),
    ("meter", "apartment"): ("ref", False),
    ("meter", "display"): ("display", False),
    ("meter", "retired_at"): ("optional_date", False),
    ("property", "name"): ("name", False),
    ("apartment", "name"): ("name", False),
    ("apartment", "property"): ("ref", False),
    ("apartment", "months"): ("months", False),
    ("period", "start"): ("future_date", True),
    ("period", "months"): ("months", False),
    ("period", "closed_on"): ("optional_date", False),
    ("period", "closing"): ("ref", False),
    ("period", "vacant"): ("flag", False),
    ("tariff", "electricity_price"): ("price", False),
    ("tariff", "water_price"): ("price", False),
    ("tariff", "effective_date"): ("future_date", True),
    ("delegate", "address"): ("email", True),
    ("photo", "kind"): ("photo_kind", False),
}


def test_every_entry_has_its_kind_and_key():
    assert {k: (f.kind, f.key) for k, f in FIELDS.items()} == EXPECTED_KINDS


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "  ",
        "no-at-sign",
        "a@b",
        "a b@x.com",
        "a@x.com|b",
        "a@@x.com",
        "a@x.com,b",
        "a" * 250 + "@x.com",
    ],
)
def test_an_address_that_cannot_be_one_is_invalid(raw):
    with pytest.raises(InvalidValue):
        parse(FIELDS[("delegate", "address")], raw, TODAY)


def test_only_the_delegate_address_is_owner_only():
    assert [key for key, f in FIELDS.items() if f.owner_only] == [("delegate", "address")]


def test_a_delegate_target_has_one_part():
    assert parse_target("delegate|a@x.com").parts == ("a@x.com",)
    with pytest.raises(UnknownTarget):
        parse_target("delegate")


def test_an_address_of_the_longest_length_is_kept():
    address = "a" * 248 + "@x.com"
    assert parse(FIELDS[("delegate", "address")], address, TODAY) == address
