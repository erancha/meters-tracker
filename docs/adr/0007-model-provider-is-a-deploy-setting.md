# 0007. The model provider is a deploy setting, behind one interface

- Status: Accepted
- Date: 2026-10-29

## Context

The app depends on a model for two narrow jobs, reading a meter photo and classifying one chat
message, both answered as a fixed schema ([ADR 0005](0005-code-led-conversation.md)). The first
implementation called Claude through the Anthropic SDK, with the model id fixed in code and the
key stored under an Anthropic-named parameter. The owner wants to run the same app on an OpenAI
model when price or reading quality favours it, without a code change, and the owner's other
repositories already name a model as `provider:model` in one setting next to one key.

## Decision

One deploy setting, `LLM_MODEL=provider:model` in `scripts/params.sh`, names the model; the
providers are `anthropic` and `openai`, and the default is `anthropic:claude-opus-5-5`. One
SSM parameter, `/meters-tracker/llm/api-key`, holds the key of whichever provider is named.
`src/common/model.py` keeps a single `Model` interface for the engine and the tests, with one
implementation per provider on that provider's own SDK, and a factory that builds the right one
from the setting and rejects any other value. The model must accept image input.

## Consequences

- Switching provider is an edit to `params.sh`, a deploy, and storing the other provider's key.
- The Lambda package carries both SDKs.
- A new provider is a new implementation of the same two methods plus a factory branch; the
  engine, the flows and the fake model used by the suite do not change.
- The suite checks each implementation's request shape and refusal handling against a stubbed
  client; a real call on each provider is a manual check, since it needs a key.
