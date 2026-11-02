"""The smoke replay behind scripts/replay.sh, run against the api handler in process over moto and
the fake model: the whole walk, a card of an unexpected kind or an error status stopping it, and
the period start it types, which puts every day in the close window."""

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
from botocore.exceptions import NoCredentialsError
from engine_kit import (
    ACCOUNT,
    NOT_STATEMENT,
    UNKNOWN,
    Kit,
    edit,
    edits,
    electricity,
    statement,
    water,
)

from common import dates
from common.model import IntentResult
from common.periods import is_closing_candidate
from handlers import api

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import replay
from owner_story import APARTMENT, BOTTOM_OFFSET, CLOSING, OPENING, PRICES, PROPERTY, SERIAL
from replay import Api, Unexpected, start_for, walk

MONTHS = 2


def _electricity(top):
    return electricity(top, top + BOTTOM_OFFSET, SERIAL)


@pytest.fixture
def kit(env, ddb, monkeypatch):
    kit = Kit()
    monkeypatch.setattr(api, "_model", lambda: kit.model)
    return kit


def _photo(kit, tmp_path, name, result, as_statement=NOT_STATEMENT):
    path = tmp_path / name
    path.write_bytes(name.encode())
    kit.register_photo(path.read_bytes(), result, as_statement)
    return path


def _api():
    return Api(lambda event: api.handler(event, None), ACCOUNT)


def _queue(kit, today, *extra):
    for intent in (
        edits(("property", PROPERTY), ("apartment", APARTMENT)),
        edit("period_start", start_for(today, MONTHS).isoformat()),
        edits(
            ("electricity_price", str(PRICES["electricity"])),
            ("water_price", str(PRICES["water"])),
        ),
        *extra,
    ):
        kit.model.queue(intent)


def test_the_walk_reaches_a_charge_and_cancels_the_statement(kit, tmp_path, capsys):
    today = dates.today()
    _queue(kit, today, IntentResult(intent="reject"))
    photos = {
        "open": _photo(kit, tmp_path, "open.jpg", _electricity(OPENING)),
        "close": _photo(kit, tmp_path, "close.jpg", _electricity(CLOSING)),
        "statement": _photo(kit, tmp_path, "statement.jpg", UNKNOWN, statement()),
    }
    walk(_api(), photos["open"], photos["close"], photos["statement"], today, MONTHS)
    out = capsys.readouterr().out
    assert "[card] tariffs_ask" in out
    assert "1,332 קוט״ש, חיוב 856.74 ₪" in out
    assert out.rstrip().endswith("waiting: 0")


def test_a_card_of_another_kind_stops_the_walk(kit, tmp_path):
    blurry = _photo(kit, tmp_path, "open.jpg", water(1250, unreadable=True))
    with pytest.raises(Unexpected, match="expected a property card, got unreadable"):
        walk(_api(), blurry, blurry, None, dates.today(), MONTHS)


def test_an_error_status_stops_the_walk(kit):
    with pytest.raises(Unexpected, match="POST /turn answered 400"):
        _api().call("POST /turn", {"bogus": 1})


def test_the_typed_start_puts_every_day_of_a_year_in_the_close_window():
    for offset in range(366):
        today = date(2026, 1, 1) + timedelta(days=offset)
        start = start_for(today, MONTHS)
        period = {"start": start.isoformat(), "months": MONTHS}
        assert start < today
        assert is_closing_candidate(period, today, 7)


def _main(monkeypatch, tmp_path, *extra, run=None):
    """Runs main on a stub reset that records whether it was confirmed, and a stub run;
    returns the recorded confirmations and the walks run."""
    confirmed, walks = [], []

    def reset(stack, address, confirm):
        confirmed.append(confirm)
        return confirm

    photo = tmp_path / "meter.jpg"
    photo.write_bytes(b"jpeg")
    monkeypatch.setattr(replay, "reset", reset)
    monkeypatch.setattr(replay, "run", run or (lambda *args: walks.append(args)))
    argv = ["replay.py", "meters-tracker-dev", "A@x.com", "--open", photo, "--close", photo]
    monkeypatch.setattr(sys, "argv", [str(arg) for arg in [*argv, *extra]])
    replay.main()
    return confirmed, walks


def test_main_without_yes_resets_nothing_and_exits_one(monkeypatch, capsys, tmp_path):
    with pytest.raises(SystemExit) as stopped:
        _main(monkeypatch, tmp_path)
    assert stopped.value.code == 1
    assert "--yes" in capsys.readouterr().err


def test_main_with_yes_resets_the_account_then_walks(monkeypatch, tmp_path):
    confirmed, walks = _main(monkeypatch, tmp_path, "--yes")
    assert confirmed == [True]
    assert [(stack, address) for stack, address, *_ in walks] == [("meters-tracker-dev", "a@x.com")]


def test_main_exits_one_on_an_unexpected_card(monkeypatch, capsys, tmp_path):
    def fail(*args):
        raise Unexpected("expected a reading card, got duplicate")

    with pytest.raises(SystemExit) as stopped:
        _main(monkeypatch, tmp_path, "--yes", run=fail)
    assert stopped.value.code == 1
    assert "expected a reading card, got duplicate" in capsys.readouterr().err


def test_main_refuses_a_photo_that_is_not_a_jpeg_or_png(monkeypatch, capsys, tmp_path):
    scan = tmp_path / "meter.pdf"
    scan.write_bytes(b"pdf")
    argv = ["replay.py", "meters-tracker-dev", "a@x.com", "--open", scan, "--close", scan]
    monkeypatch.setattr(sys, "argv", [str(arg) for arg in argv])
    with pytest.raises(SystemExit) as stopped:
        replay.main()
    assert stopped.value.code == 2
    assert "meter.pdf is not a JPEG or PNG" in capsys.readouterr().err


def test_main_stops_with_one_line_on_an_aws_error(monkeypatch, capsys, tmp_path):
    def no_credentials(*args):
        raise NoCredentialsError()

    with pytest.raises(SystemExit) as stopped:
        _main(monkeypatch, tmp_path, "--yes", run=no_credentials)
    assert stopped.value.code == 2
    fix = "run scripts/init-env.sh or check scripts/aws-config.sh"
    assert capsys.readouterr().err.splitlines() == [
        f"AWS call failed: Unable to locate credentials; {fix}"
    ]
