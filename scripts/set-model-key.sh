#!/usr/bin/env bash
# Store a model provider's API key in that provider's SSM SecureString parameter.
#
#   ./scripts/set-model-key.sh <anthropic|openai>
#
# Prompts for the key without echoing (or reads it from stdin when piped), then overwrites
# /meters-tracker/llm/<provider>/api-key. Each provider keeps its own key, so both can stay stored
# and deploy.sh passes the API Lambda the one LLM_MODEL names in scripts/params.sh. The Lambda
# reads the parameter on each turn, so replacing a key needs no redeploy. The parameters are shared
# by every stack pair, since they are account-wide.
# Requires scripts/aws-config.sh.
#
#   ./scripts/set-model-key.sh openai < key.txt
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=common.sh
source "$ROOT/scripts/common.sh"

case "${1:-}" in
  -h|--help) print_help; exit 0 ;;
  anthropic|openai) ;;
  *) print_help >&2; exit 2 ;;
esac
[ $# -eq 1 ] || { print_help >&2; exit 2; }

require_aws_config
PARAM=$(model_key_param "$1")

# A last line without a newline still fills KEY while read returns failure.
read -rs -p "Model API key: " KEY || [ -n "$KEY" ]
echo >&2
KEY="$(printf '%s' "$KEY" | tr -d '\r' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
[ -n "$KEY" ] || { echo "no key entered; nothing stored" >&2; exit 1; }
# The key travels on stdin, so it never appears in the process arguments.
printf '%s' "$KEY" | aws ssm put-parameter --name "$PARAM" --type SecureString --overwrite \
  --value file:///dev/stdin >/dev/null
echo "SSM $PARAM updated"
