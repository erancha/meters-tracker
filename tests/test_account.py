"""The in-memory account: loading every record of an account, deep copies, the writes that turn
one copy into another, their application in a single all-or-nothing transaction, and the records
block the classifier is given."""

import pytest
from botocore.exceptions import ClientError

from common import prompts, texts
from common.access import DELEGATE, OWNER
from common.account import (
    Account,
    Writes,
    diff,
    listed_context,
    load,
    lookup,
    move_period,
    realign,
    record_lines,
    records,
)
from common.model import RecordsQuery
from common.store import MAX_TRANSACTION_ITEMS, TooLarge

ACCOUNT = "acc1"
READING_FIELDS = {
    "image_key": "k",
    "recorded_by": "u",
    "opens_period": None,
    "closes_period": None,
    "resets": False,
}


def _meter(serial, apartment_id="ap1"):
    return {
        "serial": serial,
        "apartment_id": apartment_id,
        "type": "water",
        "display_choice": 1,
        "label_hint": None,
        "retired_at": None,
    }


def _period(start):
    return {"start": start, "months": 2, "closed_on": None, "last_reminded_on": None}


def _reading(day, seq, value):
    return {"date": day, "seq": seq, "value": value, **READING_FIELDS}


def _seed(store):
    prop = store.put_property(ACCOUNT, "Main street")
    apartment = store.put_apartment(ACCOUNT, prop["id"], "Flat 3", "2026-01-01", 2)
    store.put_meter(ACCOUNT, _meter("M1", apartment["id"]))
    store.put_period(ACCOUNT, apartment["id"], _period("2026-01-01"))
    store.put_reading(ACCOUNT, "M1", _reading("2026-01-05", "s1", 10.5))
    store.put_reading(ACCOUNT, "M1", _reading("2026-02-05", "s2", 20))
    store.put_tariff(
        ACCOUNT, {"effective_date": "2026-01-01", "electricity_per_kwh": 0.6, "water_per_m3": 9.5}
    )
    return apartment


def test_load_reads_every_apartment_and_meter(store):
    apartment = _seed(store)
    loaded = load(store, ACCOUNT, OWNER)
    assert [a["id"] for a in loaded.apartments] == [apartment["id"]]
    assert [m["serial"] for m in loaded.meters] == ["M1"]
    assert [p["start"] for p in loaded.periods[apartment["id"]]] == ["2026-01-01"]
    assert [r["seq"] for r in loaded.readings["M1"]] == ["s1", "s2"]
    assert len(loaded.properties) == 1 and len(loaded.tariffs) == 1


def test_copy_is_deep(store):
    _seed(store)
    original = load(store, ACCOUNT, OWNER)
    copy = original.copy()
    copy.readings["M1"][0]["value"] = 99
    copy.meters.append(_meter("M2"))
    assert original.readings["M1"][0]["value"] == 10.5
    assert len(original.meters) == 1


def test_diff_of_an_unchanged_copy_is_empty(store):
    _seed(store)
    loaded = load(store, ACCOUNT, OWNER)
    assert diff(loaded, loaded.copy()) == Writes()


def test_diff_of_a_changed_field_puts_only_that_row(store):
    _seed(store)
    before = load(store, ACCOUNT, OWNER)
    after = before.copy()
    after.readings["M1"][1]["value"] = 25
    writes = diff(before, after)
    assert writes.deletes == {}
    assert writes.puts == {"readings": [("M1", after.readings["M1"][1])]}


def test_diff_of_a_serial_rename_deletes_and_puts_every_row(store):
    _seed(store)
    before = load(store, ACCOUNT, OWNER)
    after = before.copy()
    after.meters[0]["serial"] = "M9"
    after.readings["M9"] = after.readings.pop("M1")
    writes = diff(before, after)
    assert writes.deletes["meters"] == [(None, before.meters[0])]
    assert writes.puts["meters"] == [(None, after.meters[0])]
    assert writes.deletes["readings"] == [("M1", r) for r in before.readings["M1"]]
    assert writes.puts["readings"] == [("M9", r) for r in after.readings["M9"]]
    store.apply(ACCOUNT, writes)
    renamed = load(store, ACCOUNT, OWNER)
    assert [m["serial"] for m in renamed.meters] == ["M9"]
    assert renamed.readings == {"M9": after.readings["M9"]}
    assert store.readings(ACCOUNT, "M1") == []


