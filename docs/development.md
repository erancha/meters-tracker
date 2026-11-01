# Development and deployment

This document details the local setup, the tests and the operations scripts behind the
[README](../README.md)'s Getting started and Scripts sections; [Architecture](architecture.md)
describes what the scripts deploy. Each script's header comment is its full documentation, and
`--help` prints it.

## Working copy location

Keep the checkout on the WSL ext4 filesystem (`~/projects/python/meters-tracker`), not under
`/mnt/c`. WSL reaches Windows drives through a file-sharing protocol that turns every file open
into a round trip to Windows, and the development loop is made of the operations that open the
most files: interpreter start-up, imports from the virtualenv, test collection. `.venv` records
absolute paths, so each checkout builds its own.

## Tests

### Automated: the quality gate

[`scripts/test.sh`](../scripts/test.sh) is the whole quality gate: a bash syntax check of every
script, `ruff check` and `ruff format --check` over `src/`, `tests/` and `scripts/*.py`, then
pytest. It creates `.venv` on its first run, installs `requirements-dev.txt` into it on every
run, and passes its arguments to pytest (`./scripts/test.sh -q tests/test_api.py`).

The suite never touches AWS or the model. moto stands in for DynamoDB and S3, a hand-written
fake in `tests/conftest.py` records the SES calls, and `tests/fake_model.py` returns canned
photo readings and intents. `tests/engine_kit.py` builds an engine over the moto store with a
clock the test sets, and seeds properties, apartments, periods, meters and readings.
`tests/test_template.py` checks the CloudFormation templates against the tables and settings
the code expects, and `tests/test_scripts.py` runs the stack lookups of `scripts/common.sh`
against a fake `aws` command.

`tests/test_owner_story.py` is the expectation for the owner's whole story, as verified in the
browser: linking a meter, closing its period from the next reading, pricing the close, a
statement's tariffs, a water meter after a blurry photo, and a typed close or vacate. It drives
the engine as the browser does, choosing only on the card the snapshot shows and checking that
typed text is classified against that card, and asserts every card and reply on the way. The
story's names, serials, readings and prices live in `scripts/owner_story.py`.

The frontend has its own suite, run in `frontend/` with `npm test` (Vitest over jsdom, with
Testing Library); `npm run build` runs the TypeScript check before the Vite build. The GitHub
Actions workflow `.github/workflows/ci.yml` runs `scripts/test.sh` on Python 3.13 and, in
parallel, `npm ci`, `npm test` and `npm run build` on Node 22.

### By hand only: checks with the real model

The automated suite proves what the code does once the model has answered; it cannot prove how
the real model answers. Three scripts check that. They call the model named by `LLM_MODEL`
with the key in `LLM_API_KEY`, every call is billed, and they are never run by `test.sh`, CI,
or any other automation: the owner runs them by hand, when a prompt or the model changes or
after a deploy. Each script's header documents it in full.

- [`eval-intents.sh`](../scripts/eval-intents.sh) — classifies the recorded messages in
  `tests/intents.json` (a message, the card it answers or the account's apartment names, the day
  it is typed on, and the expected intent and fields) and reports each case that differs. It
  needs no AWS settings and no recorded data. A message the deployed app misunderstood becomes a
  new case.
- [`eval-readings.sh`](../scripts/eval-readings.sh) `[env] [--limit N]` — scores a model on the
  photos the deployed model failed on (the failed-photos record of
  [Domain rules](domain-rules.md)), against the serial and value the owner confirmed. It scans
  every account's readings in the stack, with the owner's own AWS credentials, so it needs
  real use of the deployed app first: until readings carry failed photos it has nothing to
  score. A failed photo with no serial is scored against the next photo's reading, which may be
  of another meter, so its "serial NO" is not always the model's error.
- [`replay.sh`](../scripts/replay.sh) `[env] <email> --open … --close … [--statement …] --yes`
  — walks the owner's story through a deployed api Lambda with the real model, as a smoke test
  after a deploy: it resets the account first (as `reset-account.sh --yes` does), then
  invokes the Lambda with the JWT claims the sign-in would give the address. Five billed calls,
  three more with `--statement`. The photos are real meter photos, kept outside the repo.

[`read-meter.sh`](../scripts/read-meter.sh) reads one photo through the model and prints the
result, its time and its estimated cost; it is the quick check of a single photo.

## Setup for deployment

1. Install the AWS CLI. The scripts add `~/.local/bin` to `PATH` themselves, since a
   non-interactive shell does not have it.
2. Run [`scripts/init-env.sh`](../scripts/init-env.sh). It copies `aws-config.example.sh` and
   `params.example.sh` to their gitignored siblings, readable only by their owner, and refuses
   to overwrite either without `--force`. Fill in the AWS access key pair and region in
   `scripts/aws-config.sh`, and the Google OAuth client id and secret and the model
   (`LLM_MODEL`, as `provider:model`) in `scripts/params.sh`.
