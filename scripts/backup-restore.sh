#!/usr/bin/env bash
# Back up a deployed stack's data to a local folder, restore it from one, or schedule a nightly
# backup.
#
#   ./scripts/backup-restore.sh [env] backup [<folder> | --base-dir <dir>]
#   ./scripts/backup-restore.sh [env] restore <folder>
#   ./scripts/backup-restore.sh [env] --cron [<dir>] [--force]
#
# env: optional suffix (e.g. dev) that targets the isolated stack meters-tracker-dev, as in
# deploy.sh. Without it the script works on the production stack.
# backup: writes every row of the nine app tables and the current version of every file in the
# files bucket to a new folder: <folder> exactly, or <YYYYMMDD-HHMMSS> under backups/ (under
# backups/<env>/ for an env stack).
# --base-dir <dir>: puts that timestamped folder under <dir> in place of backups/; when <dir>
# cannot be created or written to (an unmounted drive), it falls back to backups/ and says so.
# restore: makes the stack hold exactly the backup's rows and files, after you type the stack's
# name. A backup taken from another stack restores too; the prompt names both. Files a restore
# replaces or deletes stay as old versions in the bucket.
# --cron: adds a crontab entry that backs the stack up every midnight, under <dir> when given
# (as --base-dir), and logs to backups/cron.log. An entry for the stack already there is left
# alone, with a warning, unless --force replaces it.
#
# A backup folder holds tables/<name>.jsonl.gz, files.tar.gz, files.json (each file's content
# type) and metadata.json; backups/ is gitignored. Requires scripts/aws-config.sh.
#
#   ./scripts/backup-restore.sh backup
#   ./scripts/backup-restore.sh dev backup --base-dir /mnt/f/backups/meters-tracker
#   ./scripts/backup-restore.sh dev restore backups/dev/20261105-000000
#   ./scripts/backup-restore.sh --cron /mnt/f/backups/meters-tracker
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=common.sh
source "$ROOT/scripts/common.sh"

DEFAULT_BASE="$ROOT/backups"
SCRIPT="$ROOT/scripts/backup-restore.sh"

usage_error() { print_help >&2; exit 2; }

# The base folder a timestamped backup goes under: the one asked for when it can be written to,
# otherwise backups/, so a nightly run with its drive unmounted still leaves a backup.
writable_base() {  # dir
  if mkdir -p "$1" 2>/dev/null && [ -w "$1" ]; then
    printf '%s' "$1"
  else
    echo "$1 cannot be written to; backing up under $DEFAULT_BASE" >&2
    printf '%s' "$DEFAULT_BASE"
  fi
}

# A new backup folder under base: production's directly, an env stack's under its suffix.
timestamped() {  # base
  printf '%s' "$1${ENV_SUFFIX:+/${ENV_SUFFIX#-}}/$(date +%Y%m%d-%H%M%S)"
}

register_cron() {  # [dir]
  command -v crontab >/dev/null \
    || { echo "crontab not found; install cron first: sudo apt-get install -y cron" >&2; exit 1; }
  pgrep -x cron >/dev/null || pgrep -x crond >/dev/null \
    || echo "cron is not running, so the entry will not fire until it starts:" \
            "sudo service cron start (WSL does not start it on boot)" >&2
  # The marker is matched at the end of its line, so the production stack's never matches dev's.
  local marker="# nightly backup of $APP" command=("\"$SCRIPT\"") existing entry
  [ -z "$ENV_SUFFIX" ] || command+=("${ENV_SUFFIX#-}")
  command+=(backup)
  [ -z "${1:-}" ] || command+=(--base-dir "\"$(realpath -m "$1")\"")
  mkdir -p "$DEFAULT_BASE"
  entry="0 0 * * * ${command[*]} >> \"$DEFAULT_BASE/cron.log\" 2>&1 $marker"
  existing="$(crontab -l 2>/dev/null || true)"
  if grep -qE -- " $marker\$" <<< "$existing"; then
    if [ "$FORCE" != true ]; then
      echo "A nightly backup of $APP is already registered; --force replaces it:" >&2
      grep -E -- " $marker\$" <<< "$existing" >&2
      exit 0
    fi
    existing="$(grep -vE -- " $marker\$" <<< "$existing" || true)"
  fi
  if [ -n "$existing" ]; then
    printf '%s\n%s\n' "$existing" "$entry" | crontab -
  else
    printf '%s\n' "$entry" | crontab -
  fi
  echo "Registered the nightly backup of $APP:"
  echo "  $entry"
}

case "${1:-}" in
  -h|--help) print_help; exit 0 ;;
  backup|restore|--cron) ENV_ARGS=() ;;
  ""|-*) usage_error ;;
  *) ENV_ARGS=("$1"); shift ;;
esac
parse_env_suffix_arg "${ENV_ARGS[@]}"

COMMAND="${1:-}"
shift || true
FORCE=false
case "$COMMAND" in
  backup)
    case "$#:${1:-}" in
      0:) FOLDER="$(timestamped "$(writable_base "$DEFAULT_BASE")")" ;;
      2:--base-dir) FOLDER="$(timestamped "$(writable_base "$2")")" ;;
      1:-*) usage_error ;;
      1:*) FOLDER="$1" ;;
      *) usage_error ;;
    esac ;;
  restore)
    case "$#:${1:-}" in
      1:-*) usage_error ;;
      1:*) FOLDER="$1" ;;
      *) usage_error ;;
    esac ;;
  --cron)
    DIR=""
    for arg in "$@"; do
      case "$arg" in
        --force) FORCE=true ;;
        -*) usage_error ;;
        *) [ -z "$DIR" ] || usage_error; DIR="$arg" ;;
      esac
    done
    register_cron "$DIR"
    exit 0 ;;
  *) usage_error ;;
esac

require_aws_config
require_venv
exec "$ROOT/.venv/bin/python" "$ROOT/scripts/backup_restore.py" "$COMMAND" "$APP" "$FOLDER"
