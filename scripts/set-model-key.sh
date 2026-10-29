#!/usr/bin/env bash
# Store the model provider's API key in the SSM SecureString parameter the API Lambda reads.
#
#   ./scripts/set-model-key.sh
#
# The key belongs to the provider LLM_MODEL names in scripts/params.sh, Anthropic or OpenAI.
# Prompts for it without echoing (or reads it from stdin when piped), then overwrites
# /meters-tracker/llm/api-key. The Lambda reads the parameter on each turn, so replacing the
# value needs no redeploy. The parameter is shared by every stack pair, since it is account-wide.
# Requires scripts/aws-config.sh.
#
#   ./scripts/set-model-key.sh < key.txt
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=common.sh
source "$ROOT/scripts/common.sh"

case "${1:-}" in -h|--help) print_help; exit 0 ;; "") ;; *) print_help >&2; exit 2 ;; esac

require_aws_config
PARAM=/meters-tracker/llm/api-key

# A last line without a newline still fills KEY while read returns failure.
read -rs -p "Model API key: " KEY || [ -n "$KEY" ]
echo >&2
KEY="$(printf '%s' "$KEY" | tr -d '\r' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
[ -n "$KEY" ] || { echo "no key entered; nothing stored" >&2; exit 1; }
# The key travels on stdin, so it never appears in the process arguments.
printf '%s' "$KEY" | aws ssm put-parameter --name "$PARAM" --type SecureString --overwrite \
  --value file:///dev/stdin >/dev/null
echo "SSM $PARAM updated"
