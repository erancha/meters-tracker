"""Typed interface to the vision/language model: reading meter photos and previous statements,
and classifying chat text.

Core pieces:
- MeterReadingResult / StatementResult / IntentResult (with its Operation list): pydantic schemas
  the model's answers are parsed into
- RecordsQuery: the find_records tool's input, the records a typed message names
- Model: the Protocol the rest of the app depends on (tests use a fake)
- ClaudeModel / OpenAIModel: one implementation per provider, each asking its API for answers
  shaped to the schema through the provider's own SDK; a classification may first answer up to
  MAX_LOOKUPS find_records calls
- parse_model_spec / build_model: check a "provider:model" setting, then pick the
  implementation it names
- model_from_env: build_model over LLM_MODEL and LLM_API_KEY, for the local scripts
- api_key_from_ssm: loads the API key from an SSM SecureString parameter
- Usage: the billed tokens of one call, which each client keeps for its last call as last_usage
- PRICE_PER_MILLION / call_cost: the listed prices per million tokens, for input, output, cache
  reads and cache writes, and the dollars one call costs at them
- _BilledModel: the per-call bookkeeping both clients share, last_usage and one log line with
  the call's kind, tokens, seconds and cost
"""

import base64
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Literal, Protocol

import anthropic
import boto3
import openai
from pydantic import BaseModel, Field

from common import prompts
from common.log import get_logger

logger = get_logger(__name__)

PROVIDERS = ("anthropic", "openai")
MAX_TOKENS = 2000
# A hung attempt fails in seconds rather than holding the turn until the api Lambda is killed;
# a photo's read and its note's classification share that Lambda's time limit.
REQUEST_TIMEOUT_SECONDS = 25
MAX_RETRIES = 1
# The find_records rounds one classification may take; the request after the last one turns the
# tool off, so a model that keeps asking still answers.
MAX_LOOKUPS = 3
FIND_RECORDS_TOOL = "find_records"
# Dollars per million input, output, cache-read and cache-write tokens, from the providers' price
# pages on 2026-11-06. A model missing here cannot be built, so every logged call carries its cost.
# Anthropic prices a cache write, for an entry that lives five minutes, at 1.25 times the input
# price; OpenAI charges nothing to write.
PRICE_PER_MILLION = {
    "claude-opus-5-5": (4.0, 20.0, 0.2, 5.0),
    "gpt-6.1-sol": (2.0, 10.0, 0.1, 0.0),
    "gpt-6-astra": (10.0, 50.0, 1.0, 0.0),
}


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
    serial_uncertain: bool
    candidates: list[Candidate]
    handwritten_label: str | None


class StatementResult(BaseModel):
    """The tariffs read from a photo of a previous statement. Prices are per kWh and per m3 in
    shekels."""

    is_statement: bool
    electricity_per_kwh: float | None
    water_per_m3: float | None
    includes_vat: bool


class Operation(BaseModel):
    """One change the model asks for on an id it was given. The target is `|`-separated because
    sort keys hold colons; value is text, typed later by the field's parser, or None on a delete and
    on a set that clears a field. A set with no field is a bare input, which the pending card's
    default field takes."""

    action: Literal["set", "delete"]
    target: str
    field: str | None
    value: str | None


class RecordsQuery(BaseModel):
    """The records a typed message names, as the classifier asks for them through the
    find_records tool. Every field is required, null or false when the message does not name it,
    so the schema can be a strict tool's."""

    apartment: str | None
    meter_type: Literal["water", "electricity"] | None
    reading_date: str | None
    tariffs: bool
    delegates: bool


class IntentResult(BaseModel):
    intent: Literal[
        "confirm",
        "reject",
        "choose_option",
        "close_period",
        "vacate",
        "change_period",
        "set_tariffs",
        "resend_reports",
        "add_delegate",
        "list_delegates",
        "remove_delegate",
        "explain",
        "edit",
        "how_to",
        "which",
        "not_offered",
        "unknown",
    ]
    date: str | None = None
    name: str | None = None
    months: int | None = None
    electricity_price: float | None = None
    water_price: float | None = None
    # The e-mail address an add_delegate or remove_delegate request names
    address: str | None = None
    operations: list[Operation] = Field(default_factory=list)


class ModelRefused(Exception):
    """The model declined to answer."""


# What a provider SDK raises when a call fails (unreachable, rejected key, no credit), each with
# the provider's message.
PROVIDER_ERRORS = (anthropic.APIError, openai.APIError)


@dataclass(frozen=True)
class Usage:
    """Billed tokens of one call; reasoning tokens count as output. input_tokens are the ones
    billed at the full input price, so they leave out the tokens read from or written to the
    prompt cache."""

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int


def call_cost(model_id: str, usage: Usage) -> float:
    """Dollars for one call at the model's listed prices; a model missing from PRICE_PER_MILLION
    raises KeyError."""
    per_input, per_output, per_read, per_write = PRICE_PER_MILLION[model_id]
    dollars = (
        usage.input_tokens * per_input
        + usage.output_tokens * per_output
        + usage.cache_read_tokens * per_read
        + usage.cache_write_tokens * per_write
    )
    return dollars / 1_000_000