3. Run [`scripts/deploy.sh`](../scripts/deploy.sh), then
   [`scripts/set-model-key.sh`](../scripts/set-model-key.sh) once to store the key of the
   provider `LLM_MODEL` names.

A bare `aws` command in your own shell has no credentials until it sources
`scripts/aws-config.sh`; the scripts source it for you.

## Frontend development

Copy `frontend/config.example.js` to `frontend/public/config.js` and fill in the values from
the stack outputs (or run `deploy.sh`, which generates the file), then run `npm ci` and
`npm run dev` in `frontend/`. The dev server listens on port 8000, the callback address the
Cognito stack allows, and serves `config/app.json` at `/app.json`. The deployed API accepts
`http://localhost:8000` as a CORS origin.

## Deploy checks

- The user pool refuses a sign-up by password. With the `UserPoolClientId` output of the
  Cognito stack, `aws cognito-idp sign-up --client-id <id> --username probe@example.com
  --password 'Probe-pass-1234'` must fail with `NotAuthorizedException`.

## Scripts

- [`deploy.sh [env]`](../scripts/deploy.sh) — builds the Lambda package and deploys the Cognito
  and app stacks, writes `frontend/public/config.js`, then runs `sync-frontend.sh`. The callback
  URL and CORS origin need the CloudFront URL, which exists only after the first deploy, so a
  run deploys both stacks again when that URL changed. An `env` suffix such as `dev` deploys an
  isolated stack pair beside the default one. It refuses to create the app stack while tables
  or a files bucket of an earlier stack of the same name remain
  ([ADR 0006](adr/0006-retain-data-on-delete.md)).
- [`sync-frontend.sh [env]`](../scripts/sync-frontend.sh) — builds the frontend and publishes it
  to the frontend bucket with `app.json` and `docs/demo/demo.html`, then invalidates the
  CloudFront cache. Run by `deploy.sh`; run it alone after a frontend-only change. It refuses to
  publish a `config.js` that targets another stack's API.
- [`delete-stack.sh [env]`](../scripts/delete-stack.sh) — empties the frontend bucket and
  deletes both stacks, leaving the tables and the files bucket in place. Its header explains how
  to import them into a new stack or remove them for good.
- [`set-model-key.sh`](../scripts/set-model-key.sh) — reads the model provider's API key
  without echoing it (or from stdin) and overwrites `/meters-tracker/llm/api-key` in SSM. The key
  never appears in process arguments. The parameter is shared by every stack pair in the
  account, and the Lambda reads it at run time, so a new key needs no deploy.
- [`aws-helper.sh`](../scripts/aws-helper.sh) — finds and tails the deployed functions'
  CloudWatch logs by alias (`api`, `presignup`) or `<stack>:<logical-id>`.
  `./scripts/aws-helper.sh --logs api --errors --since 1d` is the quickest health check.
- [`test.sh`](../scripts/test.sh) — the quality gate, above.
- [`init-env.sh`](../scripts/init-env.sh) — creates the two settings files, above.
- [`read-meter.sh <image>`](../scripts/read-meter.sh) — sends one photo through the real model
  with the app's prompt and prints the parsed reading as JSON; the model comes from `LLM_MODEL`
  and the provider's key from `LLM_API_KEY` in the environment. Use it to try a prompt change
  or another model on a real photo; the suite exercises each provider's implementation against
  a stubbed client only, so a real call is a manual check. The logic is in `read_meter.py`
  beside it.
- [`reset-account.sh [env] <email> [--yes]`](../scripts/reset-account.sh) — deletes everything
  the account owns in the named stack: the rows keyed by its address, or by its address and a
  `#`, in every app table, and every version of its photos under `<email>/` in the files bucket.
  Without `--yes` it prints the count per table and of photo versions and deletes nothing. Use
  it to replay a story from an empty account. A delegate's own rows (its access row, pending
  items and messages) are keyed by the delegate's address and stay, so a delegate keeps access
  to the emptied account. The logic is in `reset_account.py` beside it;
  `app_stack.py` resolves a stack's tables, bucket and api function for the scripts that work
  on a deployed stack.

`scripts/common.sh` holds the helpers the shell scripts source: the `--help` printer, the
settings-file checks, the `env` suffix parsing and the stack lookups.

## Configuration changes

`config/app.json` ships inside the Lambda package, so an edit reaches the deployed app only
through `deploy.sh`. The values and what each controls are listed in
[Domain rules](domain-rules.md#values).

## Logs

The api Lambda logs JSON, and the runtime keeps only its WARN lines and above. To see its
per-request lines, raise `ApplicationLogLevel` in `scripts/template.yaml` to `INFO` and deploy.
