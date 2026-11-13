# Design patterns

The same few shapes recur throughout the code: one gateway per store, one dispatch behind the
entry point, every dependency handed to the engine rather than reached for, and the code, never the
model, deciding what is written and how it is worded. [Architecture](architecture.md) describes
where the pieces run; this describes how they are put together inside.

**Contents:** [Data access behind one gateway](#data-access-behind-one-gateway) ·
[One entry point, one dispatch](#one-entry-point-one-dispatch) ·
[The engine is handed its dependencies](#the-engine-is-handed-its-dependencies) ·
[Every flow is a stored state machine](#every-flow-is-a-stored-state-machine) ·
[Changes are planned on a copy](#changes-are-planned-on-a-copy) ·
[One table describes every editable field](#one-table-describes-every-editable-field) ·
[The model reads, the code decides](#the-model-reads-the-code-decides) ·
[The frontend is handed what it uses](#the-frontend-is-handed-what-it-uses) ·
[Tests supply their own doubles](#tests-supply-their-own-doubles) ·
[Failure surfaces where it happens](#failure-surfaces-where-it-happens)

## Data access behind one gateway

`Store` (`src/common/store.py`) is the only code that touches the nine tables, and `Files`
(`src/common/files.py`) the only code that touches the files bucket. Numbers cross the store's
boundary as `float` and `int` in app code and as `Decimal` inside DynamoDB, and the conversion
lives there and nowhere else. One method family per table builds the keys a record is stored
under, so no caller assembles a `pk` or `sk` itself.

Every query is read across all its pages through `paging.query_all`, except the message history,
which asks for its latest 200 messages in one query: stopping at the first page would silently
shorten a user's own records, which reads as content that was deleted. Every read, on every table,
is strongly consistent, because a flow reads back within one turn what it has just written, such
as the reading a summary reports or the flags a re-close moved.

## One entry point, one dispatch

`handler` in `src/handlers/api.py` maps the API Gateway `routeKey` to a private function and
raises on anything unmapped: an unknown route is a mismatch between the template and the code, so
it fails loudly rather than returning an empty success. A request the handler refuses raises
`_Rejected` carrying its response, and `webapi.response` is the one JSON envelope every route
returns. What the handler keeps is only the work an HTTP request brings: reading the JWT claims,
validating the body and a photo before anything is stored, and turning the engine's outcome into
a status code.

## The engine is handed its dependencies

`Engine` (`src/common/engine.py`) takes its store, model, files, mailer, configuration, time limit
and model name as constructor arguments, and "today" as an injected clock (`dates.today` by
default). The handler builds it per invocation from the environment; the model is wrapped so it
is built on first use, and a request that never reads a photo or classifies text never fetches
the API key. A test builds the same engine over the moto store, a fake model and a clock it sets
(`tests/engine_kit.py`), so what a turn does is a function of its inputs.

## Every flow is a stored state machine

Each conversation flow (`src/common/flows/`) is a module over one pending item, the stored state of
the card it shows, described under [The conversation
engine](architecture.md#the-conversation-engine). Every flow module offers the same interface
(`flows/__init__.py`): `form` and `default_field` for the fields a typed edit may change, `edit` and
`after_edit` for applying one, and `refresh`, which re-reads the saved records a waiting card copied
and raises `CardGone` when none can stand any more. The engine looks the module up in `FLOWS` by the
item's `kind`, so a choice, a typed edit or a refresh reaches every flow the same way; it names a
flow module only to start one.

## Changes are planned on a copy

A typed correction of saved records is planned with no I/O. `account.load` reads the account into
memory, `corrections.plan` applies the model's operations to a copy and says what changes, refuses
or warns, and `account.diff` turns the two copies into the exact rows to put and delete, which
`Store.apply` writes in one all-or-nothing transaction. The rules that forbid a result live in
`corrections.check`, in one place, whatever operation produced the copy.

## One table describes every editable field

`FIELDS` (`src/common/fields.py`) holds one entry per record and field a correction may edit: its
value kind and its label, question and how-to text from `texts.py`. Parsing a typed value, the
question card that asks for a missing one, and the correction planner all look the field up there.
The correction matrix (`tests/test_correction_matrix.py`) is generated from `FIELDS` and
`DELETES`, so a new field or delete joins the matrix with no test written by hand.

## The model reads, the code decides

`Model` (`src/common/model.py`) is the protocol the rest of the app depends on, with one
implementation per provider, `ClaudeModel` and `OpenAIModel`, chosen by `build_model` from the
`provider:model` setting. Both parse the model's answer into a pydantic schema, so a flow receives
a typed reading or intent, never free text, and a refusal raises instead of returning a guess.
Code decides every step, and every reply is rendered from `src/common/texts.py`: flows pass plain
values in and never build Hebrew themselves
([ADR 0005](adr/0005-code-led-conversation.md)). The browser app's strings live alike in
`frontend/src/texts.ts`.

## The frontend is handed what it uses

`createApi(cfg, tokens)` (`frontend/src/api.ts`) returns the `Api` interface bound to one
signed-in user's tokens, and `main.tsx` passes it down as a prop; `useChat` and the components
take it as an argument, which is what lets their tests run against a fake client. Environment
settings arrive as `window.CONFIG`, typed by `AppConfig` and written per stack pair into
`public/config.js`, so retargeting a deployment needs no rebuild. A rejected request becomes an
`ApiError` carrying the status, the server's error code and, for a stale choice, the current
snapshot. The browser renders the server's snapshot and never orders the queue itself
([ADR 0011](adr/0011-browser-app-renders-the-server-snapshot.md)).

## Tests supply their own doubles

AWS is faked with moto, so store and engine tests exercise the real key, condition and
transaction expressions. `tests/conftest.py` adds `FakeSes`, which records what was asked and
can be primed to raise; `tests/fake_model.py` answers photos by the hash of their bytes and
intents in the order a test queues them, recording every classification. The frontend's tests
build a fake `Api` (`frontend/src/testChat.tsx`).

## Failure surfaces where it happens

Missing keys, unknown ids and impossible states raise rather than resolving to a plausible
default. The failures the app declares legal are handled where they occur and stay visible: a
text or photo whose handling fails is marked `failed` on its message and answered with a 503, and
a period report that cannot be sent is logged and marked on its period row, so the next day's
first chat load sends it again.
