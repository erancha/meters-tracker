"""The intent evaluation behind scripts/tools/eval-intents.sh, run with the fake model: loading and
validating the recorded cases, the card and records context each case is classified with,
comparing only the listed fields, and the printed lines, totals and exit status."""

import json
import sys
from datetime import date
from pathlib import Path

import anthropic
import httpx2
import pytest
from fake_model import FakeModel

from common import account, texts
from common.flows import classify_context, message_context
from common.model import IntentResult, RecordsQuery

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "tools"))
import eval_intents
from eval_intents import (
    CARD_SEQ,
    CARDS,
    FORMS,
    RECORDS,
    Case,
    _listed_ids,
    context,
    evaluate,
    finder,
    load_cases,
    mismatches,
    report,
    selected,
)

CASES = Path(__file__).resolve().parent / "intents.json"


def _case(**overrides):
    fields = {
        "message": "1/9",
        "card": "period",
        "today": date(2026, 10, 29),
        "intent": "change_period",
        "fields": {"date": "2026-09-01"},
        "apartments": [],
        "records": None,
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


def test_every_card_is_sent_with_its_fields():
    assert set(FORMS) == set(CARDS)


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


def _card_line(name) -> str:
    fields = "; ".join(
        f"{key}={texts.EMPTY_VALUE if value is None else value}"
        for key, value in FORMS[name].items()
    )
    return f"card|{CARD_SEQ}: {fields}"


def test_context_is_the_card_then_its_records_line_as_the_engine_sends_them():
    assert context(_case(card=None)) == ""
    period = context(_case())
    card = classify_context(CARDS["period"], FORMS["period"])
    assert card.endswith("כפתורים: 29/10 (היום) | 01/11 (תחילת חודש)")
    assert period == f"{card}\n{texts.RECORDS_TITLE}:\n{_card_line('period')}"


def test_a_card_that_asked_for_a_field_names_it():
    asked = context(_case(card="reading_asked_serial"))
    assert f"\n{texts.ASKED_TITLE}: serial\n" in asked


def test_a_case_with_records_is_sent_with_its_card_and_the_cards_behind_and_no_records():
    case = _case(card="reading", records="story")
    lines = context(case).splitlines()
    records = lines[lines.index(f"{texts.RECORDS_TITLE}:") + 1 :]
    assert not any(line.startswith(("property|", "reading|")) for line in records)
    cards = [line for line in records if line.startswith("card|")]
    assert cards[0] == _card_line("reading")
    assert len(cards) == 1 + len(RECORDS["story"].behind)
    assert sum(line.startswith("photo|") for line in records) == len(RECORDS["story"].photos)


def test_a_case_naming_unknown_records_is_refused(tmp_path):
    raw = {"message": "x", "card": None, "today": "2026-10-29", "intent": "unknown"}
    with pytest.raises(ValueError, match="records 'nope'"):
        load_cases(_write(tmp_path, [{**raw, "records": "nope"}]))


def test_a_case_listing_records_and_apartments_is_refused(tmp_path):
    raw = {
        "message": "x",
        "card": None,
        "today": "2026-10-29",
        "intent": "unknown",
        "records": "story",
        "apartments": ["עורף"],
    }
    with pytest.raises(ValueError, match="not both"):
        load_cases(_write(tmp_path, [raw]))


def test_an_expected_target_the_records_block_lacks_is_refused(tmp_path):
    operation = {"action": "set", "target": "meter|nope", "field": "type", "value": "water"}
    raw = {
        "message": "x",
        "card": None,
        "today": "2026-10-29",
        "intent": "edit",
        "records": "story",
        "fields": {"operations": [operation]},
    }
    with pytest.raises(ValueError, match="meter\\|nope"):
        load_cases(_write(tmp_path, [raw]))


def test_the_recorded_cases_cover_corrections_and_questions(env):
    cases = load_cases(CASES)
    assert {"how_to", "which", "not_offered", "unknown"} <= {c.intent for c in cases}
    assert any(c.card == "correction" and c.message.startswith("לא") for c in cases)
    targets = {o["target"].partition("|")[0] for c in cases for o in c.fields.get("operations", [])}
    assert {"card", "property", "apartment", "meter", "reading", "period", "tariff"} <= targets
    assert {"delegate", "photo"} <= targets


def test_a_request_finds_the_apartments_its_case_lists():
    names = ["דירה קדמית", "עורף"]
    assert context(_case(card=None, apartments=names)) == ""
    found = finder(_case(card=None, apartments=names))(QUERY_ALL)
    assert all(f"name={name}; " in found for name in names)
    asked = context(_case(card="tariffs_ask", apartments=names))
    standby = classify_context(CARDS["tariffs_ask"], FORMS["tariffs_ask"])
    block = f"{texts.RECORDS_TITLE}:\n{_card_line('tariffs_ask')}"
    assert asked == message_context(block, standby)


def test_evaluate_passes_each_case_a_lookup_over_its_records():
    model = LookingModel({}, [IntentResult(intent="unknown")])
    list(evaluate([_case(card="reading", records="story")], model))
    assert "property|p1: name=לאונר 18" in model.found
    assert "reading|4410290|2026-10-29#" in model.found


def test_an_expected_target_may_be_any_record_of_the_case_account():
    ids = _listed_ids(_case(card="reading", records="story"))
    lines = account.record_lines(RECORDS["story"].state, 12)
    assert {line.partition(": ")[0] for line in lines} <= ids
    assert {"card", f"card|{CARD_SEQ}"} <= ids


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
    result = IntentResult(intent="change_period", date="2026-09-01", months=2)
    assert mismatches(_case(), result) == []
    wrong = IntentResult(intent="close_period", date="2026-09-02")
    assert mismatches(_case(), wrong) == [
        "intent close_period, expected change_period",
        "date 2026-09-02, expected 2026-09-01",
    ]


class DatedModel(FakeModel):
    """Classifies as FakeModel does and records the day each message was classified on."""

    def __init__(self, intents):
        super().__init__({}, intents)
        self.days = []

    def classify(self, text, context, today, find):
        self.days.append(today)
        return super().classify(text, context, today, find)


# A find_records query that names nothing, so it lists every apartment and meter.
QUERY_ALL = RecordsQuery(
    apartment=None, meter_type=None, reading_date=None, tariffs=False, delegates=False
)


class LookingModel(FakeModel):
    """Looks every case's records up as the real model may, keeping the answer in found."""

    def classify(self, text, context, today, find):
        self.found = find(QUERY_ALL.model_copy(update={"apartment": "עורף"}))
        return super().classify(text, context, today, find)


def test_evaluate_classifies_each_case_with_its_context_and_today():
    model = DatedModel([IntentResult(intent="change_period", date="2026-09-01")])
    case = _case(today=date(2026, 3, 10))
    assert list(evaluate([case], model)) == [(case, [])]
    assert model.classified == [("1/9", context(case))]
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

    def classify(self, text, context, today, find):
        if self.classified:
            raise anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api"))
        return super().classify(text, context, today, find)


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


def _set(field, value):
    return {"action": "set", "target": "card", "field": field, "value": value}


def test_operations_are_compared_whatever_their_order():
    case = _case(
        intent="edit",
        fields={"operations": [_set("period_start", "2026-09-01"), _set("months", "3")]},
    )
    swapped = IntentResult(
        intent="edit", operations=[_set("months", "3"), _set("period_start", "2026-09-01")]
    )
    assert mismatches(case, swapped) == []
    other = IntentResult(intent="edit", operations=[_set("months", "4")])
    assert len(mismatches(case, other)) == 1


def test_an_edit_case_without_operations_is_refused(tmp_path):
    raw = {"message": "x", "card": "reading", "today": "2026-10-29", "intent": "edit"}
    with pytest.raises(ValueError, match="operations"):
        load_cases(_write(tmp_path, [raw]))


def test_a_case_with_a_malformed_operation_is_refused(tmp_path):
    raw = {
        "message": "x",
        "card": "reading",
        "today": "2026-10-29",
        "intent": "edit",
        "fields": {"operations": [{"action": "move", "target": "card"}]},
    }
    with pytest.raises(ValueError, match="action"):
        load_cases(_write(tmp_path, [raw]))


def _story_edit(tmp_path, operation, card=None):
    raw = {
        "message": "x",
        "card": card,
        "today": "2026-10-29",
        "intent": "edit",
        "records": "story",
        "fields": {"operations": [operation]},
    }
    return load_cases(_write(tmp_path, [raw]))


@pytest.mark.parametrize(
    ("operation", "card", "error"),
    [
        (
            {"action": "set", "target": "meter|5500123", "field": "tpye", "value": "water"},
            None,
            "tpye",
        ),
        ({"action": "set", "target": "card", "field": "months", "value": "3"}, "reading", "months"),
        (
            {
                "action": "set",
                "target": "photo|2026-10-29T13:00:00.000000_c3d4",
                "field": "colour",
                "value": "red",
            },
            None,
            "colour",
        ),
        (
            {"action": "set", "target": "meter|5500123", "field": "type", "value": "gas"},
            None,
            "gas",
        ),
        (
            {"action": "set", "target": "card", "field": "date", "value": "2026-11-14"},
            "reading",
            "2026-11-14",
        ),
    ],
)
def test_an_expected_field_or_value_the_target_cannot_take_is_refused(
    tmp_path, operation, card, error
):
    with pytest.raises(ValueError, match=error):
        _story_edit(tmp_path, operation, card)


def test_an_expected_bare_value_or_left_out_value_is_not_checked(tmp_path):
    _story_edit(
        tmp_path, {"action": "set", "target": "card", "field": None, "value": "x"}, "reading"
    )
    reading = "reading|4410290|2026-09-01#2026-09-01T09:00:00"
    _story_edit(tmp_path, {"action": "set", "target": reading, "field": "value", "value": None})


def test_selected_keeps_only_the_cases_expecting_the_named_intents():
    cases = [_case(intent="edit"), _case(intent="unknown"), _case(intent="resend_reports")]
    assert [c.intent for c in selected(cases, "resend_reports,unknown")] == [
        "unknown",
        "resend_reports",
    ]


def test_selected_refuses_an_intent_the_classifier_has_not():
    with pytest.raises(ValueError, match="resend"):
        selected([_case()], "resend")


def test_selected_refuses_a_list_matching_no_case():
    with pytest.raises(ValueError, match="no case expects"):
        selected([_case(intent="edit")], "unknown")
