#!/usr/bin/env bash
# Delete everything one account owns in a deployed stack, so its owner starts again from an
# empty account: its rows in every app table, its pending items and messages, and every version
# of its photos in the files bucket.
#
#   ./scripts/reset-account.sh [env] <email> [--yes]
#
# env: optional suffix (e.g. dev) that targets the isolated stack meters-tracker-dev, as in
# deploy.sh.
# email: the account's Google address; rows are matched by it and its photos by "<email>/".
# --yes: delete. Without it the script prints the count it would delete per table and the
# photo versions, deletes nothing, and exits 1.
#
# The deletion cannot be undone: the tables keep no backups and the photo versions go too.
# A delegate's own access row and messages are keyed by the delegate's address, so they stay.
# Requires scripts/aws-config.sh.
#
#   ./scripts/reset-account.sh dev owner@gmail.com
#   ./scripts/reset-account.sh dev owner@gmail.com --yes
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=common.sh
source "$ROOT/scripts/common.sh"

YES=() POSITIONAL=()
for arg in "$@"; do
  case "$arg" in
    -h|--help) print_help; exit 0 ;;
    --yes) YES=(--yes) ;;
    -*) print_help >&2; exit 2 ;;
    *) POSITIONAL+=("$arg") ;;
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
exec "$ROOT/.venv/bin/python" "$ROOT/scripts/reset_account.py" "$APP" "$EMAIL" "${YES[@]}"
