#!/usr/bin/env bash
# Classify the recorded chat messages with a real model and check each intent it returns.
#
#   ./scripts/eval-intents.sh [--intent <intent,...>]
#
# --intent <intent,...>  Run only the cases that expect one of these intents, comma-separated.
#                        A prompt change can move any case, so this is a cheap check while a
#                        change is in progress, never a substitute for the full run before
#                        production. Name the intents the change adds or rewords, and the
#                        fallbacks a message lands in when nothing else matches (unknown,
#                        not_offered, how_to, explain), since a new intent takes messages from
#                        them or loses its own to them.
#
# The cases are tests/intents.json: each is a message, the card it answers (named from the
# cards eval_intents.py renders with texts.py, or null), the day it is typed on, the expected
# intent and the fields to compare; a field the case does not list is not compared. Each message
# is sent as the engine sends it: the card with its fields and button labels, then a records
# block listing the card, from the named account the case lists, or from the apartment names a
# request (no card, or a standby card) lists.
# The model comes from LLM_MODEL (provider:model, as in scripts/lib/stack-params.sh) and the
# provider's key from LLM_API_KEY, both in the environment. It needs no AWS settings and no
# recorded data. Each case is one billed classification call, about a hundred in all.
# Prints one PASS or FAIL line per case as it is classified, with what the model returned
# against what was expected, then the total; exits 1 when any case fails. A failed model call
# (no credit, rejected key, unreachable provider) stops the run with one line naming it, exit 2.
#
#   export LLM_MODEL=anthropic:claude-opus-5-5 LLM_API_KEY=<key>
#   ./scripts/eval-intents.sh
#   ./scripts/eval-intents.sh --intent resend_reports,unknown,not_offered,how_to,explain
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/app-env.sh
source "$SCRIPT_DIR/lib/app-env.sh"

INTENTS=()
case "${1:-}" in
  -h|--help) print_help; exit 0 ;;
  --intent) [ $# -eq 2 ] || usage_error "--intent takes one list of intents"; INTENTS=("$2") ;;
  "") ;;
  *) usage_error "Unknown argument: $1" ;;
esac

require_venv
exec "$APP_FOLDER/.venv/bin/python" "$SCRIPT_DIR/eval_intents.py" "$APP_FOLDER/tests/intents.json" \
  "${INTENTS[@]}"
