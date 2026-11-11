#!/usr/bin/env bash
# Score a model on every photo the deployed model failed on, against the readings users saved.
#
#   ./scripts/eval-readings.sh [--limit N]
#
# The stack is the one the git branch selects, as in deploy.sh: meters-tracker on main,
# meters-tracker-fb on any other branch.
# --limit N: stop after the first N failed photos, to size a run before paying for all of it.
#
# The model under test comes from LLM_MODEL (provider:model, as in scripts/lib/stack-params.sh)
# and the provider's key from LLM_API_KEY, both in the environment. The run reads every
# account's readings and photos in the deployed app stack, so it needs lib/aws-config.sh.
# It calls the model once per photo it scores, and each call is billed. Each photo's line is
# printed as soon as it is scored, so an error mid-run keeps the lines already paid for; the
# totals and the count per model that failed the photo follow the last line. A photo whose
# card had no serial is scored against the reading of the next photo, which may be of another
# meter, so its "serial NO" is not always the model's error.
#
#   export LLM_MODEL=openai:gpt-6.1-sol LLM_API_KEY=<key>
#   ./scripts/eval-readings.sh --limit 5
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/app-env.sh
source "$SCRIPT_DIR/lib/app-env.sh"

LIMIT=()
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) print_help; exit 0 ;;
    --limit)
      [ $# -ge 2 ] || usage_error "--limit needs a number"
      LIMIT=(--limit "$2"); shift 2 ;;
    *) usage_error "Unknown argument: $1" ;;
  esac
done
connect_stack
require_venv
exec "$APP_FOLDER/.venv/bin/python" "$SCRIPT_DIR/eval_readings.py" "$STACK_NAME" "${LIMIT[@]}"
