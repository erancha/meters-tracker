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
[The browser app renders the server's snapshot](#the-browser-app-renders-the-servers-snapshot) ·
[Conventions the whole backend keeps](#conventions-the-whole-backend-keeps) ·
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

Each conversation flow (`src/common/flows/`) is a small state machine stored as a **pending item**:
`seq` (a timestamp-ordered key), `kind` (the flow), `status`, `step`, `version` and `data` (the
answers so far). The version grows each time the item shows a card, and every card carries it, so a
choice answers only the card the user saw. Before handling a choice the engine claims the item with
a write that holds only while the version and status are unchanged, so of a double-click or two
devices answering one card, one request proceeds and the other gets 409. The statuses are `reading`
while the model reads the photo, `awaiting` while a card waits for the user, `parked` while a close
waits for a meter's photo and shows no card, and `standby` while a request for tariffs waits without
blocking the queue: its card shows only when no card awaits, and typed text with nothing awaiting
reaches it only when it is prices, a cancel or a question; any other text is classified as a request
as usual. The **snapshot** is the first awaiting item's card, a question then a correction ahead of
the rest, else the oldest standby item's, plus the seqs of the queued items behind it; the client
renders it and never orders the queue itself. A turn that ends the shown card and moves the queue to
a card shown earlier records that card again as the turn's last reply; a shown card that an older
photo's finished read only moved behind is not ended, so no card repeats and the card the next
message answers is always the latest in the thread. A meter photo's card carries `photo`, the stored
key of the photo it answers, so several photos sent at once each show beside their own reply. Every
data write waits behind a confirmation card ([ADR 0005](adr/0005-code-led-conversation.md)). The
flows and the rules they enforce are in [Domain rules](domain-rules.md).

Every flow module offers the same interface (`flows/__init__.py`): `form` and `default_field` for
the fields a typed edit may change, `edit` and
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

The deploy setting `LLM_MODEL` names the model as `provider:model`, `anthropic:claude-opus-5-5` by
default or an OpenAI model, and each implementation calls it through that provider's own SDK
([ADR 0008](adr/0008-one-stored-key-per-model-provider.md)). Both ask for an answer shaped to a
declared schema (the SDKs call this structured output): a meter reading (type, serial, candidate
values with their display position, unreadable digits, handwritten label), a previous statement's
tariffs (whether the photo is a statement, the electricity and water prices, whether they include
VAT), or one intent from a closed list — `confirm`, `reject`, `edit`, `choose_option`,
`close_period`, `vacate`, `change_period`, `set_tariffs`, `explain`, `how_to`, `which`,
`not_offered`, `unknown`, with the operations an `edit`, a `how_to` or a `which` names. A photo is
read as a statement only when its meter read finds neither a meter type nor a serial. Each
classification is sent with today's date, the pending card's text with its fields, the field it
asked for again and its button labels, and the
waiting cards and dropped photos, but no saved records. The model looks up the records a message
names with a `find_records` tool, offered by both the Claude and the OpenAI client, so it names an
apartment or a record exactly as stored while a message that names none sends no records at all.
The Lambda answers each lookup from the account it loaded for the turn (`account.lookup`); a
classification takes at most `MAX_LOOKUPS` (3) lookups, and the request after the last turns the
tool off, so the model still answers ([Domain rules](domain-rules.md#typed-edits-on-a-card)).
The prompts are in `src/common/prompts.py`. Only the owner runs the classifier against the real
model, with `scripts/tools/eval-intents.sh` over the recorded messages in `tests/intents.json`; the
test suite uses a fake model ([Development](development.md#backend-tests)).

## The frontend is handed what it uses

`createApi(cfg, tokens)` (`frontend/src/api.ts`) returns the `Api` interface bound to one
signed-in user's tokens, and `main.tsx` passes it down as a prop; `useChat` and the components
take it as an argument, which is what lets their tests run against a fake client. Environment
settings arrive as `window.CONFIG`, typed by `AppConfig` and written per stack pair into
`public/config.js`, so retargeting a deployment needs no rebuild. A rejected request becomes an
`ApiError` carrying the status, the server's error code and, for a stale choice, the current
snapshot.

## The browser app renders the server's snapshot

`frontend/` is a React and TypeScript single-page app in Hebrew (`<html lang="he" dir="rtl">`)
that renders what the api Lambda returns and decides nothing about the conversation
([ADR 0011](adr/0011-browser-app-renders-the-server-snapshot.md)). What a user sees on each
surface, the day panel included, is in [Feature overview](features.md).

- **Sign-in** (`auth.ts`) — [Sign-in](sign-in.md#how-it-works) follows it step by step; local
  development runs on port 8000, the callback the Cognito stack allows.
- **Chat state** (`useChat.ts`, over TanStack Query) — loads `GET /chat` for the shown day, one
  query per day, and keeps the latest snapshot it received. Today's history stays loaded while an
  earlier day is shown: the server records every turn under today, so from the first turn sent on an
  earlier day the thread appends today's messages that came after it. A typed turn, a card choice or
  a photo is a `POST /turn`: the user's message is shown at once, the server's replies are appended
  and its snapshot replaces the shown one, then a refetch of `GET /chat` replaces the local entries
  with the server's history. A sent photo's stored key, from the reply, takes the page's own copy as
  its link, so it shows with no download. A stale choice (409) only replaces the snapshot. A turn
  lost to the network or a gateway timeout only refetches, since the server may have handled it. A
  503 `message_failed` only refetches too, which brings the message with its failed mark and retry
  button; a text or photo whose request never reached the server stays in the page with a resend
  while the page is open. Any other failure shows a notice and refetches. Several photos can be in
  flight together. While the snapshot shows no card, lists queued items and no turn is in flight,
  `GET /chat` is polled every 5 seconds, so a card saved after the gateway gave up still arrives.
  With a card shown the queued items include cards behind it, which change only after an answer, so
  polling stops.
- **Cards** (`CardView.tsx`) — the buttons come from the snapshot card's options, a choice
  posts the card's `pending`, `version` and the option, and `foldChoices.ts` shows a recorded
  choice in place on the card its `choice` names instead of as a bubble of its own; typed text
  never folds. `MessageList.tsx` marks the snapshot's card as live and the latest card of each
  seq the snapshot lists as queued, and shows the `photo` of those two kinds of card as a
  thumbnail.
- **Photos** (`images.ts`) — the camera button takes a photo or picks existing ones; each is
  scaled to 1600 px on its longer edge and re-encoded as JPEG, which keeps it under the API's
  4 MiB limit. A thumbnail or the full-screen viewer fetches a link from `GET /files/{key+}`.
- **Layout** — `activity.ts` derives the day panel's lists from the loaded messages, so the panel
  needs no request of its own and follows every refetch; its missing-tariffs line comes from the
  snapshot's `tariffs_missing`, true while a request for tariffs is open, even behind another card.
  Below the width in `sidebar_min_width_px` the same lists sit behind a strip under the header
  (`DayStrip.tsx`). The border between the panel and the chat sets the panel's width
  (`PanelResizer.tsx`), kept in the browser's local storage, and one full-screen photo viewer,
  owned by `App.tsx`, serves the thread.

## Conventions the whole backend keeps

The Python side, both Lambda functions and the shared core behind them, keeps three conventions:

- **Dates.** Every stored and shown date is a calendar date in Asia/Jerusalem
  (`src/common/dates.py`), and the engine takes "today" from an injected clock, so tests fix
  the day.
- **Nothing runs on a clock.** The only time-driven behavior, the daily greeting, is computed
  when the chat loads.
- **Logging.** The api Lambda logs JSON at INFO: a line per request and a line per model call
  with its tokens and cost, which `aws-helper.sh --costs` sums ([Development](development.md#logs)).

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
