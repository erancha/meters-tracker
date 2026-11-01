#!/usr/bin/env bash
# Classify the recorded chat messages with a real model and check each intent it returns.
#
#   ./scripts/eval-intents.sh
#
# The cases are tests/intents.json: each is a message, the card it answers (named from the
# cards eval_intents.py renders with texts.py, or null), the day it is typed on, the expected
# intent and the fields to compare; a field the case does not list is not compared. Each card is
# sent as the engine sends it: an awaiting card with its button labels, a request (no card, or
# a standby card) with the apartment names the case lists.
# The model comes from LLM_MODEL (provider:model, as in scripts/params.example.sh) and the
# provider's key from LLM_API_KEY, both in the environment. It needs no AWS settings and no
# recorded data. Each case is one billed classification call, about forty in all.
# Prints one PASS or FAIL line per case as it is classified, with what the model returned
# against what was expected, then the total; exits 1 when any case fails. A failed model call
# (no credit, rejected key, unreachable provider) stops the run with one line naming it, exit 2.
#
#   export LLM_MODEL=anthropic:claude-opus-5-5 LLM_API_KEY=<key>
#   ./scripts/eval-intents.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=common.sh
source "$ROOT/scripts/common.sh"

case "${1:-}" in
  -h|--help) print_help; exit 0 ;;
  "") ;;
  *) print_help >&2; exit 2 ;;
esac

require_venv
exec "$ROOT/.venv/bin/python" "$ROOT/scripts/eval_intents.py" "$ROOT/tests/intents.json"
