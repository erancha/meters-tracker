"""The fake model's lookup by image hash, the provider factory, and each real model's request
shape and refusal handling against a stubbed SDK client."""

import base64
import hashlib
import json
import logging
from datetime import date
from types import SimpleNamespace

import anthropic
import openai
import pytest
from fake_model import FakeModel
from openai.lib._pydantic import to_strict_json_schema

from common import prompts
from common.model import (
    ClaudeModel,
    IntentResult,
    MeterReadingResult,
    ModelRefused,
    OpenAIModel,
    Operation,
    StatementResult,
    Usage,
    build_model,
    call_cost,
    model_from_env,
)

IMAGE = b"\xff\xd8fake-jpeg"
USAGE = SimpleNamespace(input_tokens=1500, output_tokens=200)
MODEL_IDS = {ClaudeModel: "claude-opus-5-5", OpenAIModel: "gpt-6.1-sol"}


def _reading():
    return MeterReadingResult(
        meter_type="water",
        serial="123",
        serial_unreadable=False,
        serial_uncertain=False,
        candidates=[],
        handwritten_label=None,
    )


def _stub_anthropic(monkeypatch, response):
    calls = []

    class Messages:
        def parse(self, **kwargs):
            calls.append(kwargs)
            return response

    class Client:
        def __init__(self, **options):
            calls.append(options)
            self.messages = Messages()

    monkeypatch.setattr(anthropic, "Anthropic", Client)
    return calls


def _stub_openai(monkeypatch, response):
    calls = []

    class Responses:
        def parse(self, **kwargs):
            calls.append(kwargs)
            return response

    class Client:
        def __init__(self, **options):
            calls.append(options)
            self.responses = Responses()

    monkeypatch.setattr(openai, "OpenAI", Client)
    return calls


def _message(*parts):
    return SimpleNamespace(type="message", content=list(parts))


@pytest.mark.parametrize(
    "spec, cls, model_id",
    [
        ("anthropic:claude-opus-5-5", ClaudeModel, "claude-opus-5-5"),
        ("openai:gpt-6.1-sol", OpenAIModel, "gpt-6.1-sol"),
    ],
)
def test_build_model_picks_the_provider(monkeypatch, spec, cls, model_id):
    _stub_anthropic(monkeypatch, None)
    _stub_openai(monkeypatch, None)
    model = build_model(spec, "key")
    assert isinstance(model, cls)
    assert model.model_id == model_id


@pytest.mark.parametrize("spec", ["claude-opus-5-5", "anthropic:", "gemini:pro"])
def test_build_model_rejects_a_setting_it_cannot_serve(spec):
    with pytest.raises(ValueError, match="LLM_MODEL"):
        build_model(spec, "key")


def test_build_model_refuses_a_model_with_no_listed_price():
    with pytest.raises(
        ValueError, match="no listed price.*PRICE_PER_MILLION in src/common/model.py"
    ):
        build_model("anthropic:claude-unlisted", "key")


def test_call_cost_prices_input_and_output_tokens_per_million():
    assert call_cost("claude-opus-5-5", Usage(1500, 200)) == pytest.approx(0.01)


@pytest.mark.parametrize("missing", ["LLM_MODEL", "LLM_API_KEY"])
def test_model_from_env_names_a_missing_variable(monkeypatch, missing):
    monkeypatch.setenv("LLM_MODEL", "openai:gpt-6.1-sol")
    monkeypatch.setenv("LLM_API_KEY", "key")
    monkeypatch.delenv(missing)
    with pytest.raises(ValueError, match=missing):
        model_from_env()


def test_model_from_env_builds_the_named_model(monkeypatch):
    _stub_openai(monkeypatch, None)
    monkeypatch.setenv("LLM_MODEL", "openai:gpt-6.1-sol")
    monkeypatch.setenv("LLM_API_KEY", "key")
    assert isinstance(model_from_env(), OpenAIModel)


def _statement():
    return StatementResult(
        is_statement=True,
        electricity_per_kwh=0.6432,
        water_per_m3=15.62,
        includes_vat=True,
    )


def test_fake_model_returns_by_image_hash():
    reading = _reading()
    fake = FakeModel({hashlib.sha256(IMAGE).hexdigest(): reading}, [IntentResult(intent="confirm")])
    assert fake.read_meter(IMAGE, "image/jpeg") is reading
    assert fake.classify("כן", "card", date(2026, 10, 29)).intent == "confirm"
    assert fake.classified == [("כן", "card")]
    with pytest.raises(IndexError):
        fake.classify("עוד", "", date(2026, 10, 29))


