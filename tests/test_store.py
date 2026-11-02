"""The DynamoDB store: conditional access rows, sorted reads, pending-item claims, the last
messages page, strongly consistent reads, the cross-account scan for failed photos, and
plain Python number types on the way out."""

import pytest
from botocore.exceptions import ClientError

from common.access import DELEGATE, OWNER
from common.account import Writes
from common.store import AccessChanged


def test_access_put_if_absent_is_conditional(store):
    assert store.access_put_if_absent("a@x.com", "acc1", "owner") is True
    assert store.access_put_if_absent("a@x.com", "acc2", "delegate") is False
    assert store.access_get("a@x.com") == {"email": "a@x.com", "account": "acc1", "role": "owner"}
    assert store.access_get("missing@x.com") is None


def test_property_and_apartment_roundtrip(store):
    prop = store.put_property("acc1", "Main street")
    apartment = store.put_apartment("acc1", prop["id"], "Flat 3", "2026-01-01", 2)
    assert store.properties("acc1") == [prop]
    assert store.apartments("acc1") == [apartment]
    assert apartment["property_id"] == prop["id"]
    assert store.properties("acc2") == []


def test_meter_put_get_update(store):
    meter = {
        "serial": "M1",
        "apartment_id": "ap1",
        "type": "water",
        "display_choice": 1,
        "label_hint": "kitchen",
        "retired_at": None,
    }
    store.put_meter("acc1", meter)
    assert store.meter("acc1", "M1") == meter
    assert store.meter("acc1", "nope") is None
    store.update_meter("acc1", "M1", retired_at="2026-05-01", label_hint=None)
    assert store.meters("acc1") == [{**meter, "retired_at": "2026-05-01", "label_hint": None}]


def test_periods_sorted_by_start_and_updated(store):
    for start in ("2026-03-01", "2026-01-01"):
        store.put_period(
            "acc1",
            "ap1",
            {"start": start, "months": 2, "closed_on": None, "last_reminded_on": None},
        )
    store.update_period("acc1", "ap1", "2026-01-01", closed_on="2026-03-02")
    periods = store.periods("acc1", "ap1")
    assert [p["start"] for p in periods] == ["2026-01-01", "2026-03-01"]
    assert periods[0]["closed_on"] == "2026-03-02"
    assert store.periods("acc1", "ap2") == []


def test_delete_period(store):
    for start in ("2026-01-01", "2026-03-01"):
        store.put_period(
            "acc1",
            "ap1",
            {"start": start, "months": 2, "closed_on": None, "last_reminded_on": None},
        )
    store.delete_period("acc1", "ap1", "2026-01-01")
    assert [p["start"] for p in store.periods("acc1", "ap1")] == ["2026-03-01"]


SEQ = "2026-01-05T09:00:00"


def _reading(date, seq, value=10):
    return {
        "date": date,
        "seq": seq,
        "value": value,
        "image_key": "k",
        "recorded_by": "u",
        "opens_period": None,
        "closes_period": None,
        "resets": False,
    }


def test_readings_sorted_by_date_then_seq(store):
    for date, seq in (
        ("2026-02-01", "2026-02-01T08:00:00"),
        ("2026-01-05", "2026-01-05T10:00:00"),
        ("2026-01-05", "2026-01-05T09:00:00"),
    ):
        store.put_reading("acc1", "M1", _reading(date, seq))
    assert [(r["date"], r["seq"]) for r in store.readings("acc1", "M1")] == [
        ("2026-01-05", "2026-01-05T09:00:00"),
        ("2026-01-05", "2026-01-05T10:00:00"),
        ("2026-02-01", "2026-02-01T08:00:00"),
    ]


def test_update_reading_sets_closes_period(store):
    store.put_reading("acc1", "M1", _reading("2026-01-05", SEQ))
    store.update_reading("acc1", "M1", "2026-01-05", SEQ, closes_period="2026-01-01")
    assert store.readings("acc1", "M1")[0]["closes_period"] == "2026-01-01"


def test_update_reading_removes_named_attributes(store):
    store.put_reading("acc1", "M1", {**_reading("2026-01-05", SEQ), "model": "m"})
    store.update_reading("acc1", "M1", "2026-01-05", SEQ, remove=["model"], value=7)
    [saved] = store.readings("acc1", "M1")
    assert "model" not in saved and saved["value"] == 7


def test_update_missing_item_raises(store):
    with pytest.raises(ClientError):
        store.update_reading("acc1", "M1", "2026-01-05", SEQ, closes_period="2026-01-01")


