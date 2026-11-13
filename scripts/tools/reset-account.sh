#!/usr/bin/env bash
# Delete everything one account owns in a deployed stack, so its owner starts again from an
# empty account: its rows in every app table, its pending items and messages, and every version
# of its photos in the files bucket.
#
#   ./scripts/tools/reset-account.sh <email> [--yes]
#
# The stack is the one the git branch selects, as in deploy.sh: meters-tracker on main,
# meters-tracker-fb on any other branch.
# email: the account's Google address; rows are matched by it and its photos by "<email>/".
# --yes: delete. Without it the script prints the count it would delete per table and the
# photo versions, deletes nothing, and exits 1.
#
# The deletion cannot be undone: the tables keep no backups and the photo versions go too.
# A delegate's own access row and messages are keyed by the delegate's address, so they stay.
# Requires scripts/lib/aws-config.sh.
#
#   ./scripts/tools/reset-account.sh owner@gmail.com
#   ./scripts/tools/reset-account.sh owner@gmail.com --yes
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib/app-env.sh
source "$SCRIPT_DIR/lib/app-env.sh"

YES=() POSITIONAL=()
for arg in "$@"; do
  case "$arg" in
    -h|--help) print_help; exit 0 ;;
    --yes) YES=(--yes) ;;
    -*) usage_error "Unknown option: $arg" ;;
    *) POSITIONAL+=("$arg") ;;
  esac
done
[ ${#POSITIONAL[@]} -eq 1 ] || usage_error "Expected one email address"
EMAIL="${POSITIONAL[0]}"
connect_stack
require_venv
exec "$APP_FOLDER/.venv/bin/python" "$SCRIPT_DIR/tools/reset_account.py" "$STACK_NAME" "$EMAIL" \
  "${YES[@]}"