def test_claude_model_builds_parse_request(monkeypatch):
    reading = _reading()
    response = SimpleNamespace(stop_reason="end_turn", parsed_output=reading, usage=USAGE)
    calls = _stub_anthropic(monkeypatch, response)
    assert ClaudeModel("key", "claude-opus-5-5").read_meter(IMAGE, "image/jpeg") is reading
    client, call = calls
    assert client == {"api_key": "key", "timeout": 25, "max_retries": 1}
    assert call["model"] == "claude-opus-5-5"
    assert call["system"] == prompts.READ_METER
    assert call["output_format"] is MeterReadingResult
    image, text = call["messages"][0]["content"]
    assert image == {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": base64.standard_b64encode(IMAGE).decode(),
        },
    }
    assert text["type"] == "text"


@pytest.mark.parametrize("cls, stub", [(ClaudeModel, "anthropic"), (OpenAIModel, "openai")])
def test_classify_tells_the_model_today_and_the_pending_card(monkeypatch, cls, stub):
    intent = IntentResult(intent="confirm")
    response = SimpleNamespace(
        stop_reason="end_turn",
        parsed_output=intent,
        output=[_message(SimpleNamespace(type="output_text"))],
        output_parsed=intent,
        usage=USAGE,
    )
    calls = (_stub_anthropic if stub == "anthropic" else _stub_openai)(monkeypatch, response)
    assert cls("key", MODEL_IDS[cls]).classify("כן", "כרטיס", date(2026, 10, 29)) is intent
    _, call = calls
    content = call["messages"][0]["content"] if stub == "anthropic" else call["input"][1]["content"]
    assert content == "Today: 2026-10-29\n\nכן\n\nContext:\nכרטיס"


def test_claude_model_raises_on_refusal(monkeypatch):
    response = SimpleNamespace(
        stop_reason="refusal", stop_details="policy", parsed_output=None, usage=USAGE
    )
    _stub_anthropic(monkeypatch, response)
    with pytest.raises(ModelRefused, match="policy"):
        ClaudeModel("key", "claude-opus-5-5").read_meter(IMAGE, "image/jpeg")


def test_openai_model_builds_parse_request(monkeypatch):
    reading = _reading()
    response = SimpleNamespace(
        output=[_message(SimpleNamespace(type="output_text"))], output_parsed=reading, usage=USAGE
    )
    calls = _stub_openai(monkeypatch, response)
    assert OpenAIModel("key", "gpt-6.1-sol").read_meter(IMAGE, "image/jpeg") is reading
    client, call = calls
    assert client == {"api_key": "key", "timeout": 25, "max_retries": 1}
    assert call["model"] == "gpt-6.1-sol"
    assert call["text_format"] is MeterReadingResult
    system, user = call["input"]
    assert system == {"role": "system", "content": prompts.READ_METER}
    image, text = user["content"]
    data = base64.standard_b64encode(IMAGE).decode()
    assert image == {"type": "input_image", "image_url": f"data:image/jpeg;base64,{data}"}
    assert text["type"] == "input_text"


def test_openai_model_raises_on_refusal(monkeypatch):
    refusal = SimpleNamespace(type="refusal", refusal="policy")
    response = SimpleNamespace(output=[_message(refusal)], output_parsed=None, usage=USAGE)
    _stub_openai(monkeypatch, response)
    with pytest.raises(ModelRefused, match="policy"):
        OpenAIModel("key", "gpt-6.1-sol").classify("כן", "", date(2026, 10, 29))


@pytest.mark.parametrize("cls, stub", [(ClaudeModel, _stub_anthropic), (OpenAIModel, _stub_openai)])
def test_model_keeps_the_last_calls_usage(monkeypatch, cls, stub):
    reading = _reading()
    response = SimpleNamespace(
        stop_reason="end_turn",
        parsed_output=reading,
        output=[_message(SimpleNamespace(type="output_text"))],
        output_parsed=reading,
        usage=USAGE,
    )
    stub(monkeypatch, response)
    model = cls("key", MODEL_IDS[cls])
    assert model.last_usage is None
    model.read_meter(IMAGE, "image/jpeg")
    assert model.last_usage == Usage(1500, 200)


def test_fake_model_returns_statements_by_image_hash():
    statement = _statement()
    fake = FakeModel({}, [], {hashlib.sha256(IMAGE).hexdigest(): statement})
    assert fake.read_statement(IMAGE, "image/jpeg") is statement