def test_tariffs_sorted_by_effective_date(store):
    for date in ("2026-06-01", "2026-01-01"):
        store.put_tariff(
            "acc1", {"effective_date": date, "electricity_per_kwh": 0.6, "water_per_m3": 9.5}
        )
    assert [t["effective_date"] for t in store.tariffs("acc1")] == ["2026-01-01", "2026-06-01"]


def test_pending_roundtrip_and_delete(store):
    for seq in ("2026-01-02T10:00:00", "2026-01-02T09:00:00"):
        store.put_pending(
            "u1",
            {
                "seq": seq,
                "kind": "reading",
                "status": "reading",
                "step": "value",
                "data": {"serial": "M1", "n": [1, 2]},
            },
        )
    assert [p["seq"] for p in store.pending("u1")] == ["2026-01-02T09:00:00", "2026-01-02T10:00:00"]
    assert store.pending("u1")[0]["data"] == {"serial": "M1", "n": [1, 2]}
    store.delete_pending("u1", "2026-01-02T09:00:00")
    assert [p["seq"] for p in store.pending("u1")] == ["2026-01-02T10:00:00"]


def test_claim_pending_holds_only_at_the_given_version_and_status(store):
    item = {"seq": SEQ, "kind": "k", "status": "awaiting", "step": "s", "version": 3, "data": {}}
    store.put_pending("u1", item)
    assert store.claim_pending("u1", SEQ, 3, "awaiting") is True
    assert store.claim_pending("u1", SEQ, 3, "awaiting") is False
    assert store.claim_pending("u1", SEQ, 4, "parked") is False
    assert store.pending("u1") == [{**item, "version": 4}]
    assert store.claim_pending("u1", "missing", 0, "awaiting") is False
    assert store.pending("u1") == [{**item, "version": 4}]


def _message(at, text):
    return {
        "at": at,
        "role": "user",
        "text": text,
        "card": None,
        "image_key": None,
        "pdf_key": None,
    }


def _two_days(store):
    for i in range(5):
        store.append_message("u1", _message(f"2026-01-01T00:00:0{i}", f"t{i}"))
    for i in range(2):
        store.append_message("u1", _message(f"2026-01-02T00:00:0{i}", f"d2-{i}"))


def test_messages_returns_one_day_oldest_first(store):
    _two_days(store)
    assert [m["text"] for m in store.messages("u1", "2026-01-02")] == ["d2-0", "d2-1"]
    assert len(store.messages("u1", "2026-01-01")) == 5
    assert store.messages("u1", "2026-01-03") == []


def test_messages_of_a_day_keeps_the_latest_within_the_limit(store):
    _two_days(store)
    calls = []
    store._tables = {name: _ReadRecorder(t, calls) for name, t in store._tables.items()}
    assert [m["text"] for m in store.messages("u1", "2026-01-01", limit=2)] == ["t3", "t4"]
    [call] = calls
    assert (call["Limit"], call["ScanIndexForward"]) == (2, False)


def test_photo_messages_spans_days_and_holds_only_tagged_messages(store):
    for at in ("2026-02-01T08:00:00", "2026-02-02T08:00:00", "2026-02-03T08:00:00"):
        store.append_message("u3", {"at": at, "role": "user", "text": None, "image_key": at})
    tag = {"state": "saved", "meter_type": "water", "apartment": "a", "property": "p"}
    store.set_message_reading("u3", "2026-02-01T08:00:00", tag)
    store.set_message_reading("u3", "2026-02-03T08:00:00", tag)
    tagged = store.photo_messages("u3")
    assert [m["at"] for m in tagged] == ["2026-02-01T08:00:00", "2026-02-03T08:00:00"]
    assert tagged[0]["reading"] == tag


def test_has_messages(store):
    assert store.has_messages("u2") is False
    store.append_message("u2", _message("2026-01-01T00:00:00", "t"))
    assert store.has_messages("u2") is True


def test_set_message_reading_adds_the_field(store):
    store.append_message("u1", _message("2026-01-01T00:00:00", "t"))
    store.set_message_reading("u1", "2026-01-01T00:00:00", {"state": "saved"})
    assert store.messages("u1", "2026-01-01")[0]["reading"] == {"state": "saved"}


def test_decimals_do_not_leak(store):
    store.put_tariff(
        "acc1", {"effective_date": "2026-01-01", "electricity_per_kwh": 0.62, "water_per_m3": 9}
    )
    tariff = store.tariffs("acc1")[0]
    assert tariff["electricity_per_kwh"] == 0.62
    assert isinstance(tariff["electricity_per_kwh"], float)
    assert isinstance(tariff["water_per_m3"], int)