def test_apply_writes_every_table_in_one_call(store):
    apartment = _seed(store)
    before = load(store, ACCOUNT, OWNER)
    after = before.copy()
    after.periods[apartment["id"]] = [_period("2026-03-01")]
    after.tariffs[0]["water_per_m3"] = 10.25
    store.apply(ACCOUNT, diff(before, after))
    changed = load(store, ACCOUNT, OWNER)
    assert [p["start"] for p in changed.periods[apartment["id"]]] == ["2026-03-01"]
    assert changed.tariffs[0]["water_per_m3"] == 10.25
    assert changed.readings == before.readings


def test_apply_of_empty_writes_makes_no_call(store, monkeypatch):
    def refuse(**_):
        raise AssertionError("transact_write_items called")

    monkeypatch.setattr(store._client, "transact_write_items", refuse)
    store.apply(ACCOUNT, Writes())


def test_apply_writes_all_or_nothing(store):
    _seed(store)
    before = load(store, ACCOUNT, OWNER)
    reading = before.readings["M1"][0]
    changed = {**reading, "value": 1}
    writes = Writes(
        puts={"readings": [("M1", changed)], "tariffs": [(None, {**before.tariffs[0], "x": 1})]},
        deletes={"readings": [("M1", reading)]},
    )
    with pytest.raises(ClientError):
        store.apply(ACCOUNT, writes)
    assert load(store, ACCOUNT, OWNER) == before


def test_apply_refuses_over_one_hundred_items_before_any_write(store):
    puts = [("M1", _reading("2026-03-01", f"n{i}", i)) for i in range(MAX_TRANSACTION_ITEMS + 1)]
    with pytest.raises(TooLarge, match="101"):
        store.apply(ACCOUNT, Writes(puts={"readings": puts}))
    assert store.readings(ACCOUNT, "M1") == []


def test_diff_raises_on_two_records_with_one_key(store):
    _seed(store)
    before = load(store, ACCOUNT, OWNER)
    after = before.copy()
    after.readings["M1"].append(dict(after.readings["M1"][0]))
    with pytest.raises(ValueError, match="readings.*M1.*2026-01-05#s1"):
        diff(before, after)


def _block_account(readings) -> Account:
    return Account(
        delegates=[],
        properties=[{"id": "p1", "name": "Main street"}],
        apartments=[{"id": "a1", "property_id": "p1", "name": "Flat 3", "period_months": 2}],
        meters=[
            {**_meter("M1", "a1"), "type": "electricity", "display_choice": 1},
            {**_meter("M2", "a1"), "display_choice": None, "retired_at": "2026-02-01"},
        ],
        readings={"M1": readings, "M2": []},
        periods={
            "a1": [{**_period("2026-01-01"), "closed_on": "2026-03-01"}, _period("2026-03-02")]
        },
        tariffs=[
            {
                "effective_date": "2026-01-01",
                "electricity_per_kwh": 0.6,
                "water_per_m3": None,
                "source_image_key": None,
            }
        ],
    )


def test_records_are_keyed_by_the_id_each_target_names():
    readings = [_reading("2026-01-05", "s1", 10)]
    account = _block_account(readings)
    assert set(records(account, "property")) == {"p1"}
    assert set(records(account, "apartment")) == {"a1"}
    assert set(records(account, "meter")) == {"M1", "M2"}
    assert records(account, "reading") == {"M1|2026-01-05#s1": readings[0]}
    assert set(records(account, "period")) == {"a1|2026-01-01", "a1|2026-03-02"}
    assert set(records(account, "tariff")) == {"2026-01-01"}


def test_record_lines_list_every_record_with_its_id():
    closing = {**_reading("2026-03-01", "s2", 20), "closes_period": "2026-01-01"}
    opening = {**_reading("2026-01-05", "s1", 10), "opens_period": "2026-01-01", "resets": True}
    empty = texts.EMPTY_VALUE
    assert record_lines(_block_account([opening, closing]), 12) == [
        "property|p1: name=Main street",
        "apartment|a1: name=Flat 3; property=Main street; months=2",
        f"meter|M1: type=electricity; apartment=Flat 3; display=bottom; retired_at={empty}",
        (
            "reading|M1|2026-01-05#s1: value=10; date=2026-01-05; resets=true; "
            f"opens_period=2026-01-01; closes_period={empty}"
        ),
        (
            "reading|M1|2026-03-01#s2: value=20; date=2026-03-01; resets=false; "
            f"opens_period={empty}; closes_period=2026-01-01; latest=true"
        ),
        f"meter|M2: type=water; apartment=Flat 3; display={empty}; retired_at=2026-02-01",
        "period|a1|2026-01-01: apartment=Flat 3; months=2; closed_on=2026-03-01",
        f"period|a1|2026-03-02: apartment=Flat 3; months=2; closed_on={empty}",
        f"tariff|2026-01-01: electricity_price=0.6; water_price={empty}",
    ]


