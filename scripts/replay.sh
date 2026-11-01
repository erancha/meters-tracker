#!/usr/bin/env bash
# Replay the owner's story against a deployed stack with its real model, as a smoke test of the
# backend after a deploy.
#
#   ./scripts/replay.sh [env] <email> --open <photo> --close <photo> [--statement <photo>] [--yes]
#
# env: optional suffix (e.g. dev) that targets the isolated stack meters-tracker-dev, as in
# deploy.sh. Without it the replay targets the PRODUCTION stack meters-tracker.
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
# Requires scripts/aws-config.sh.
#
#   ./scripts/replay.sh dev owner@gmail.com --open ~/m/open.jpg --close ~/m/close.jpg --yes
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=common.sh
source "$ROOT/scripts/common.sh"

OPTIONS=() POSITIONAL=()
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) print_help; exit 0 ;;
    --yes) OPTIONS+=("$1"); shift ;;
    --open|--close|--statement)
      [ $# -ge 2 ] || { print_help >&2; exit 2; }
      OPTIONS+=("$1" "$2"); shift 2 ;;
    -*) print_help >&2; exit 2 ;;
    *) POSITIONAL+=("$1"); shift ;;
  esac
done
case ${#POSITIONAL[@]} in
  1) ENV_ARGS=() ;;
  2) ENV_ARGS=("${POSITIONAL[0]}") ;;
  *) print_help >&2; exit 2 ;;
esac
EMAIL="${POSITIONAL[-1]}"
parse_env_suffix_arg "${ENV_ARGS[@]}"
require_aws_config
require_venv
exec "$ROOT/.venv/bin/python" "$ROOT/scripts/replay.py" "$APP" "$EMAIL" "${OPTIONS[@]}"