class _ReadRecorder:
    """Wraps a table, recording the keyword arguments of every query and get_item."""

    def __init__(self, table, calls):
        self._table = table
        self._calls = calls

    def query(self, **kwargs):
        self._calls.append(kwargs)
        return self._table.query(**kwargs)

    def get_item(self, **kwargs):
        self._calls.append(kwargs)
        return self._table.get_item(**kwargs)


def test_every_read_is_consistent(store):
    calls = []
    store._tables = {name: _ReadRecorder(t, calls) for name, t in store._tables.items()}
    store.access_get("a@x.com")
    store.properties("acc1")
    store.apartments("acc1")
    store.meter("acc1", "M1")
    store.meters("acc1")
    store.periods("acc1", "ap1")
    store.readings("acc1", "M1")
    store.tariffs("acc1")
    store.pending("u1")
    store.messages("u1", "2026")
    assert len(calls) == 10
    assert all(call["ConsistentRead"] is True for call in calls)


def test_failed_photo_readings_returns_only_rows_with_a_chain(store):
    store.put_reading(
        "acc1", "M1", {**_reading("2026-01-05", "s1"), "failed_photos": ["k1"], "model": "x:y"}
    )
    store.put_reading("acc1", "M1", _reading("2026-01-06", "s2"))
    store.put_reading(
        "acc2",
        "M9",
        {**_reading("2026-01-07", "s3"), "failed_photos": ["k2", "k3"], "model": "x:y"},
    )
    rows = store.failed_photo_readings()
    assert [(r["account"], r["serial"], r["failed_photos"]) for r in rows] == [
        ("acc1", "M1", ["k1"]),
        ("acc2", "M9", ["k2", "k3"]),
    ]


def _access_row(email, account="acc1", role=DELEGATE):
    return {"email": email, "account": account, "role": role}


def _moved_address(old="old@x.com", new="new@x.com"):
    return Writes(
        puts={"access": [(None, _access_row(new))]},
        deletes={"access": [(None, _access_row(old))]},
    )


def test_delegates_lists_only_the_delegate_rows_of_the_account(store):
    store.access_put_if_absent("acc1", "acc1", OWNER)
    store.access_put_if_absent("b@x.com", "acc1", DELEGATE)
    store.access_put_if_absent("a@x.com", "acc1", DELEGATE)
    store.access_put_if_absent("c@x.com", "acc2", DELEGATE)
    assert store.delegates("acc1") == [_access_row("a@x.com"), _access_row("b@x.com")]
    assert store.delegates("acc3") == []


def test_apply_moves_an_access_row_in_one_transaction(store):
    store.access_put_if_absent("old@x.com", "acc1", DELEGATE)
    store.apply("acc1", _moved_address())
    assert store.access_get("old@x.com") is None
    assert store.access_get("new@x.com") == _access_row("new@x.com")


def test_apply_replaces_the_owner_row_of_another_address(store):
    store.access_put_if_absent("old@x.com", "acc1", DELEGATE)
    store.access_put_if_absent("new@x.com", "new@x.com", OWNER)
    store.apply("acc1", _moved_address())
    assert store.access_get("new@x.com") == _access_row("new@x.com")


def test_apply_refuses_an_old_row_that_stopped_naming_the_account(store):
    store.access_put_if_absent("old@x.com", "acc2", DELEGATE)
    with pytest.raises(AccessChanged):
        store.apply("acc1", _moved_address())
    assert store.access_get("old@x.com") == _access_row("old@x.com", "acc2")
    assert store.access_get("new@x.com") is None


def test_apply_refuses_an_address_that_gained_a_delegate_row(store):
    store.access_put_if_absent("old@x.com", "acc1", DELEGATE)
    store.access_put_if_absent("new@x.com", "acc2", DELEGATE)
    with pytest.raises(AccessChanged):
        store.apply("acc1", _moved_address())
    assert store.access_get("old@x.com") == _access_row("old@x.com")
    assert store.access_get("new@x.com") == _access_row("new@x.com", "acc2")


def test_apply_writes_nothing_else_when_an_access_condition_fails(store):
    store.access_put_if_absent("new@x.com", "acc2", DELEGATE)
    prop = {"id": "p1", "name": "Main street"}
    writes = _moved_address()
    writes.puts["properties"] = [(None, prop)]
    with pytest.raises(AccessChanged):
        store.apply("acc1", writes)
    assert store.properties("acc1") == []


def test_apply_deletes_a_waiting_item_of_any_address(store):
    item = {"seq": "s1", "kind": "correction", "status": "awaiting", "step": "s", "version": 1}
    store.put_pending("old@x.com", item)
    store.apply("acc1", Writes(deletes={"pending": [("old@x.com", item)]}))
    assert store.pending("old@x.com") == []