def test_record_lines_keep_each_meters_latest_readings():
    readings = [_reading(f"2026-01-0{day}", f"s{day}", day) for day in range(1, 6)]
    block = "\n".join(record_lines(_block_account(readings), 2))
    assert "reading|M1|2026-01-03#s3" not in block
    assert "reading|M1|2026-01-04#s4" in block and "reading|M1|2026-01-05#s5" in block


def test_record_lines_keep_the_latest_saved_reading_even_when_it_is_back_dated():
    readings = [
        _reading("2026-01-01", "s9", 1),
        _reading("2026-01-02", "s2", 2),
        _reading("2026-01-03", "s3", 3),
    ]
    block = "\n".join(record_lines(_block_account(readings), 1))
    assert "reading|M1|2026-01-01#s9" in block
    assert "reading|M1|2026-01-02#s2" not in block
    assert "reading|M1|2026-01-03#s3" in block
    assert block.count("latest=true") == 1


def test_listed_context_lists_waiting_cards_and_dropped_photos():
    empty = texts.EMPTY_VALUE
    cards = {"2026-01-05T08:00:00_ab12": {"value": 10, "serial": None}}
    photos = {
        "2026-01-06T09:00:00_cd34": {
            "state": "dropped",
            "meter_type": "unknown",
            "apartment": None,
            "property": None,
        },
        "2026-01-07T09:00:00_ef56": {
            "state": "dropped",
            "meter_type": None,
            "apartment": None,
            "property": None,
        },
    }
    lines = listed_context(cards, photos).split("\n")
    assert lines == [
        f"{texts.RECORDS_TITLE}:",
        f"card|2026-01-05T08:00:00_ab12: value=10; serial={empty}",
        (
            "photo|2026-01-06T09:00:00_cd34: kind=meter; meter_type=unknown; "
            f"apartment={empty}; property={empty}"
        ),
        (
            f"photo|2026-01-07T09:00:00_ef56: kind=statement; meter_type={empty}; "
            f"apartment={empty}; property={empty}"
        ),
    ]


def test_an_empty_account_has_no_lines():
    assert record_lines(Account([], [], [], {}, {}, [], []), 12) == []
    assert listed_context({}, {}) == ""


def _closed_and_next() -> Account:
    """Two meters of one apartment whose closing readings also open the next period."""
    shared = {**READING_FIELDS, "opens_period": "2026-07-01", "closes_period": "2026-05-01"}
    meter_a = [{"date": "2026-06-20", "seq": "1", "value": 5, **shared}]
    meter_b = [{"date": "2026-06-30", "seq": "2", "value": 9, **shared}]
    periods = [
        {"start": "2026-05-01", "months": 2, "closed_on": "2026-06-30", "last_reminded_on": None},
        {"start": "2026-07-01", "months": 2, "closed_on": None, "last_reminded_on": None},
    ]
    return Account(
        delegates=[],
        properties=[],
        apartments=[],
        meters=[_meter("A"), _meter("B")],
        readings={"A": meter_a, "B": meter_b},
        periods={"ap1": periods},
        tariffs=[],
    )


def test_realign_sets_closed_on_and_moves_the_next_period_with_its_flags():
    state = _closed_and_next()
    state.readings["B"][0]["date"] = "2026-06-25"
    realign(state, "ap1", "2026-05-01", "2026-07-01")
    closed, following = state.periods["ap1"]
    assert (closed["closed_on"], following["start"]) == ("2026-06-25", "2026-06-26")
    assert [state.readings[s][0]["opens_period"] for s in "AB"] == ["2026-06-26"] * 2


def test_realign_with_no_next_period_sets_only_closed_on():
    state = _closed_and_next()
    state.readings["B"][0]["date"] = "2026-06-25"
    realign(state, "ap1", "2026-05-01", None)
    assert [p["start"] for p in state.periods["ap1"]] == ["2026-05-01", "2026-07-01"]
    assert state.periods["ap1"][0]["closed_on"] == "2026-06-25"


def test_realign_leaves_a_next_period_already_starting_the_day_after():
    state = _closed_and_next()
    realign(state, "ap1", "2026-05-01", "2026-07-01")
    assert state == _closed_and_next()


