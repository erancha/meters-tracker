#!/usr/bin/env bash
# Send one meter photo through the real model and print the reading it returns as JSON.
#
#   ./scripts/tools/read-meter.sh <image>
#
# The image is a JPEG or PNG. The model comes from LLM_MODEL (provider:model, as in
# scripts/lib/stack-params.sh) and the provider's key from LLM_API_KEY, both in the environment;
# the script never takes the key as an argument. Creates .venv on the first run, as test.sh does.
#
#   export LLM_MODEL=anthropic:claude-opus-5-5 LLM_API_KEY=<key>
#   ./scripts/tools/read-meter.sh ~/photos/water-apt3.jpg
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib/app-env.sh
source "$SCRIPT_DIR/lib/app-env.sh"

case "${1:-}" in
  -h|--help) print_help; exit 0 ;;
  "") usage_error "No image given" ;;
  -*) usage_error "Unknown option: $1" ;;
esac
[ $# -eq 1 ] || usage_error "Takes one image"

require_venv
exec "$APP_FOLDER/.venv/bin/python" "$SCRIPT_DIR/tools/read_meter.py" "$@"
