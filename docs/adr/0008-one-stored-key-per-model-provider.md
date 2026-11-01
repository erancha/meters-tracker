# 0008. One stored key per model provider

- Status: Accepted
- Date: 2026-11-01

## Context

[ADR 0007](0007-model-provider-is-a-deploy-setting.md) kept one SSM parameter for whichever
provider `LLM_MODEL` named, so moving to the other provider overwrote the only stored key, and
moving back meant finding the old key again. The owner wants to switch quickly, for example to
run on OpenAI while the Anthropic account has no credit. A second standard parameter has no
storage cost, and each request still decrypts only one key.

## Decision

The rest of ADR 0007 stands: one deploy setting, `LLM_MODEL=provider:model` in
`scripts/params.sh`, names the model; the providers are `anthropic` and `openai`; the default is
`anthropic:claude-opus-5-5`; a single `Model` interface has one implementation per provider on
that provider's own SDK, and the model must accept image input.

Each provider has its own SSM parameter, `/meters-tracker/llm/<provider>/api-key`, written by
`scripts/set-model-key.sh <provider>`. `scripts/deploy.sh` derives the parameter from `LLM_MODEL`
and passes it to the app stack, which gives the api Lambda that name and permission to read only
that parameter. The deploy stops before any change when the parameter does not exist, naming the
command that stores the key.

## Consequences

- Switching provider is an edit to `params.sh` and a deploy; no key is overwritten.
- A key must be stored before the first deploy that names its provider.
- The Lambda package carries both SDKs.
- A new provider is a new implementation of the same interface, a factory branch and its name in
  the template's `LlmModel` pattern and `set-model-key.sh`; the engine, the flows and the fake
  model used by the suite do not change.
- The suite checks each implementation's request shape and refusal handling against a stubbed
  client; a real call on each provider is a manual check, since it needs a key.