def test_move_period_points_both_flags_at_the_new_start_and_keeps_the_order():
    state = _closed_and_next()
    state.periods["ap1"][1]["start"] = "2026-04-01"
    state.periods["ap1"].sort(key=lambda p: p["start"])
    closed = state.periods["ap1"][1]
    move_period(state, "ap1", closed, "2026-03-01")
    assert state.periods["ap1"][0] is closed
    assert [state.readings[s][0]["closes_period"] for s in "AB"] == ["2026-03-01"] * 2


def test_load_holds_the_account_delegates(store):
    store.access_put_if_absent("helper@x.com", ACCOUNT, DELEGATE)
    assert load(store, ACCOUNT, OWNER).delegates == [
        {"email": "helper@x.com", "account": ACCOUNT, "role": DELEGATE}
    ]


def test_a_changed_address_is_one_delete_and_one_put(store):
    store.access_put_if_absent("old@x.com", ACCOUNT, DELEGATE)
    before = load(store, ACCOUNT, OWNER)
    after = before.copy()
    after.delegates[0]["email"] = "new@x.com"
    writes = diff(before, after)
    assert writes.size() == 2
    assert writes.deletes == {"access": [(None, before.delegates[0])]}
    assert writes.puts == {"access": [(None, after.delegates[0])]}


def test_a_delegate_is_a_record_kind_the_classifier_reads(store):
    store.access_put_if_absent("helper@x.com", ACCOUNT, DELEGATE)
    state = load(store, ACCOUNT, OWNER)
    assert records(state, "delegate") == {"helper@x.com": state.delegates[0]}
    assert "delegate|helper@x.com: address=helper@x.com" in record_lines(state, 12)


def test_a_delegate_loads_no_delegates(store):
    store.access_put_if_absent("helper@x.com", ACCOUNT, DELEGATE)
    assert load(store, ACCOUNT, DELEGATE).delegates == []


def _lookup_account(apartments=None) -> Account:
    """Two properties; the first holds כניסה מזרח, with a water and an electricity meter of 15
    readings each, and חדר 2; the second holds דירה 7. One tariff and one delegate."""
    apartments = apartments or [
        {"id": "a1", "property_id": "p1", "name": "כניסה מזרח", "period_months": 2},
        {"id": "a2", "property_id": "p1", "name": "חדר 2", "period_months": 2},
        {"id": "a3", "property_id": "p2", "name": "דירה 7", "period_months": 2},
    ]
    series = {
        serial: [_reading(f"2026-01-{day:02d}", f"{serial}{day:02d}", day) for day in range(1, 16)]
        for serial in ("W1", "E1")
    }
    return Account(
        properties=[{"id": "p1", "name": 'אונר 123 ראשל"צ'}, {"id": "p2", "name": "הרצל 5"}],
        apartments=apartments,
        meters=[_meter("W1", "a1"), {**_meter("E1", "a1"), "type": "electricity"}],
        readings=series,
        periods={"a1": [_period("2026-01-01")], "a2": [_period("2026-01-01")]},
        tariffs=[
            {
                "effective_date": "2026-01-01",
                "electricity_per_kwh": 0.6,
                "water_per_m3": 9.5,
                "source_image_key": None,
            }
        ],
        delegates=[{"email": "helper@x.com"}],
    )


def _query(**fields) -> RecordsQuery:
    return RecordsQuery(
        **{
            "apartment": None,
            "meter_type": None,
            "reading_date": None,
            "tariffs": False,
            "delegates": False,
            **fields,
        }
    )


def _ids(block: str) -> list[str]:
    return [line.partition(": ")[0] for line in block.splitlines()]


def test_lookup_by_apartment_lists_its_property_meters_readings_and_periods():
    ids = _ids(lookup(_lookup_account(), _query(apartment="כניסה מזרח"), 12))
    assert ids[:3] == ["property|p1", "apartment|a1", "meter|W1"]
    assert "apartment|a2" not in ids and "apartment|a3" not in ids
    assert "meter|E1" in ids and "period|a1|2026-01-01" in ids
    for serial in ("W1", "E1"):
        readings = [i for i in ids if i.startswith(f"reading|{serial}|")]
        assert len(readings) == 12 and readings[-1] == f"reading|{serial}|2026-01-15#{serial}15"
    assert not any(i.startswith(("tariff|", "delegate|")) for i in ids)