class Model(Protocol):
    """What the app needs from a model: read one meter photo, read one photo as a previous
    statement, classify one chat message given the text describing the pending card, the waiting
    cards and dropped photos (empty when there is none), and find, which answers a find_records
    call with the record lines it asks for."""

    def read_meter(self, image: bytes, media_type: str) -> MeterReadingResult: ...

    def read_statement(self, image: bytes, media_type: str) -> StatementResult: ...

    def classify(
        self, text: str, context: str, today: date, find: Callable[["RecordsQuery"], str]
    ) -> IntentResult: ...


def _query_schema() -> dict:
    """RecordsQuery as a strict tool's parameters: every field required, no other field."""
    return {**RecordsQuery.model_json_schema(), "additionalProperties": False}


def _classify_content(text: str, context: str, today: date) -> str:
    """The message to classify, after today's date, which resolves dates written without a year,
    and before the pending card and the account's records, when there are any."""
    message = f"Today: {today.isoformat()}\n\n{text}"
    return f"{message}\n\nContext:\n{context}" if context else message


def api_key_from_ssm(param_name: str) -> str:
    ssm = boto3.client("ssm")
    return ssm.get_parameter(Name=param_name, WithDecryption=True)["Parameter"]["Value"]


def parse_model_spec(spec: str) -> tuple[str, str]:
    """The provider and model id of a "provider:model" setting, such as
    anthropic:claude-opus-5-5 or openai:gpt-6.1-sol; a setting the app cannot serve or price
    raises ValueError naming the fix."""
    provider, separator, model_id = spec.partition(":")
    if not separator or not model_id:
        raise ValueError(f"LLM_MODEL={spec!r} must be provider:model")
    if provider not in PROVIDERS:
        raise ValueError(f"LLM_MODEL provider {provider!r} is not one of {', '.join(PROVIDERS)}")
    if model_id not in PRICE_PER_MILLION:
        raise ValueError(
            f"LLM_MODEL model {model_id!r} has no listed price; add its dollars per million "
            "tokens to PRICE_PER_MILLION in src/common/model.py"
        )
    return provider, model_id


def build_model(spec: str, api_key: str) -> Model:
    """Builds the model a "provider:model" setting names, after parse_model_spec accepts it."""
    provider, model_id = parse_model_spec(spec)
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


class _BilledModel:
    """Per-call bookkeeping both clients share: each answered call, refused ones included, is
    kept as last_usage and logged as one JSON line with "event": "model_call", the kind of call,
    the provider:model value, its tokens, seconds and dollars. A call the provider fails is not
    billed and leaves no line. The model id is one listed in PRICE_PER_MILLION: a setting naming
    any other is refused before a client is built."""

    PROVIDER: str

    def __init__(self, model_id: str):
        self.model_id = model_id
        self.last_usage: Usage | None = None

    def _billed(self, kind: str, started: float, usage: Usage) -> None:
        self.last_usage = usage
        line = {
            "event": "model_call",
            "kind": kind,
            "model": f"{self.PROVIDER}:{self.model_id}",
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_read_tokens": usage.cache_read_tokens,
            "cache_write_tokens": usage.cache_write_tokens,
            "seconds": round(time.perf_counter() - started, 2),
            "cost": call_cost(self.model_id, usage),
        }
        logger.info(json.dumps(line))


