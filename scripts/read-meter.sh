#!/usr/bin/env bash
# Send one meter photo through the real model and print the reading it returns as JSON.
#
#   ./scripts/read-meter.sh <image>
#
# The image is a JPEG or PNG. The model comes from LLM_MODEL (provider:model, as in
# scripts/params.example.sh) and the provider's key from LLM_API_KEY, both in the environment;
# the script never takes the key as an argument. Creates .venv on the first run, as test.sh does.
#
#   export LLM_MODEL=anthropic:claude-opus-5-5 LLM_API_KEY=<key>
#   ./scripts/read-meter.sh ~/photos/water-apt3.jpg
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=common.sh
source "$ROOT/scripts/common.sh"

case "${1:-}" in
  -h|--help) print_help; exit 0 ;;
  "") print_help >&2; exit 2 ;;
esac

require_venv
exec "$ROOT/.venv/bin/python" "$ROOT/scripts/read_meter.py" "$@"
