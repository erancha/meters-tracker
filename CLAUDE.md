# CLAUDE.md

Hebrew chat for billing apartments by water and electricity meter readings: Python Lambdas
(`src/handlers/` over a shared `src/common/` core), DynamoDB, S3, Cognito, a Claude or OpenAI
model, and a React browser app in `frontend/`, deployed by CloudFormation. Layout and parts:
docs/architecture.md. The checkout belongs on the WSL ext4 filesystem, not under `/mnt/c`;
docs/development.md explains the cost.

## Operations scripts (scripts/)

- No script takes an environment argument: the git branch selects the stack pair, `main` →
  `meters-tracker` + `meters-tracker-cognito`, any other branch → `meters-tracker-fb` +
  `meters-tracker-fb-cognito`; an exported `STACK_NAME` wins.
- Create the settings files of `scripts/lib/` with `init-env.sh`, never by copying by hand: it
  makes them readable only by their owner.
- `.githooks/pre-commit` refuses a commit that stages `aws-config.sh` or `secrets.sh` of
  `scripts/lib/` or a value one holds; the scripts enable it (`core.hooksPath`) the first time
  they load one. Of `params.sh` it refuses the email addresses, the allow-list's included, and
  no other value; `PUBLISHED_SETTINGS` of `lib/app-env.sh` names a setting whose address may
  appear in git, and none does.
- The aws CLI lives in `~/.local/bin`, which a non-interactive shell lacks. A bare `aws` call
  failing with NoCredentials means the shell did not source `scripts/lib/aws-config.sh`.
- `config/app.json` ships inside the Lambda package, so an edit needs `deploy.sh`; the browser
  reads a second copy that `deploy.sh --frontend` publishes.
- Frontend work: `npm test` and `npm run build` in `frontend/`; `npm run dev` needs
  `frontend/public/config.js` (`lib/app-config.sh` writes it, or see `config.example.js`) and
  runs on port 8000.
- Every table and the files bucket are retained on stack delete and deletion-protected.
  `deploy.sh` refuses to create a stack while an earlier one's tables or bucket remain; recover
  them by resource import (`aws-helper.sh --help`), never by deleting them to get past it.
- Hebrew strings live only in `src/common/texts.py` and, for the browser app,
  `frontend/src/texts.ts`; prompts in `src/common/prompts.py`.
- `test.sh [--matrix [slice ...] | --matrix-records | pytest args]` — bash syntax check, ruff,
  pytest; `--matrix` runs the full correction matrix, `--matrix-records` its record slices.
- `init-env.sh [--force]` — creates `lib/aws-config.sh`, `lib/secrets.sh` and `lib/params.sh`
  from their examples.
- `deploy.sh [--frontend | --frontend-only]` — builds `build/lambda.zip`, deploys the Cognito
  and app stacks and writes `frontend/public/config.js` (`lib/app-config.sh`, run again at the
  start of every frontend publish); `--frontend` then builds `frontend/` and publishes it,
  `config/app.json` and `docs/demo/demo.html` to the frontend bucket; a frontend-only change
  needs only `--frontend-only`.
- `lib/` — sourced or run by the entry points: `app-env.sh` (shared helpers, the app name and
  region), `app-config.sh` (writes `frontend/public/config.js` for the branch's stack pair),
  `stack-params.sh` (every stack parameter; `LLM_MODEL` is set here), `aws-helper-app.sh` with
  `aws_helper.py` (the `--costs` summing), `list-resources.sh`, and the gitignored
  `aws-config.sh` (credentials), `secrets.sh` (the Google OAuth client) and `params.sh` (private
  settings that are not secrets: `SES_SENDER`, `ADMIN_EMAIL`, `ALLOWED_EMAILS`).
- `aws-helper.sh <option>` — operates the deployed stack pair; the same file in every AWS
  repo, with this repo's options in `lib/aws-helper-app.sh`. `--logs <function>` tails one
  function (`api`, `presignup`); `--errors --since 1d` is the health check, over every function;
  `--costs`, `--ses-status | --ses-verify | --ses-unverify`;
  `--set-model-key <anthropic|openai>` writes that provider's key to SSM
  `/meters-tracker/llm/<provider>/api-key`; `--delete-stack --empty` empties the frontend
  bucket and deletes both stacks, with the tables and the files bucket retained.
- `tools/read-meter.sh <image>` — one photo through the real model; needs `LLM_MODEL` and
  `LLM_API_KEY`.
- `tools/eval-intents.sh [--intent <intent,...>]` — classifies `tests/intents.json` with the real
  model; billed, run by hand only.
- `tools/eval-readings.sh [--limit N]` — scores a model on the deployed stack's failed photos;
  billed.
- `tools/replay.sh <email> --open <photo> --close <photo> [--statement <photo>] [--yes]` — the
  owner's story against the deployed api Lambda; resets that account first; billed.
- `tools/reset-account.sh <email> [--yes]` — deletes the account's rows and photos in the stack;
  without `--yes` it only prints the counts.
- `tools/backup-restore.sh backup [folder] | restore <folder> | --cron [dir]` — copies the
  stack's rows and files to `backups/` and back; a restore asks for the stack name.
