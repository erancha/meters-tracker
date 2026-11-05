"""The account reset behind scripts/reset-account.sh: the deletion plan over the mocked tables and
bucket, its printed counts, carrying it out, and the refusal without --yes."""

import sys
from pathlib import Path

import boto3
import pytest
from conftest import FILES_BUCKET, REGION
from engine_kit import ACCOUNT, USER, Kit, electricity

from common.store import TABLE_NAMES

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import reset_account
from reset_account import describe, execute, plan

OWNER = ACCOUNT
# Shares the owner's address as a prefix, so a match by bare prefix would sweep it up.
NEIGHBOUR = "owner@x.com.au"


def _tables(ddb):
    return {name: ddb.Table(name) for name in TABLE_NAMES}


def _s3():
    return boto3.client("s3", region_name=REGION)


def _neighbour(store, s3):
    store.access_put_if_absent(NEIGHBOUR, NEIGHBOUR, "owner")
    store.put_meter(NEIGHBOUR, {"serial": "N-1", "apartment_id": "a", "type": "water"})
    store.put_period(NEIGHBOUR, "a", {"start": "2026-09-01", "months": 2, "closed_on": None})
    s3.put_object(Bucket=FILES_BUCKET, Key=f"{NEIGHBOUR}/images/1.jpg", Body=b"n")


def _seed():
    """The owner's account after the welcome and two photos of unknown meters, each waiting on
    its property question, beside a neighbour account."""
    kit = Kit()
    kit.store.access_put_if_absent(OWNER, OWNER, "owner")
    kit.engine.load(USER)
    kit.send_photo(electricity())
    kit.send_photo(electricity(label="E-77"))
    _neighbour(kit.store, _s3())
    return kit


def test_plan_holds_only_the_accounts_own_rows_and_files(env, ddb):
    kit = _seed()
    planned = plan(_tables(ddb), _s3(), FILES_BUCKET, OWNER)
    assert {name: len(keys) for name, keys in planned.rows.items()} == {
        "access": 1,
        "properties": 0,
        "apartments": 0,
        "meters": 0,
        "periods": 0,
        "readings": 0,
        "tariffs": 0,
        "pending": 2,
        "messages": len(kit.store.messages(OWNER, "2026")),
    }
    assert all(k["pk"] == OWNER for keys in planned.rows.values() for k in keys)
    assert sorted(f["Key"] for f in planned.files) == sorted(kit.photo_keys())


def test_plan_takes_rows_keyed_by_the_account_and_a_hash(env, ddb):
    kit = Kit()
    apartment = kit.apartment()
    kit.meter(apartment, "E-1", "electricity", [])
    kit.store.put_reading(OWNER, "E-1", {"date": "2026-09-01", "seq": "s", "value": 1})
    planned = plan(_tables(ddb), _s3(), FILES_BUCKET, OWNER)
    assert [k["pk"] for k in planned.rows["periods"]] == [f"{OWNER}#{apartment['id']}"]
    assert [k["pk"] for k in planned.rows["readings"]] == [f"{OWNER}#E-1"]
    assert len(planned.rows["properties"]) == len(planned.rows["apartments"]) == 1


def test_describe_counts_rows_per_table_and_the_files(env, ddb):
    _seed()
    text = describe(plan(_tables(ddb), _s3(), FILES_BUCKET, OWNER), OWNER)
    assert "access: 1" in text and "pending: 2" in text and "readings: 0" in text
    assert f"files under {OWNER}/: 2" in text


def test_execute_deletes_the_plan_and_leaves_other_accounts(env, ddb):
    kit = _seed()
    s3 = _s3()
    execute(plan(_tables(ddb), s3, FILES_BUCKET, OWNER), _tables(ddb), s3, FILES_BUCKET)
    after = plan(_tables(ddb), s3, FILES_BUCKET, OWNER)
    assert all(keys == [] for keys in after.rows.values())
    assert after.files == []
    assert kit.store.access_get(NEIGHBOUR) is not None
    assert len(kit.store.meters(NEIGHBOUR)) == 1
    assert len(kit.store.periods(NEIGHBOUR, "a")) == 1
    listed = s3.list_objects_v2(Bucket=FILES_BUCKET)["Contents"]
    assert [o["Key"] for o in listed] == [f"{NEIGHBOUR}/images/1.jpg"]


def test_execute_removes_every_version_of_a_versioned_photo(env, ddb):
    s3 = _s3()
    s3.put_bucket_versioning(Bucket=FILES_BUCKET, VersioningConfiguration={"Status": "Enabled"})
    key = f"{OWNER}/images/1.jpg"
    s3.put_object(Bucket=FILES_BUCKET, Key=key, Body=b"1")
    s3.put_object(Bucket=FILES_BUCKET, Key=key, Body=b"2")
    s3.delete_object(Bucket=FILES_BUCKET, Key=key)
    planned = plan(_tables(ddb), s3, FILES_BUCKET, OWNER)
    assert len(planned.files) == 3
    execute(planned, _tables(ddb), s3, FILES_BUCKET)
    listed = s3.list_object_versions(Bucket=FILES_BUCKET, Prefix=f"{OWNER}/")
    assert "Versions" not in listed and "DeleteMarkers" not in listed


def _run_main(monkeypatch, ddb, *args):
    monkeypatch.setattr(reset_account, "resources", lambda stack: (_tables(ddb), FILES_BUCKET))
    monkeypatch.setattr(sys, "argv", ["reset_account.py", "meters-tracker-dev", *args])
    reset_account.main()


def test_main_without_yes_prints_the_plan_and_deletes_nothing(env, ddb, monkeypatch, capsys):
    kit = _seed()
    with pytest.raises(SystemExit) as stopped:
        _run_main(monkeypatch, ddb, OWNER)
    assert stopped.value.code == 1
    out = capsys.readouterr()
    assert "pending: 2" in out.out
    assert "--yes" in out.err
    assert len(kit.store.pending(OWNER)) == 2


def test_main_with_yes_deletes_the_account(env, ddb, monkeypatch, capsys):
    kit = _seed()
    _run_main(monkeypatch, ddb, OWNER, "--yes")
    assert kit.store.pending(OWNER) == [] and kit.store.messages(OWNER, "2026") == []
    assert "Deleted" in capsys.readouterr().out


def test_main_takes_the_address_in_lowercase_as_accounts_are_keyed(env, ddb, monkeypatch):
    kit = _seed()
    _run_main(monkeypatch, ddb, OWNER.upper(), "--yes")
    assert kit.store.access_get(OWNER) is None
