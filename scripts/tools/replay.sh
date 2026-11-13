#!/usr/bin/env bash
# Replay the owner's story against a deployed stack with its real model, as a smoke test of the
# backend after a deploy.
#
#   ./scripts/tools/replay.sh <email> --open <photo> --close <photo> [--statement <photo>] [--yes]
#
# The stack is the one the git branch selects, as in deploy.sh: meters-tracker-fb on any branch
# but main. On main the replay targets the PRODUCTION stack meters-tracker.
# email: the account the replay acts as. EVERYTHING it owns in the stack is deleted first, as
# reset-account.sh --yes does.
# --open <photo>: a two-number electricity meter whose serial no meter in the account has.
# --close <photo>: the same meter showing a higher reading.
# --statement <photo>: optional; a previous statement listing the tariffs.
# --yes: reset the account and replay. Without it the script prints what the reset would
# delete, deletes nothing, replays nothing, and exits 1.
#
# The script invokes the api Lambda directly, with the JWT claims the sign-in would give the
# address, and walks: the welcome, the opening photo, the typed property and apartment, a typed
# period start that puts today in the period's last week, the top number, the closing photo,
# the close, typed tariffs and the charge they price, then the statement's tariffs card and a
# typed cancel. It prints every reply and card, and exits 1 on the first card of another kind or
# status other than 200. A failed AWS call stops it with one line naming the cause, exit 2.
# Each photo read and each typed message is a billed model call: five for a run, and three more
# with --statement (the statement photo is read as a meter, then as a statement). The photos
# are real meter photos, which the owner keeps in Google Drive, not in the repo.
# Requires scripts/lib/aws-config.sh.
#
#   ./scripts/tools/replay.sh owner@gmail.com --open ~/m/open.jpg --close ~/m/close.jpg --yes
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib/app-env.sh
source "$SCRIPT_DIR/lib/app-env.sh"

OPTIONS=() POSITIONAL=()
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) print_help; exit 0 ;;
    --yes) OPTIONS+=("$1"); shift ;;
    --open|--close|--statement)
      [ $# -ge 2 ] || usage_error "$1 needs a value"
      OPTIONS+=("$1" "$2"); shift 2 ;;
    -*) usage_error "Unknown option: $1" ;;
    *) POSITIONAL+=("$1"); shift ;;
  esac
done
[ ${#POSITIONAL[@]} -eq 1 ] || usage_error "Expected one email address"
EMAIL="${POSITIONAL[0]}"
connect_stack
require_venv
exec "$APP_FOLDER/.venv/bin/python" "$SCRIPT_DIR/tools/replay.py" "$STACK_NAME" "$EMAIL" "${OPTIONS[@]}"