def test_claude_model_builds_statement_request(monkeypatch):
    statement = _statement()
    response = SimpleNamespace(stop_reason="end_turn", parsed_output=statement, usage=USAGE)
    calls = _stub_anthropic(monkeypatch, response)
    assert ClaudeModel("key", "claude-opus-5-5").read_statement(IMAGE, "image/png") is statement
    _, call = calls
    assert call["system"] == prompts.READ_STATEMENT
    assert call["output_format"] is StatementResult
    image, text = call["messages"][0]["content"]
    assert image["source"]["media_type"] == "image/png"
    assert text["type"] == "text"


def test_openai_model_builds_statement_request(monkeypatch):
    statement = _statement()
    response = SimpleNamespace(
        output=[_message(SimpleNamespace(type="output_text"))],
        output_parsed=statement,
        usage=USAGE,
    )
    calls = _stub_openai(monkeypatch, response)
    assert OpenAIModel("key", "gpt-6.1-sol").read_statement(IMAGE, "image/png") is statement
    _, call = calls
    assert call["text_format"] is StatementResult
    system, user = call["input"]
    assert system == {"role": "system", "content": prompts.READ_STATEMENT}
    image, text = user["content"]
    data = base64.standard_b64encode(IMAGE).decode()
    assert image == {"type": "input_image", "image_url": f"data:image/png;base64,{data}"}
    assert text["type"] == "input_text"


def test_statement_prompt_ties_includes_vat_to_the_reported_prices():
    prompt = " ".join(prompts.READ_STATEMENT.split())
    assert "Set includes_vat when the prices you report include VAT" in prompt


def test_set_tariffs_intent_carries_both_prices():
    intent = IntentResult(intent="set_tariffs", electricity_price=0.6432, water_price=None)
    assert (intent.electricity_price, intent.water_price) == (0.6432, None)
    assert "set_tariffs" in prompts.CLASSIFY


def test_intent_result_carries_operations():
    result = IntentResult.model_validate(
        {
            "intent": "edit",
            "operations": [
                {"action": "set", "target": "meter|2721056", "field": "serial", "value": "2721058"}
            ],
        }
    )
    (operation,) = result.operations
    assert isinstance(operation, Operation)
    assert (operation.action, operation.target) == ("set", "meter|2721056")
    assert (operation.field, operation.value) == ("serial", "2721058")
    assert IntentResult(intent="confirm").operations == []


def test_strict_schema_carries_no_non_null_default():
    def non_null_defaults(node, path="$"):
        found = []
        if isinstance(node, dict):
            if "default" in node and node["default"] is not None:
                found.append(path)
            for key, child in node.items():
                found += non_null_defaults(child, f"{path}.{key}")
        elif isinstance(node, list):
            for index, child in enumerate(node):
                found += non_null_defaults(child, f"{path}[{index}]")
        return found

    assert non_null_defaults(to_strict_json_schema(IntentResult)) == []


def _answered(result):
    return SimpleNamespace(
        stop_reason="end_turn",
        parsed_output=result,
        output=[_message(SimpleNamespace(type="output_text"))],
        output_parsed=result,
        usage=USAGE,
    )


def _call_lines(caplog):
    return [json.loads(r.getMessage()) for r in caplog.records if r.getMessage().startswith("{")]


@pytest.mark.parametrize(
    "cls, stub, provider",
    [(ClaudeModel, _stub_anthropic, "anthropic"), (OpenAIModel, _stub_openai, "openai")],
)
def test_each_call_logs_its_kind_model_tokens_seconds_and_cost(
    monkeypatch, caplog, cls, stub, provider
):
    stub(monkeypatch, _answered(_reading()))
    model = cls("key", MODEL_IDS[cls])
    with caplog.at_level(logging.INFO, logger="common.model"):
        model.read_meter(IMAGE, "image/jpeg")
        model.classify("כן", "", date(2026, 10, 29))
    meter, classify = _call_lines(caplog)
    assert meter["event"] == "model_call"
    assert (meter["kind"], classify["kind"]) == ("read_meter", "classify")
    assert meter["model"] == f"{provider}:{MODEL_IDS[cls]}"
    assert (meter["input_tokens"], meter["output_tokens"]) == (1500, 200)
    assert meter["seconds"] >= 0
    assert meter["cost"] == pytest.approx(call_cost(MODEL_IDS[cls], Usage(1500, 200)))


def test_a_refused_call_logs_its_line(monkeypatch, caplog):
    response = SimpleNamespace(
        stop_reason="refusal", stop_details="policy", parsed_output=None, usage=USAGE
    )
    _stub_anthropic(monkeypatch, response)
    with caplog.at_level(logging.INFO, logger="common.model"), pytest.raises(ModelRefused):
        ClaudeModel("key", "claude-opus-5-5").read_statement(IMAGE, "image/png")
    (line,) = _call_lines(caplog)
    assert (line["kind"], line["input_tokens"]) == ("read_statement", 1500)
