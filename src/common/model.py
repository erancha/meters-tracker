"""Typed interface to the vision/language model: reading meter photos and classifying chat text.

Core pieces:
- MeterReadingResult / IntentResult: pydantic schemas the model's answers are parsed into
- Model: the Protocol the rest of the app depends on (tests use a fake)
- ClaudeModel / OpenAIModel: one implementation per provider, each asking its API for answers
  shaped to the schema through the provider's own SDK
- build_model: picks the implementation from a "provider:model" setting
- model_from_env: build_model over LLM_MODEL and LLM_API_KEY, for the local scripts
- api_key_from_ssm: loads the API key from an SSM SecureString parameter
- Usage: the billed tokens of one call, which each client keeps for its last call as last_usage
"""

import base64
import os
from dataclasses import dataclass
from typing import Literal, Protocol

import anthropic
import boto3
import openai
from pydantic import BaseModel

from common import prompts

PROVIDERS = ("anthropic", "openai")
MAX_TOKENS = 2000
# A hung attempt fails in seconds rather than holding the turn until the api Lambda is killed;
# a photo's read and its note's classification share that Lambda's time limit.
REQUEST_TIMEOUT_SECONDS = 25
MAX_RETRIES = 1


class Candidate(BaseModel):
    value: int | None
    position: Literal["top", "bottom", "single"]
    unit: str | None
    unreadable_digits: bool
    note: str


class MeterReadingResult(BaseModel):
    meter_type: Literal["water", "electricity", "unknown"]
    serial: str | None
    serial_unreadable: bool
    candidates: list[Candidate]
    handwritten_label: str | None


class IntentResult(BaseModel):
    intent: Literal[
        "confirm",
        "reject",
        "correct_field",
        "set_date",
        "choose_property",
        "choose_apartment",
        "set_period",
        "close_period",
        "vacate",
        "change_period",
        "unknown",
    ]
    field: str | None = None
    value: int | None = None
    date: str | None = None
    name: str | None = None
    is_new: bool | None = None
    months: int | None = None


class ModelRefused(Exception):
    """The model declined to answer."""


@dataclass(frozen=True)
class Usage:
    """Billed tokens of one call; reasoning tokens count as output."""

    input_tokens: int
    output_tokens: int


class Model(Protocol):
    """What the app needs from a model: read one meter photo, classify one chat message given the
    text describing the pending card (empty when there is none)."""

    def read_meter(self, image: bytes, media_type: str) -> MeterReadingResult: ...

    def classify(self, text: str, context: str) -> IntentResult: ...


def api_key_from_ssm(param_name: str) -> str:
    ssm = boto3.client("ssm")
    return ssm.get_parameter(Name=param_name, WithDecryption=True)["Parameter"]["Value"]


def build_model(spec: str, api_key: str) -> Model:
    """Builds the model named by a "provider:model" setting, such as
    anthropic:claude-opus-5-5 or openai:gpt-6.1-sol."""
    provider, separator, model_id = spec.partition(":")
    if not separator or not model_id:
        raise ValueError(f"LLM_MODEL={spec!r} must be provider:model")
    if provider not in PROVIDERS:
        raise ValueError(f"LLM_MODEL provider {provider!r} is not one of {', '.join(PROVIDERS)}")
    if provider == "openai":
        return OpenAIModel(api_key, model_id)
    return ClaudeModel(api_key, model_id)


def model_from_env() -> Model:
    """build_model over LLM_MODEL and LLM_API_KEY; a missing variable raises ValueError naming
    the fix."""
    if "LLM_MODEL" not in os.environ:
        raise ValueError("LLM_MODEL is not set; export it first; see scripts/params.example.sh")
    if "LLM_API_KEY" not in os.environ:
        raise ValueError(
            "LLM_API_KEY is not set; export the API key of the provider LLM_MODEL names"
        )
    return build_model(os.environ["LLM_MODEL"], os.environ["LLM_API_KEY"])


class ClaudeModel(Model):
    """Model backed by the Anthropic API: one request per call, its answer parsed into the
    result schema, a refusal raised as ModelRefused."""

    def __init__(self, api_key: str, model_id: str):
        self._client = anthropic.Anthropic(
            api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS, max_retries=MAX_RETRIES
        )
        self.model_id = model_id
        self.last_usage: Usage | None = None

    def read_meter(self, image: bytes, media_type: str) -> MeterReadingResult:
        content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64.standard_b64encode(image).decode(),
                },
            },
            {"type": "text", "text": "Read this meter."},
        ]
        return self._parse(prompts.READ_METER, content, MeterReadingResult)

    def classify(self, text: str, context: str) -> IntentResult:
        content = f"{text}\n\nPending card:\n{context}" if context else text
        return self._parse(prompts.CLASSIFY, content, IntentResult)

    # The installed SDK's messages.parse takes no fallbacks/betas; see the claude-api skill,
    # section "Refusal Fallbacks", when upgrading.
    def _parse(self, system: str, content, output_format):
        response = self._client.messages.parse(
            model=self.model_id,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": content}],
            output_format=output_format,
        )
        self.last_usage = Usage(response.usage.input_tokens, response.usage.output_tokens)
        if response.stop_reason == "refusal":
            raise ModelRefused(f"model refused: {response.stop_details}")
        return response.parsed_output


class OpenAIModel(Model):
    """Model backed by the OpenAI Responses API: one request per call, its answer parsed into
    the result schema, a refusal raised as ModelRefused."""

    def __init__(self, api_key: str, model_id: str):
        self._client = openai.OpenAI(
            api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS, max_retries=MAX_RETRIES
        )
        self.model_id = model_id
        self.last_usage: Usage | None = None

    def read_meter(self, image: bytes, media_type: str) -> MeterReadingResult:
        data = base64.standard_b64encode(image).decode()
        content = [
            {"type": "input_image", "image_url": f"data:{media_type};base64,{data}"},
            {"type": "input_text", "text": "Read this meter."},
        ]
        return self._parse(prompts.READ_METER, content, MeterReadingResult)

    def classify(self, text: str, context: str) -> IntentResult:
        content = f"{text}\n\nPending card:\n{context}" if context else text
        return self._parse(prompts.CLASSIFY, content, IntentResult)

    def _parse(self, system: str, content, text_format):
        response = self._client.responses.parse(
            model=self.model_id,
            max_output_tokens=MAX_TOKENS,
            input=[{"role": "system", "content": system}, {"role": "user", "content": content}],
            text_format=text_format,
        )
        self.last_usage = Usage(response.usage.input_tokens, response.usage.output_tokens)
        for item in response.output:
            if item.type != "message":
                continue
            for part in item.content:
                if part.type == "refusal":
                    raise ModelRefused(f"model refused: {part.refusal}")
        return response.output_parsed