def test_lookup_matches_the_apartment_as_written():
    state = _lookup_account()
    plain = lookup(state, _query(apartment="כניסה מזרח"), 12)
    assert lookup(state, _query(apartment='ראשל"צ  כניסה   מזרח'), 12) == plain
    assert lookup(state, _query(apartment="ראשלצ כניסה"), 12) == plain


def test_lookup_by_meter_type_leaves_out_the_other_meter():
    ids = _ids(lookup(_lookup_account(), _query(apartment="כניסה", meter_type="water"), 12))
    assert "meter|W1" in ids and "meter|E1" not in ids
    assert not any(i.startswith("reading|E1|") for i in ids)


def test_lookup_adds_the_readings_of_a_named_date_beyond_the_latest():
    query = _query(apartment="כניסה מזרח", meter_type="water", reading_date="2026-01-02")
    ids = _ids(lookup(_lookup_account(), query, 12))
    assert "reading|W1|2026-01-02#W102" in ids and "reading|W1|2026-01-01#W101" not in ids


def test_lookup_with_no_apartment_lists_every_apartment_and_meter_without_readings():
    ids = _ids(lookup(_lookup_account(), _query(), 12))
    assert ids == [
        "property|p1",
        "property|p2",
        "apartment|a1",
        "apartment|a2",
        "apartment|a3",
        "meter|W1",
        "meter|E1",
        "reading|W1|2026-01-15#W115",
    ]


def test_lookup_matching_nothing_names_every_apartment():
    names = 'אונר 123 ראשל"צ / כניסה מזרח, אונר 123 ראשל"צ / חדר 2, הרצל 5 / דירה 7'
    expected = prompts.NO_APARTMENT_MATCH.format(query="חדר 9", names=names)
    block = lookup(_lookup_account(), _query(apartment="חדר 9"), 12)
    assert block.splitlines()[0] == expected
    assert _ids(block)[1:] == ["reading|W1|2026-01-15#W115"]


def test_lookup_lists_tariffs_and_delegates_only_when_asked():
    ids = _ids(lookup(_lookup_account(), _query(apartment="דירה 7", tariffs=True), 12))
    assert "tariff|2026-01-01" in ids and "delegate|helper@x.com" not in ids
    ids = _ids(lookup(_lookup_account(), _query(apartment="דירה 7", delegates=True), 12))
    assert "delegate|helper@x.com" in ids and "tariff|2026-01-01" not in ids


def test_a_lookup_on_a_hundred_apartments_holds_only_the_named_one():
    many = [
        {"id": f"a{n}", "property_id": "p1", "name": f"דירה {n}", "period_months": 2}
        for n in range(1, 101)
    ]
    ids = _ids(lookup(_lookup_account(many), _query(apartment="דירה 42"), 12))
    assert [i for i in ids if i.startswith("apartment|")] == ["apartment|a42"]


def _named(name, property_name="לאונר 18", empty_property=False) -> Account:
    properties = [{"id": "p1", "name": property_name}]
    if empty_property:
        properties.append({"id": "p2", "name": "בית ריק"})
    apartment = {"id": "a1", "property_id": "p1", "name": name, "period_months": 2}
    return Account(properties, [apartment], [_meter("M1", "a1")], {"M1": []}, {}, [], [])


@pytest.mark.parametrize(
    "written", ["דירה 3/1", "3/1", "לאונר 18 / דירה 3/1", "לאונר-18 דירה 3/1,"]
)
def test_lookup_matches_a_name_with_a_slash_or_hyphen(written):
    ids = _ids(lookup(_named("דירה 3/1"), _query(apartment=written), 12))
    assert "apartment|a1" in ids


def test_lookup_matches_a_name_copied_from_the_no_match_answer():
    ids = _ids(lookup(_named("דירה קדמית"), _query(apartment="לאונר 18 / דירה קדמית"), 12))
    assert "apartment|a1" in ids


def test_every_lookup_carries_the_reading_saved_last():
    block = lookup(_lookup_account(), _query(apartment="דירה 7"), 12)
    assert "reading|W1|2026-01-15#W115" in block and block.count("latest=true") == 1
    assert "latest=true" in lookup(_lookup_account(), _query(), 12)


def test_a_property_with_no_apartments_is_found_and_listed():
    state = _named("דירה קדמית", empty_property=True)
    assert "property|p2" in _ids(lookup(state, _query(apartment="בית ריק"), 12))
    assert "property|p2" in _ids(lookup(state, _query(), 12))


def test_a_blank_apartment_lists_as_no_apartment_does():
    state = _lookup_account()
    assert lookup(state, _query(apartment="  "), 12) == lookup(state, _query(), 12)