class ClaudeModel(_BilledModel, Model):
    """Model backed by the Anthropic API: one request per photo read, and per classification one
    more per round of find_records calls; the answer parsed into the result schema, a refusal
    raised as ModelRefused."""

    PROVIDER = "anthropic"

    def __init__(self, api_key: str, model_id: str):
        super().__init__(model_id)
        self._client = anthropic.Anthropic(
            api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS, max_retries=MAX_RETRIES
        )

    def read_meter(self, image: bytes, media_type: str) -> MeterReadingResult:
        content = self._image_content(image, media_type, "Read this meter.")
        return self._parse("read_meter", prompts.READ_METER, content, MeterReadingResult)

    def read_statement(self, image: bytes, media_type: str) -> StatementResult:
        content = self._image_content(image, media_type, "Read this statement.")
        return self._parse("read_statement", prompts.READ_STATEMENT, content, StatementResult)

    @staticmethod
    def _image_content(image: bytes, media_type: str, instruction: str) -> list:
        return [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64.standard_b64encode(image).decode(),
                },
            },
            {"type": "text", "text": instruction},
        ]

    def classify(self, text, context, today, find) -> IntentResult:
        tool = {
            "name": FIND_RECORDS_TOOL,
            "description": prompts.FIND_RECORDS,
            "input_schema": _query_schema(),
            "strict": True,
        }
        messages = [{"role": "user", "content": _classify_content(text, context, today)}]
        for lookups in range(MAX_LOOKUPS + 1):
            choice = {"type": "auto"} if lookups < MAX_LOOKUPS else {"type": "none"}
            response = self._request(
                "classify",
                prompts.CLASSIFY,
                messages,
                IntentResult,
                tools=[tool],
                tool_choice=choice,
            )
            if response.stop_reason != "tool_use":
                return response.parsed_output
            results = [
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": find(RecordsQuery.model_validate(block.input)),
                }
                for block in response.content
                if block.type == "tool_use"
            ]
            messages = [
                *messages,
                {"role": "assistant", "content": response.content},
                {"role": "user", "content": results},
            ]
        raise RuntimeError("the model asked for a lookup with the tool turned off")

    def _parse(self, kind: str, system: str, content, output_format):
        messages = [{"role": "user", "content": content}]
        return self._request(kind, system, messages, output_format).parsed_output

    # The installed SDK's messages.parse takes no fallbacks/betas; see the claude-api skill,
    # section "Refusal Fallbacks", when upgrading.
    def _request(self, kind: str, system: str, messages: list, output_format, **tools):
        """One billed request; a refusal raises ModelRefused."""
        started = time.perf_counter()
        response = self._client.messages.parse(
            model=self.model_id,
            max_tokens=MAX_TOKENS,
            # The instructions and tools of each kind of call are fixed text, the same from one
            # call to the next, so the API caches them and later calls pay the cache-read price.
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=messages,
            output_format=output_format,
            **tools,
        )
        usage = response.usage
        self._billed(
            kind,
            started,
            Usage(
                usage.input_tokens,
                usage.output_tokens,
                usage.cache_read_input_tokens,
                usage.cache_creation_input_tokens,
            ),
        )
        if response.stop_reason == "refusal":
            raise ModelRefused(f"model refused: {response.stop_details}")
        return response


class OpenAIModel(_BilledModel, Model):
    """Model backed by the OpenAI Responses API: one request per photo read, and per
    classification one more per round of find_records calls; the answer parsed into the result
    schema, a refusal raised as ModelRefused."""

    PROVIDER = "openai"

    def __init__(self, api_key: str, model_id: str):
        super().__init__(model_id)
        self._client = openai.OpenAI(
            api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS, max_retries=MAX_RETRIES
        )

    def read_meter(self, image: bytes, media_type: str) -> MeterReadingResult:
        content = self._image_content(image, media_type, "Read this meter.")
        return self._parse("read_meter", prompts.READ_METER, content, MeterReadingResult)

    def read_statement(self, image: bytes, media_type: str) -> StatementResult:
        content = self._image_content(image, media_type, "Read this statement.")
        return self._parse("read_statement", prompts.READ_STATEMENT, content, StatementResult)

    @staticmethod
    def _image_content(image: bytes, media_type: str, instruction: str) -> list:
        data = base64.standard_b64encode(image).decode()
        return [
            {"type": "input_image", "image_url": f"data:{media_type};base64,{data}"},
            {"type": "input_text", "text": instruction},
        ]

    def classify(self, text, context, today, find) -> IntentResult:
        tool = {
            "type": "function",
            "name": FIND_RECORDS_TOOL,
            "description": prompts.FIND_RECORDS,
            "parameters": _query_schema(),
            "strict": True,
        }
        items = [
            {"role": "system", "content": prompts.CLASSIFY},
            {"role": "user", "content": _classify_content(text, context, today)},
        ]
        for lookups in range(MAX_LOOKUPS + 1):
            choice = "auto" if lookups < MAX_LOOKUPS else "none"
            response = self._request(
                "classify", items, IntentResult, tools=[tool], tool_choice=choice
            )
            calls = [item for item in response.output if item.type == "function_call"]
            if not calls:
                return response.output_parsed
            outputs = [
                {
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": find(RecordsQuery.model_validate_json(call.arguments)),
                }
                for call in calls
            ]
            items = [*items, *response.output, *outputs]
        raise RuntimeError("the model asked for a lookup with the tool turned off")

    def _parse(self, kind: str, system: str, content, text_format):
        items = [{"role": "system", "content": system}, {"role": "user", "content": content}]
        return self._request(kind, items, text_format).output_parsed

    def _request(self, kind: str, items: list, text_format, **tools):
        """One billed request; a refusal raises ModelRefused."""
        started = time.perf_counter()
        response = self._client.responses.parse(
            model=self.model_id,
            max_output_tokens=MAX_TOKENS,
            input=items,
            text_format=text_format,
            **tools,
        )
        usage = response.usage
        # OpenAI caches a long prompt on its own, counts the tokens the cache served inside
        # input_tokens, and charges nothing to write them.
        cached = usage.input_tokens_details.cached_tokens
        self._billed(
            kind, started, Usage(usage.input_tokens - cached, usage.output_tokens, cached, 0)
        )
        for item in response.output:
            if item.type != "message":
                continue
            for part in item.content:
                if part.type == "refusal":
                    raise ModelRefused(f"model refused: {part.refusal}")
        return response
