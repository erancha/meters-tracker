#!/usr/bin/env bash
# Back up a deployed stack's data to a local folder, restore it from one, or schedule a nightly
# backup.
#
#   ./scripts/tools/backup-restore.sh backup [<folder> | --base-dir <dir>]
#   ./scripts/tools/backup-restore.sh restore <folder>
#   ./scripts/tools/backup-restore.sh --cron [<dir>] [--force]
#
# The stack is the one the git branch selects, as in deploy.sh: meters-tracker on main,
# meters-tracker-fb on any other branch.
# backup: writes every row of the nine app tables and the current version of every file in the
# files bucket to a new folder: <folder> exactly, or <YYYYMMDD-HHMMSS> under backups/ (under
# backups/fb/ for the meters-tracker-fb stack).
# --base-dir <dir>: puts that timestamped folder under <dir> in place of backups/; when <dir>
# cannot be created or written to (an unmounted drive), it falls back to backups/ and says so.
# restore: makes the stack hold exactly the backup's rows and files, after you type the stack's
# name. A backup taken from another stack restores too; the prompt names both. Files a restore
# replaces or deletes stay as old versions in the bucket.
# --cron: adds a crontab entry that backs the stack up every midnight, under <dir> when given
# (as --base-dir), and logs to backups/cron.log. The entry names the stack of the branch it was
# registered on, so the nightly run backs that stack up whatever branch is checked out then. An
# entry for the stack already there is left alone, with a warning, unless --force replaces it.
#
# A backup folder holds tables/<name>.jsonl.gz, files.tar.gz, files.json (each file's content
# type) and metadata.json; backups/ is gitignored. Requires scripts/lib/aws-config.sh.
#
#   ./scripts/tools/backup-restore.sh backup
#   ./scripts/tools/backup-restore.sh backup --base-dir /mnt/f/backups/meters-tracker
#   ./scripts/tools/backup-restore.sh restore backups/20261105-000000
#   ./scripts/tools/backup-restore.sh --cron /mnt/f/backups/meters-tracker
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib/app-env.sh
source "$SCRIPT_DIR/lib/app-env.sh"

DEFAULT_BASE="$APP_FOLDER/backups"
SCRIPT="$SCRIPT_DIR/tools/backup-restore.sh"

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

# A new backup folder under base: the main stack's directly, another stack's under its suffix.
timestamped() {  # base
  printf '%s' "$1${STACK_SUFFIX:+/${STACK_SUFFIX#-}}/$(date +%Y%m%d-%H%M%S)"
}

register_cron() {  # [dir]
  command -v crontab >/dev/null \
    || { echo "crontab not found; install cron first: sudo apt-get install -y cron" >&2; exit 1; }
  pgrep -x cron >/dev/null || pgrep -x crond >/dev/null \
    || echo "cron is not running, so the entry will not fire until it starts:" \
            "sudo service cron start (WSL does not start it on boot)" >&2
  # The marker is matched at the end of its line, so the main stack's never matches another's.
  # STACK_NAME in the entry overrides the branch that is checked out when cron runs it.
  local marker="# nightly backup of $STACK_NAME" existing entry
  local command=("STACK_NAME=$STACK_NAME" "\"$SCRIPT\"" backup)
  [ -z "${1:-}" ] || command+=(--base-dir "\"$(realpath -m "$1")\"")
  mkdir -p "$DEFAULT_BASE"
  entry="0 0 * * * ${command[*]} >> \"$DEFAULT_BASE/cron.log\" 2>&1 $marker"
  existing="$(crontab -l 2>/dev/null || true)"
  if grep -qE -- " $marker\$" <<< "$existing"; then
    if [ "$FORCE" != true ]; then
      echo "A nightly backup of $STACK_NAME is already registered; --force replaces it:" >&2
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
  echo "Registered the nightly backup of $STACK_NAME:"
  echo "  $entry"
}

case "${1:-}" in
  -h|--help) print_help; exit 0 ;;
  backup|restore|--cron) ;;
  "") usage_error "No command given" ;;
  *) usage_error "Unknown command: $1" ;;
esac
resolve_stack_pair

COMMAND="${1:-}"
shift || true
FORCE=false
case "$COMMAND" in
  backup)
    case "$#:${1:-}" in
      0:) FOLDER="$(timestamped "$(writable_base "$DEFAULT_BASE")")" ;;
      2:--base-dir) FOLDER="$(timestamped "$(writable_base "$2")")" ;;
      1:-*) usage_error "Unknown backup option: $1" ;;
      1:*) FOLDER="$1" ;;
      *) usage_error "backup takes a folder or --base-dir <dir>" ;;
    esac ;;
  restore)
    case "$#:${1:-}" in
      1:-*) usage_error "Unknown restore option: $1" ;;
      1:*) FOLDER="$1" ;;
      *) usage_error "restore takes one backup folder" ;;
    esac ;;
  --cron)
    DIR=""
    for arg in "$@"; do
      case "$arg" in
        --force) FORCE=true ;;
        -*) usage_error "Unknown --cron option: $arg" ;;
        *) [ -z "$DIR" ] || usage_error "--cron takes one folder"; DIR="$arg" ;;
      esac
    done
    register_cron "$DIR"
    exit 0 ;;
  *) usage_error ;;
esac

connect_stack
require_venv
exec "$APP_FOLDER/.venv/bin/python" "$SCRIPT_DIR/tools/backup_restore.py" "$COMMAND" "$STACK_NAME" \
  "$FOLDER"
