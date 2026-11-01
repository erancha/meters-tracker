"""The intent evaluation behind scripts/eval-intents.sh, run with the fake model: loading and
validating the recorded cases, the card context each case is classified with, comparing only
the listed fields, and the printed lines, totals and exit status."""

import json
import sys
from datetime import date
from pathlib import Path

import anthropic
import httpx2
import pytest
from fake_model import FakeModel

from common.flows import classify_context, request_context
from common.model import IntentResult

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import eval_intents
from eval_intents import CARDS, Case, context, evaluate, load_cases, mismatches, report

CASES = Path(__file__).resolve().parent / "intents.json"


def _case(**overrides):
    fields = {
        "message": "1/9",
        "card": "period",
        "today": date(2026, 10, 29),
        "intent": "set_period",
        "fields": {"date": "2026-09-01"},
        "apartments": [],
    }
    return Case(**{**fields, **overrides})


def _write(tmp_path, cases):
    path = tmp_path / "intents.json"
    path.write_text(json.dumps(cases, ensure_ascii=False))
    return path


def test_the_recorded_cases_load(env):
    cases = load_cases(CASES)
    assert len(cases) >= 40
    assert {c.card for c in cases} - {None} <= set(CARDS)


def test_a_case_naming_an_unknown_card_is_refused(tmp_path):
    raw = {"message": "x", "card": "nope", "today": "2026-10-29", "intent": "confirm"}
    with pytest.raises(ValueError, match="card 'nope'"):
        load_cases(_write(tmp_path, [raw]))


def test_a_case_expecting_a_field_the_intent_has_not_is_refused(tmp_path):
    raw = {
        "message": "x",
        "card": None,
        "today": "2026-10-29",
        "intent": "confirm",
        "fields": {"colour": "red"},
    }
    with pytest.raises(ValueError, match="colour"):
        load_cases(_write(tmp_path, [raw]))


def test_a_case_expecting_an_unknown_intent_is_refused(tmp_path):
    raw = {"message": "x", "card": None, "today": "2026-10-29", "intent": "dance"}
    with pytest.raises(ValueError, match="dance"):
        load_cases(_write(tmp_path, [raw]))


def test_context_is_the_card_as_the_engine_sends_it():
    assert context(_case(card=None)) == ""
    period = context(_case())
    assert period == classify_context(CARDS["period"])
    assert period.endswith("כפתורים: 29/10 (היום) | 01/11 (תחילת חודש)")


def test_a_request_is_sent_with_the_apartments_after_any_standby_card():
    names = ["דירה קדמית", "עורף"]
    assert context(_case(card=None, apartments=names)) == request_context(names)
    assert context(_case(card=None, apartments=names)) == "דירות: דירה קדמית | עורף"
    asked = context(_case(card="tariffs_ask", apartments=names))
    assert asked == request_context(names, CARDS["tariffs_ask"])


def test_apartments_on_a_card_that_awaits_an_answer_are_refused(tmp_path):
    raw = {
        "message": "כן",
        "card": "reading",
        "today": "2026-10-29",
        "intent": "confirm",
        "apartments": ["עורף"],
    }
    with pytest.raises(ValueError, match="apartments"):
        load_cases(_write(tmp_path, [raw]))


def test_only_the_listed_fields_are_compared():
    result = IntentResult(intent="set_period", date="2026-09-01", months=2)
    assert mismatches(_case(), result) == []
    wrong = IntentResult(intent="set_date", date="2026-09-02")
    assert mismatches(_case(), wrong) == [
        "intent set_date, expected set_period",
        "date 2026-09-02, expected 2026-09-01",
    ]


class DatedModel(FakeModel):
    """Classifies as FakeModel does and records the day each message was classified on."""

    def __init__(self, intents):
        super().__init__({}, intents)
        self.days = []

    def classify(self, text, context, today):
        self.days.append(today)
        return super().classify(text, context, today)


def test_evaluate_classifies_each_case_with_its_context_and_today():
    model = DatedModel([IntentResult(intent="set_period", date="2026-09-01")])
    case = _case(today=date(2026, 3, 10))
    assert list(evaluate([case], model)) == [(case, [])]
    assert model.classified == [("1/9", classify_context(CARDS["period"]))]
    assert model.days == [date(2026, 3, 10)]


def test_report_counts_the_passed_cases():
    assert report([[], ["intent x, expected y"]]) == "passed 1/2"


def test_main_exits_one_when_a_case_fails(tmp_path, monkeypatch, capsys):
    raw = [
        {"message": "כן", "card": "reading", "today": "2026-10-29", "intent": "confirm"},
        {"message": "לא", "card": "reading", "today": "2026-10-29", "intent": "reject"},
    ]
    model = FakeModel({}, [IntentResult(intent="confirm"), IntentResult(intent="confirm")])
    monkeypatch.setattr(eval_intents, "model_from_env", lambda: model)
    monkeypatch.setattr(sys, "argv", ["eval_intents.py", str(_write(tmp_path, raw))])
    with pytest.raises(SystemExit) as stopped:
        eval_intents.main()
    assert stopped.value.code == 1
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("PASS") and "כן" in out[0]
    assert out[1].startswith("FAIL") and "intent confirm, expected reject" in out[1]
    assert out[2] == "passed 1/2"


def test_main_exits_zero_when_every_case_passes(tmp_path, monkeypatch, capsys):
    raw = [{"message": "כן", "card": None, "today": "2026-10-29", "intent": "confirm"}]
    model = FakeModel({}, [IntentResult(intent="confirm")])
    monkeypatch.setattr(eval_intents, "model_from_env", lambda: model)
    monkeypatch.setattr(sys, "argv", ["eval_intents.py", str(_write(tmp_path, raw))])
    eval_intents.main()
    assert capsys.readouterr().out.splitlines()[-1] == "passed 1/1"


class FailingModel(FakeModel):
    """Answers the first case, then fails as an unreachable provider does."""

    def classify(self, text, context, today):
        if self.classified:
            raise anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api"))
        return super().classify(text, context, today)


def test_main_stops_with_one_line_on_a_provider_error(tmp_path, monkeypatch, capsys):
    raw = [{"message": m, "card": None, "today": "2026-10-29", "intent": "confirm"} for m in "ab"]
    model = FailingModel({}, [IntentResult(intent="confirm")])
    monkeypatch.setattr(eval_intents, "model_from_env", lambda: model)
    monkeypatch.setattr(sys, "argv", ["eval_intents.py", str(_write(tmp_path, raw))])
    with pytest.raises(SystemExit) as stopped:
        eval_intents.main()
    assert stopped.value.code == 2
    captured = capsys.readouterr()
    assert captured.out.startswith("PASS 'a'")
    assert captured.err.splitlines() == [
        "model call failed: Connection error.; check LLM_API_KEY and the provider account"
    ]
