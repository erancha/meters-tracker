"""The fake model's lookup by image hash, the provider factory, and each real model's request
shape and refusal handling against a stubbed SDK client."""

import base64
import hashlib
from types import SimpleNamespace

import anthropic
import openai
import pytest
from fake_model import FakeModel

from common import prompts
from common.model import (
    ClaudeModel,
    IntentResult,
    MeterReadingResult,
    ModelRefused,
    OpenAIModel,
    Usage,
    build_model,
    model_from_env,
)

IMAGE = b"\xff\xd8fake-jpeg"
USAGE = SimpleNamespace(input_tokens=1500, output_tokens=200)


def _reading():
    return MeterReadingResult(
        meter_type="water",
        serial="123",
        serial_unreadable=False,
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


def test_fake_model_returns_by_image_hash():
    reading = _reading()
    fake = FakeModel({hashlib.sha256(IMAGE).hexdigest(): reading}, [IntentResult(intent="confirm")])
    assert fake.read_meter(IMAGE, "image/jpeg") is reading
    assert fake.classify("כן", "card").intent == "confirm"
    assert fake.classified == [("כן", "card")]
    with pytest.raises(IndexError):
        fake.classify("עוד", "")


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
        OpenAIModel("key", "gpt-6.1-sol").classify("כן", "")


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
    model = cls("key", "m")
    assert model.last_usage is None
    model.read_meter(IMAGE, "image/jpeg")
    assert model.last_usage == Usage(1500, 200)
