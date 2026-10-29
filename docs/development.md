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

## Deploy checks

- The user pool refuses a sign-up by password. With the `UserPoolClientId` output of the
  Cognito stack, `aws cognito-idp sign-up --client-id <id> --username probe@example.com
  --password 'Probe-pass-1234'` must fail with `NotAuthorizedException`.

## Scripts

- [`deploy.sh [env]`](../scripts/deploy.sh) — builds the Lambda package and deploys the Cognito
  and app stacks, then writes `frontend/public/config.js`. The callback URL and CORS origin need
  the CloudFront URL, which exists only after the first deploy, so a run deploys both stacks
  again when that URL changed. An `env` suffix such as `dev` deploys an isolated stack pair
  beside the default one. It refuses to create the app stack while tables or a files bucket of
  an earlier stack of the same name remain ([ADR 0006](adr/0006-retain-data-on-delete.md)).
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
- [`eval-readings.sh [env] [--limit N]`](../scripts/eval-readings.sh) — runs the model in
  `LLM_MODEL` over every photo recorded as failed in the named stack, or the first N, and
  prints whether it got each serial and value right as each photo is scored, then totals. It
  reads every account's readings and photos in the stack with the owner's credentials. Each
  photo scored is one billed model call.

`scripts/common.sh` holds the helpers the shell scripts source: the `--help` printer, the
settings-file checks, the `env` suffix parsing and the stack lookups.

## Configuration changes

`config/app.json` ships inside the Lambda package, so an edit reaches the deployed app only
through `deploy.sh`. The values and what each controls are listed in
[Domain rules](domain-rules.md#values).

## Logs

The api Lambda logs JSON, and the runtime keeps only its WARN lines and above. To see its
per-request lines, raise `ApplicationLogLevel` in `scripts/template.yaml` to `INFO` and deploy.
