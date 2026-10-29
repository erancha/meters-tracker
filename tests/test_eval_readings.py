"""Scores the offline evaluation of failed photos: one photo's result against the reading it led
to, a run over the mocked store and bucket with a fake model, its limit, and the printed lines
and summary."""

import hashlib
import sys
from pathlib import Path

import boto3
import pytest
from conftest import FILES_BUCKET, REGION
from engine_kit import water
from fake_model import FakeModel

from common.files import Files
from common.model import ModelRefused
from common.store import Store

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from eval_readings import evaluate, main, photo_line, report, score


def _reading(date, value, **failure):
    return {
        "date": date,
        "seq": f"{date}T08:00:00",
        "value": value,
        "image_key": "acc/images/2.jpg",
        "recorded_by": "acc",
        "opens_period": None,
        "closes_period": None,
        "resets": False,
        **failure,
    }


def test_score_marks_serial_value_and_unreadable():
    result = water(1250)
    assert score("W-1250", 1250, result) == {
        "serial_ok": True,
        "value_ok": True,
        "unreadable": False,
    }
    assert score("W-1250", 1260, result)["value_ok"] is False
    assert score("W-9", 1250, result)["serial_ok"] is False
    assert score("W-1250", 1250, water(None, unreadable=True))["unreadable"] is True


def test_evaluate_runs_every_failed_photo_through_the_model(env, ddb):
    store = Store.from_env()
    store.put_reading(
        "acc",
        "W-1250",
        _reading("2026-10-01", 1250, failed_photos=["acc/images/1.jpg"], model="x:old"),
    )
    store.put_reading("acc", "W-1250", _reading("2026-10-02", 1300))
    image = b"failed-photo"
    boto3.client("s3", region_name=REGION).put_object(
        Bucket=FILES_BUCKET, Key="acc/images/1.jpg", Body=image, ContentType="image/jpeg"
    )
    model = FakeModel({hashlib.sha256(image).hexdigest(): water(1260)}, [])

    [row] = evaluate(store, Files(FILES_BUCKET), model)

    assert {k: v for k, v in row.items() if k != "seconds"} == {
        "account": "acc",
        "serial": "W-1250",
        "date": "2026-10-01",
        "key": "acc/images/1.jpg",
        "failed_model": "x:old",
        "serial_ok": True,
        "value_ok": False,
        "unreadable": False,
        "refused": False,
    }
    assert row["seconds"] >= 0
    assert report([row]).startswith(
        "photos: 1, serial ok: 1/1, value ok: 0/1, both ok: 0/1, unreadable: 0/1, refused: 0/1"
    )


class RefusingModel(FakeModel):
    """Refuses the photos whose bytes are in refused, reads the rest as FakeModel does."""

    def __init__(self, readings, refused):
        super().__init__(readings, [])
        self._refused = refused

    def read_meter(self, image, media_type):
        if image in self._refused:
            raise ModelRefused("declined")
        return super().read_meter(image, media_type)


def test_evaluate_records_a_refusal_and_reads_the_other_photos(env, ddb):
    store = Store.from_env()
    store.put_reading(
        "acc",
        "W-1250",
        _reading(
            "2026-10-01",
            1250,
            failed_photos=["acc/images/0.jpg", "acc/images/1.jpg"],
            model="x:old",
        ),
    )
    s3 = boto3.client("s3", region_name=REGION)
    for key, body in (("acc/images/0.jpg", b"refused"), ("acc/images/1.jpg", b"read")):
        s3.put_object(Bucket=FILES_BUCKET, Key=key, Body=body, ContentType="image/jpeg")
    model = RefusingModel({hashlib.sha256(b"read").hexdigest(): water(1250)}, {b"refused"})

    rows = list(evaluate(store, Files(FILES_BUCKET), model))

    flags = ("serial_ok", "value_ok", "unreadable", "refused")
    assert [tuple(r[f] for f in flags) for r in rows] == [
        (False, False, False, True),
        (True, True, False, False),
    ]
    assert "acc/images/0.jpg: refused" in photo_line(rows[0])
    text = report(rows)
    assert "acc/images" not in text
    assert "photos: 2, serial ok: 1/2, value ok: 1/2, both ok: 1/2, unreadable: 0/2" in text
    assert "failed by x:old: 2" in text


class CountingModel(FakeModel):
    """Reads as FakeModel does and counts the photos it was sent."""

    def __init__(self, readings):
        super().__init__(readings, [])
        self.calls = 0

    def read_meter(self, image, media_type):
        self.calls += 1
        return super().read_meter(image, media_type)


def test_evaluate_stops_at_the_limit_before_calling_the_model_again(env, ddb):
    store = Store.from_env()
    keys = [f"acc/images/{n}.jpg" for n in range(3)]
    store.put_reading(
        "acc", "W-1250", _reading("2026-10-01", 1250, failed_photos=keys, model="x:old")
    )
    s3 = boto3.client("s3", region_name=REGION)
    for key in keys:
        s3.put_object(Bucket=FILES_BUCKET, Key=key, Body=key.encode(), ContentType="image/jpeg")
    model = CountingModel({hashlib.sha256(k.encode()).hexdigest(): water(1250) for k in keys})

    rows = list(evaluate(store, Files(FILES_BUCKET), model, limit=2))

    assert [r["key"] for r in rows] == keys[:2]
    assert model.calls == 2


def test_evaluate_yields_each_row_before_reading_the_next_photo(env, ddb):
    store = Store.from_env()
    keys = ["acc/images/0.jpg", "acc/images/1.jpg"]
    store.put_reading(
        "acc", "W-1250", _reading("2026-10-01", 1250, failed_photos=keys, model="x:old")
    )
    s3 = boto3.client("s3", region_name=REGION)
    for key in keys:
        s3.put_object(Bucket=FILES_BUCKET, Key=key, Body=key.encode(), ContentType="image/jpeg")
    model = CountingModel({hashlib.sha256(keys[0].encode()).hexdigest(): water(1250)})

    rows = evaluate(store, Files(FILES_BUCKET), model)

    assert next(rows)["key"] == keys[0]
    assert model.calls == 1


def test_report_with_no_rows_prints_a_zero_summary():
    assert report([]).startswith("photos: 0,")


@pytest.mark.parametrize("limit", ["0", "-1", "two"])
def test_main_refuses_a_limit_that_is_not_a_positive_whole_number(monkeypatch, capsys, limit):
    monkeypatch.setattr(sys, "argv", ["eval_readings.py", "meters-tracker", "--limit", limit])
    with pytest.raises(SystemExit) as stopped:
        main()
    assert stopped.value.code == 2
    assert "usage: eval-readings.sh [env] [--limit N]" in capsys.readouterr().err
