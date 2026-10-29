# CLAUDE.md

Hebrew chat backend for billing apartments by water and electricity meter readings: Python
Lambdas (`src/handlers/` over a shared `src/common/` core), DynamoDB, S3, Cognito and Claude,
deployed by CloudFormation. Layout and parts: docs/architecture.md.

## Scripts (scripts/)

- `test.sh [pytest args]` — bash syntax check, ruff, pytest; creates `.venv` on first run.
- `init-env.sh [--force]` — creates `aws-config.sh` and `params.sh` from their examples.
- `deploy.sh [env]` — builds `build/lambda` and deploys the Cognito and app stacks.
- `delete-stack.sh [env]` — deletes both stacks; tables and the files bucket are retained.
- `set-model-key.sh` — writes the model provider's key to SSM `/meters-tracker/llm/api-key`.
- `aws-helper.sh` — CloudWatch logs by target; `--logs api --errors --since 1d` is the health
  check.
- `read-meter.sh <image>` — one photo through the real model; needs `LLM_MODEL` and
  `LLM_API_KEY`.
- `eval-readings.sh [env] [--limit N]` — scores `LLM_MODEL` on the recorded failed photos; each
  photo is a billed call.

## Gotchas

- The aws CLI lives in `~/.local/bin`, which a non-interactive shell lacks. A bare `aws` call
  failing with NoCredentials means the shell did not source `scripts/aws-config.sh`.
- Create `scripts/aws-config.sh` and `scripts/params.sh` with `init-env.sh`, never by copying by
  hand: it makes them readable only by their owner.
- `config/app.json` ships inside the Lambda package, so an edit needs `deploy.sh`.
- Every table and the files bucket are retained on stack delete and deletion-protected.
  `deploy.sh` refuses to create a stack while an earlier one's tables or bucket remain; recover
  them by resource import (`delete-stack.sh --help`), never by deleting them to get past it.
- Hebrew strings live only in `src/common/texts.py`; prompts in `src/common/prompts.py`.
