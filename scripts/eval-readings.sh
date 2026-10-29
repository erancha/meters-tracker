#!/usr/bin/env bash
# Score a model on every photo the deployed model failed on, against the readings users saved.
#
#   ./scripts/eval-readings.sh [env] [--limit N]
#
# env: optional suffix (e.g. dev) that targets the isolated stack meters-tracker-dev, as in
# deploy.sh.
# --limit N: stop after the first N failed photos, to size a run before paying for all of it.
#
# The model under test comes from LLM_MODEL (provider:model, as in scripts/params.example.sh)
# and the provider's key from LLM_API_KEY, both in the environment. The run reads every
# account's readings and photos in the deployed app stack, so it needs scripts/aws-config.sh.
# It calls the model once per photo it scores, and each call is billed. Each photo's line is
# printed as soon as it is scored, so an error mid-run keeps the lines already paid for; the
# totals and the count per model that failed the photo follow the last line. A photo whose
# card had no serial is scored against the reading of the next photo, which may be of another
# meter, so its "serial NO" is not always the model's error.
#
#   export LLM_MODEL=openai:gpt-6.1-sol LLM_API_KEY=<key>
#   ./scripts/eval-readings.sh dev --limit 5
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=common.sh
source "$ROOT/scripts/common.sh"

LIMIT=() ENV_ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --limit)
      [ $# -ge 2 ] || { print_help >&2; exit 2; }
      LIMIT=(--limit "$2"); shift 2 ;;
    *) ENV_ARGS+=("$1"); shift ;;
  esac
done
parse_env_suffix_arg "${ENV_ARGS[@]}"
require_aws_config
require_venv
exec "$ROOT/.venv/bin/python" "$ROOT/scripts/eval_readings.py" "$APP" "${LIMIT[@]}"
