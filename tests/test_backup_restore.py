"""The backup and restore behind scripts/backup-restore.sh: a backup of the mocked tables and
bucket, a restore that brings the stack back to exactly the backup, and the refusals that keep a
backup from overwriting files or a restore from running unconfirmed or from a partial folder."""

import json
import sys
from pathlib import Path

import boto3
import pytest
from conftest import FILES_BUCKET, REGION
from engine_kit import ACCOUNT, USER, Kit, electricity

from common.store import TABLE_NAMES

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import backup_restore
from backup_restore import Stack, backup, restore

STACK = "meters-tracker-dev"


def _stack(ddb):
    return Stack(
        STACK,
        {name: name for name in TABLE_NAMES},
        FILES_BUCKET,
        ddb,
        boto3.client("s3", region_name=REGION),
    )


def _seed():
    """The owner's account with an apartment, a meter, a reading and two photos."""
    kit = Kit()
    kit.store.access_put_if_absent(ACCOUNT, ACCOUNT, "owner")
    kit.engine.load(USER)
    apartment = kit.apartment()
    kit.meter(apartment, "E-1", "electricity", [])
    kit.store.put_reading(ACCOUNT, "E-1", {"date": "2026-09-01", "seq": "s", "value": 1})
    kit.send_photo(electricity())
    kit.send_photo(electricity(serial="E-77"))
    return kit


def _snapshot(ddb, s3):
    rows = {name: ddb.Table(name).scan(ConsistentRead=True)["Items"] for name in TABLE_NAMES}
    files = {}
    for entry in s3.list_objects_v2(Bucket=FILES_BUCKET).get("Contents", []):
        stored = s3.get_object(Bucket=FILES_BUCKET, Key=entry["Key"])
        files[entry["Key"]] = (stored["Body"].read(), stored["ContentType"])
    return rows, files


def _confirm(answer):
    return lambda prompt: answer


def test_backup_writes_every_row_and_file_with_metadata(env, ddb, tmp_path):
    kit = _seed()
    metadata = backup(_stack(ddb), tmp_path / "b")
    assert metadata["stack"] == STACK
    assert metadata["rows"]["readings"] == 1 and metadata["rows"]["access"] == 1
    assert metadata["files"] == 2
    manifest = json.loads((tmp_path / "b" / "files.json").read_text())
    assert sorted(manifest) == sorted(kit.photo_keys())
    assert json.loads((tmp_path / "b" / "metadata.json").read_text()) == metadata


def test_backup_refuses_a_folder_that_holds_files(env, ddb, tmp_path):
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "other").write_text("x")
    with pytest.raises(SystemExit, match="already holds files"):
        backup(_stack(ddb), tmp_path / "b")


def test_restore_brings_back_exactly_the_backup(env, ddb, tmp_path):
    _seed()
    stack = _stack(ddb)
    before = _snapshot(ddb, stack.s3)
    backup(stack, tmp_path / "b")
    ddb.Table("readings").delete_item(Key={"pk": f"{ACCOUNT}#E-1", "sk": "s"})
    ddb.Table("meters").put_item(Item={"pk": ACCOUNT, "sk": "X-9", "type": "water"})
    stack.s3.put_object(Bucket=FILES_BUCKET, Key=f"{ACCOUNT}/images/new.jpg", Body=b"n")
    for key in before[1]:
        stack.s3.put_object(Bucket=FILES_BUCKET, Key=key, Body=b"changed", ContentType="text/x")
    assert restore(stack, tmp_path / "b", _confirm(STACK))
    assert _snapshot(ddb, stack.s3) == before


def test_restore_without_the_stack_name_changes_nothing(env, ddb, tmp_path):
    _seed()
    stack = _stack(ddb)
    backup(stack, tmp_path / "b")
    ddb.Table("meters").put_item(Item={"pk": ACCOUNT, "sk": "X-9", "type": "water"})
    after_change = _snapshot(ddb, stack.s3)
    assert not restore(stack, tmp_path / "b", _confirm("yes"))
    assert _snapshot(ddb, stack.s3) == after_change


def test_restore_refuses_an_incomplete_backup(env, ddb, tmp_path):
    _seed()
    stack = _stack(ddb)
    backup(stack, tmp_path / "b")
    (tmp_path / "b" / "tables" / "readings.jsonl.gz").unlink()
    with pytest.raises(SystemExit, match="readings.jsonl.gz"):
        restore(stack, tmp_path / "b", _confirm(STACK))


def test_main_cancelled_restore_exits_one(env, ddb, tmp_path, monkeypatch, capsys):
    _seed()
    backup(_stack(ddb), tmp_path / "b")
    monkeypatch.setattr(backup_restore, "resolve", lambda name: _stack(ddb))
    monkeypatch.setattr("builtins.input", _confirm("no"))
    monkeypatch.setattr(sys, "argv", ["backup_restore.py", "restore", STACK, str(tmp_path / "b")])
    with pytest.raises(SystemExit) as stopped:
        backup_restore.main()
    assert stopped.value.code == 1
    assert "cancelled" in capsys.readouterr().err
