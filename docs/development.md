# Development & deployment

This document details the local setup, the tests and the operations scripts behind the
[README](../README.md)'s Getting started and Scripts sections; [Architecture](architecture.md)
describes what the scripts deploy. Each script's header comment is its full documentation, and
`--help` prints it.

**Contents:** [Working copy location](#working-copy-location) · [Frontend](#frontend) ·
[Configuration](#configuration) · [Backend tests](#backend-tests) ·
[Deployment scripts (scripts/)](#deployment-scripts-scripts) · [Deploy checks](#deploy-checks) ·
[Logs](#logs)

## Working copy location

Keep the checkout on the WSL ext4 filesystem (`~/projects/serverless/meters-tracker`), not under
`/mnt/c`. WSL reaches Windows drives through a file-sharing protocol that turns every file open
into a round trip to Windows, and the development loop is made of the operations that open the
most files: interpreter start-up, imports from the virtualenv, test collection. `.venv` records
absolute paths, so each checkout builds its own.

## Frontend

Run [`scripts/lib/app-config.sh`](../scripts/lib/app-config.sh) to write
`frontend/public/config.js` for the branch's deployed stack pair (every `deploy.sh` run writes it
too), or copy `frontend/config.example.js` there and fill in the values by hand. Then run
`npm ci` and `npm run dev` in `frontend/`. The dev server listens on port 8000, the callback
address the Cognito stack allows, and serves `config/app.json` at `/app.json`. The deployed API
accepts `http://localhost:8000` as a CORS origin.

The frontend has its own suite, run in `frontend/` with `npm test` (Vitest over jsdom, with
Testing Library); `npm run build` runs the TypeScript check before the Vite build.

## Configuration

Settings reach the deployed app through different routes, so each kind of edit takes its own
step:

- `config/app.json` ships inside the Lambda package, so an edit reaches the deployed app only
  through `deploy.sh`; the browser reads a second copy, which `deploy.sh --frontend` publishes.
  The values and what each controls are listed in [Domain rules](domain-rules.md#values).
- A stack parameter (`scripts/lib/stack-params.sh`, or a setting of the gitignored
  `lib/secrets.sh` or `lib/params.sh`) takes `deploy.sh`; the admin's address also reaches the
  browser's `config.js`, which only `--frontend` publishes.
- A model provider's API key takes `aws-helper.sh --set-model-key` and no deploy: the Lambda
  reads it at run time.
- A frontend-only change takes `deploy.sh --frontend-only`.

## Backend tests

### Automated: the quality gate

[`scripts/test.sh`](../scripts/test.sh) is the whole quality gate: a bash syntax check of every
script, `ruff check` and `ruff format --check` over `src/`, `tests/` and `scripts/`, then
pytest, which leaves out the correction matrix below. It creates `.venv` on its first run, installs
`requirements-dev.txt` into it on every run, and with arguments runs pytest with them alone
(`./scripts/test.sh -q tests/test_api.py`).

The suite never touches AWS or the model. moto stands in for DynamoDB and S3, a hand-written
fake in `tests/conftest.py` records the SES calls, and `tests/fake_model.py` returns canned
photo readings and intents. `tests/engine_kit.py` builds an engine over the moto store with a
clock the test sets, and seeds properties, apartments, periods, meters and readings.
`tests/test_template.py` checks the CloudFormation templates against the tables and settings
the code expects. `tests/test_scripts.py` runs the stack lookups and settings loaders of
`scripts/lib/app-env.sh`, `init-env.sh`, `aws-helper.sh` whole and the frontend step of
`deploy.sh` and `lib/app-config.sh` against a fake `aws` command in a temporary git checkout.

`tests/test_correction_matrix.py` is the correction matrix, generated from the field table (`FIELDS`
in `src/common/fields.py`) and the table of records a correction may delete (`DELETES` in
`src/common/corrections.py`). Its cases, exactly: every operation in eight seeded account states (an
open period, a closed one with its successor, the row that closes one and opens the next, a replaced
meter, a meter, apartment and property to merge into, a vacant apartment, no tariff, a delegate)
with every answer (a valid value then confirm, cancel, or the corrected record changed before the
confirm; an invalid value; none; an unknown id; an unknown field); every waiting card kind in every
state with every operation and a valid value confirmed; the card fields with every card kind and a
valid, an invalid and no value, in one state per card; the inverse of every set in every state; and
sixteen fixed-seed sequences. Every confirmed case goes on to a later photo of each live meter and
the close, both checked to have happened, then checks that units are conserved: each stretch between
two consecutive readings of a meter is billed in exactly one period, or in none across a reset or a
vacate between them. Outcomes other than an answer's default are listed in the file with the rule
that words them. Each case runs in an account of its own over one moto store, emptied after it,
since moto copies its whole tables on every transaction. The matrix is marked `matrix`, so a plain
`pytest` and the plain `test.sh` leave it out. Its slices (`MATRIX_SLICES`) run as separate pytest
processes, as many at once as `nproc`, each under three minutes and at most about 590 MB at its
peak. `./scripts/test.sh --matrix-records` runs the quick matrix, the eight record slices
(`QUICK_SLICES`: every operation and answer in every state, with no waiting card), in about 6
minutes with three processors, before a squash to main. `./scripts/test.sh --matrix` runs the full
matrix on demand, all 60 slices, in about 33 minutes, and `./scripts/test.sh --matrix
matrix_cards_open_1` runs one slice.

`tests/test_owner_story.py` is the expectation for the owner's whole story, as verified in the
browser on 29/10/2026, with its electricity meter identified by its handwritten label: linking a
meter, closing its period from the next reading, pricing the close, a
statement's tariffs, a water meter after a blurry photo, and a typed close or vacate. It drives
the engine as the browser does, choosing only on the card the snapshot shows and checking that
typed text is classified against that card, and asserts every card and reply on the way. The
story's names, serials, readings and prices live in `scripts/tools/owner_story.py`.

The GitHub Actions workflow `.github/workflows/ci.yml` runs `scripts/test.sh` on Python 3.13
and, in parallel, `npm ci`, `npm test` and `npm run build` on Node 22.

### By hand only: checks with the real model

The automated suite proves what the code does once the model has answered; it cannot prove how
the real model answers. Three scripts check that. They call the model named by `LLM_MODEL`
with the key in `LLM_API_KEY`, every call is billed, and they are never run by `test.sh`, CI,
or any other automation: the owner runs them by hand, when a prompt or the model changes or
after a deploy. Each script's header documents it in full.

- [`eval-intents.sh`](../scripts/tools/eval-intents.sh) — classifies the recorded messages in
  `tests/intents.json` (a message, the card it answers, the account it is sent with, the day it
  is typed on, and the expected intent and fields) and reports each case that differs. Each case
  is sent as the engine sends it, the card listed beside the waiting cards and dropped photos,
  with a lookup of the account's records. It needs no AWS settings and no recorded data. A
  message the deployed app misunderstood becomes a new case.
- [`eval-readings.sh`](../scripts/tools/eval-readings.sh) `[--limit N]` — scores a model on the
  photos the deployed model failed on (the failed-photos record of
  [Domain rules](domain-rules.md)), against the serial and value the owner confirmed. It scans
  every account's readings in the stack, with the owner's own AWS credentials, so it needs
  real use of the deployed app first: until readings carry failed photos it has nothing to
  score. A failed photo with no serial is scored against the next photo's reading, which may be
  of another meter, so its "serial NO" is not always the model's error.
- [`replay.sh`](../scripts/tools/replay.sh) `<email> --open … --close … [--statement …] --yes`
  — walks the owner's story through a deployed api Lambda with the real model, as a smoke test
  after a deploy: it resets the account first (as `reset-account.sh --yes` does), then
  invokes the Lambda with the JWT claims the sign-in would give the address. Each run makes billed
  model calls, more with `--statement`. The photos are real meter photos, kept outside the repo.

[`read-meter.sh`](../scripts/tools/read-meter.sh) reads one photo through the model and prints the
result, its time and its cost at the listed prices; it is the quick check of a single photo.

## Deployment scripts (`scripts/`)

Install the AWS CLI, `jq`, `git`, `npm` and `python3`; the scripts add `~/.local/bin` to `PATH`
themselves, since a non-interactive shell does not have it. `./scripts/init-env.sh` creates the
gitignored `aws-config.sh`, `secrets.sh` and `params.sh` under `scripts/lib/` from their
`.example` files, readable only by their owner; fill them in before running any other script;
each setting's comment says what it holds and whether it is required. Write each address of
`params.sh` in full (`a\.b@host\.com|c@host\.com`), since the scripts check every address of the
file and stop at an `@` outside one. `lib/stack-params.sh` lists every parameter the stack pair
is deployed with, `LLM_MODEL` among them.

No script takes an environment argument. The git branch selects the stack pair every script
works on: `main` selects `meters-tracker` and `meters-tracker-cognito`, any other branch the
isolated pair `meters-tracker-fb` and `meters-tracker-fb-cognito`.

Sign-in, mail and the model depend on settings outside this repo, which must exist first.
`SES_SENDER` in `params.sh` must be an identity SES has verified, since every mail goes out from
it. The Google OAuth client of `secrets.sh` must list among its authorized redirect URIs the
address `./scripts/aws-helper.sh --redirect-uri` prints for the branch
([Setting up Google](sign-in.md#setting-up-google)). The key of the model provider `LLM_MODEL`
names is stored once with `./scripts/aws-helper.sh --set-model-key <provider>`; `deploy.sh` stops
with that command while it is missing.

- [`deploy.sh`](../scripts/deploy.sh) — builds the Lambda package, deploys the Cognito and app
  stacks and writes `frontend/public/config.js` with `lib/app-config.sh`. The package is
  byte-identical for an unchanged source (`zip_lambda.py` beside it), so CloudFormation leaves
  the functions alone on such a run. The callback URL and CORS origin need the CloudFront URL,
  which exists only after the first deploy, so a run deploys both stacks again when that URL
  changed. It refuses to create the app stack while tables or a files bucket of an earlier stack
  of the same name remain ([ADR 0006](adr/0006-retain-data-on-delete.md)). The stack parameters
  come from `lib/stack-params.sh` and the gitignored `lib/secrets.sh` and `lib/params.sh`.
  `--frontend` then writes `config.js` again for the branch's stack pair, builds the frontend and
  publishes it to the frontend bucket with `app.json` and `docs/demo/demo.html`, and invalidates
  the CloudFront cache; `--frontend-only` does only that, after a frontend-only change.
- [`aws-helper.sh`](../scripts/aws-helper.sh) — operates the deployed stack pair, the same
  file in every AWS repo; this repo's own options are in
  [`lib/aws-helper-app.sh`](../scripts/lib/aws-helper-app.sh), and `--help` prints both headers.
  `--functions` lists the deployed functions of both stacks and `--logs <function>` tails one,
  named by its logical id or a unique part of it (`api`, `presignup`).
  `./scripts/aws-helper.sh --errors --since 1d`, the warnings and errors of every function in
  both stacks, is the quickest health check;
  `--costs --since 1d` sums the model spend ([Logs](#logs)). `--ses-status` lists each address
  SES knows with its verification status and says whether the account is still in the SES
  sandbox; `--ses-verify <email>...` mails each address a new verification link, for a user whose
  report could not be sent; `--ses-unverify <email>...` deletes each address's SES identity, to
  see a report fail on purpose, and refuses `SES_SENDER`. `--redirect-uri` prints the address
  Google returns sign-in to ([Sign-in](sign-in.md#setting-up-google)).
  `--set-model-key <provider>` reads a model provider's API key without echoing it (or from
  stdin) and overwrites `/meters-tracker/llm/<provider>/api-key` in SSM. The key never appears
  in process arguments. Each provider keeps its own parameter, so switching `LLM_MODEL` needs
  only a deploy. The parameters are shared by every stack pair in the account, and the Lambda
  reads its key at run time, so a new key needs no deploy.
  `--delete-stack --empty` empties the frontend bucket, deletes both stacks and then the deploy's
  upload bucket, leaving the tables and the files bucket with its objects in place; without
  `--empty` it stops while the frontend bucket holds objects. The header of `lib/aws-helper-app.sh`
  explains how to import the retained resources into a new stack or remove them for good.

- [`test.sh`](../scripts/test.sh) — the quality gate, under [Backend tests](#backend-tests).
- [`tools/`](../scripts/tools/README.md) — hands-on tools: one photo through the real model, the
  real-model checks under [By hand only](#by-hand-only-checks-with-the-real-model), an account
  reset, and backups with a nightly cron entry. Each script's header documents it in full.

Beside them, `zip_lambda.py` packs the Lambda build for `deploy.sh`, and `app_stack.py` resolves
a stack's tables, bucket and api function for the tools that work on a deployed stack.

`scripts/lib/app-env.sh` holds what the scripts share: the `--help` printer, the settings-file
loaders, which refuse a file git tracks, and the branch's stack selection. A bare `aws` command
in your own shell has no credentials until it sources `scripts/lib/aws-config.sh`; the scripts
source it for you. A script that loads `aws-config.sh` or `secrets.sh` first stops if git tracks
the file or a tracked file holds one of its values, and sets `core.hooksPath` to `.githooks`,
whose `pre-commit` hook makes the same check on every commit's staged changes. Loading
`params.sh` makes the same check for its email addresses only, so the sign-up list and the
admin's address never reach git.

## Deploy checks

- The user pool refuses a sign-up by password. With the `UserPoolClientId` output of the
  Cognito stack, `aws cognito-idp sign-up --client-id <id> --username probe@example.com
  --password 'Probe-pass-1234'` must fail with `NotAuthorizedException`.

## Logs

The api Lambda logs JSON at INFO and above: one line per request, and one line per model call
with `"event": "model_call"`, the kind of call (`read_meter`, `read_statement`, `classify`), the
`provider:model` value, the input tokens billed at the full price, the tokens read from and
written to the prompt cache, the output tokens, the seconds the call took and its cost in
dollars ([Domain rules](domain-rules.md#model-call-cost)). The spend of a stack over a span:

```bash
./scripts/aws-helper.sh --costs --since 1d
```

It prints the calls, tokens (cache reads and writes in their own columns) and dollars by UTC
day, by kind of call, and in total; the summing is in `scripts/lib/aws_helper.py`.
