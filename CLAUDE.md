# CLAUDE.md

Hebrew chat backend for billing apartments by water and electricity meter readings: Python
Lambdas (`src/handlers/` over a shared `src/common/` core), DynamoDB, S3, Cognito and Claude,
deployed by CloudFormation. Layout and parts: docs/architecture.md.

## Scripts (scripts/)

- `test.sh [--matrix [slice ...] | --matrix-records | pytest args]` — bash syntax check, ruff,
  pytest; `--matrix` runs the full correction matrix, `--matrix-records` its record slices.
- `init-env.sh [--force]` — creates `lib/aws-config.sh`, `lib/secrets.sh` and `lib/params.sh`
  from their examples.
- `deploy.sh [--frontend | --frontend-only]` — builds `build/lambda.zip`, deploys the Cognito
  and app stacks and writes `frontend/public/config.js`; `--frontend` then builds `frontend/`
  and publishes it, `config/app.json` and `docs/demo/demo.html` to the frontend bucket; a
  frontend-only change needs only `--frontend-only`.
- `aws-helper.sh <option>` — operates the deployed stack pair; the same file in every AWS
  repo, with this repo's options in `lib/aws-helper-app.sh`. `--logs <function>` tails one
  function (`api`, `presignup`); `--errors --since 1d` is the health check, over every function;
  `--costs`, `--ses-status | --ses-verify | --ses-unverify`;
  `--set-model-key <anthropic|openai>` writes that provider's key to SSM
  `/meters-tracker/llm/<provider>/api-key`; `--delete-stack --empty` empties the frontend
  bucket and deletes both stacks, with the tables and the files bucket retained.
- `lib/` — sourced, never run: `app-env.sh` (shared helpers, the app name and region),
  `stack-params.sh` (every stack parameter; `LLM_MODEL` is set here), `aws-helper-app.sh`,
  `list-resources.sh`, and the gitignored `aws-config.sh` (credentials), `secrets.sh` (the
  Google OAuth client) and `params.sh` (private settings that are not secrets: `SES_SENDER`,
  `ADMIN_EMAIL`, `ALLOWED_EMAILS`).
- `read-meter.sh <image>` — one photo through the real model; needs `LLM_MODEL` and
  `LLM_API_KEY`.
- `reset-account.sh <email> [--yes]` — deletes the account's rows and photos in the stack;
  without `--yes` it only prints the counts.
- `backup-restore.sh backup [folder] | restore <folder> | --cron [dir]` — copies the
  stack's rows and files to `backups/` and back; a restore asks for the stack name.

## Gotchas

- No script takes an environment argument: the git branch selects the stack pair, `main` →
  `meters-tracker` + `meters-tracker-cognito`, any other branch → `meters-tracker-fb` +
  `meters-tracker-fb-cognito`; an exported `STACK_NAME` wins.
- `.githooks/pre-commit` refuses a commit that stages `aws-config.sh` or `secrets.sh` of
  `scripts/lib/` or a value one holds; the scripts enable it (`core.hooksPath`) the first time
  they load one. The values of `params.sh` are not searched for: the app publishes some.
- The aws CLI lives in `~/.local/bin`, which a non-interactive shell lacks. A bare `aws` call
  failing with NoCredentials means the shell did not source `scripts/lib/aws-config.sh`.
- Create the settings files of `scripts/lib/` with `init-env.sh`, never by copying by hand: it
  makes them readable only by their owner.
- `config/app.json` ships inside the Lambda package, so an edit needs `deploy.sh`; the browser
  reads a second copy that `deploy.sh --frontend` publishes.
- Frontend work: `npm test` and `npm run build` in `frontend/`; `npm run dev` needs
  `frontend/public/config.js` (see `config.example.js`) and runs on port 8000.
- Every table and the files bucket are retained on stack delete and deletion-protected.
  `deploy.sh` refuses to create a stack while an earlier one's tables or bucket remain; recover
  them by resource import (`aws-helper.sh --help`), never by deleting them to get past it.
- Hebrew strings live only in `src/common/texts.py`; prompts in `src/common/prompts.py`.
