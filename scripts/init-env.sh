#!/usr/bin/env bash
# Create the gitignored settings files under scripts/lib/ from their committed .example files:
# each scripts/lib/<name>.example.sh the repo has becomes scripts/lib/<name>.sh.
#
#   ./scripts/init-env.sh [--force]
#
# Options:
#   --force      Overwrite the settings files that already exist (default: keep each one and
#                create only the missing ones)
#   -h, --help   Show this usage
#
# Each copy is readable by its owner only. Afterwards fill in every value of each copy; the
# comments of its .example file document the keys.
#
#   ./scripts/init-env.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/app-env.sh
source "$SCRIPT_DIR/lib/app-env.sh"

FORCE=""
case "${1:-}" in
  -h|--help) print_help; exit 0 ;;
  --force) FORCE=1 ;;
  "") ;;
  *) usage_error "Unknown argument: $1" ;;
esac
[ $# -le 1 ] || usage_error "Takes at most --force"

EXAMPLES=("$SCRIPT_DIR"/lib/*.example.sh)
for example in "${EXAMPLES[@]}"; do
  assert_gitignored "${example%.example.sh}.sh"
done
for example in "${EXAMPLES[@]}"; do
  file="${example%.example.sh}.sh"
  if [ -e "$file" ] && [ -z "$FORCE" ]; then
    echo "scripts/lib/$(basename "$file") already exists; left as it is (--force overwrites it)."
    continue
  fi
  cp "$example" "$file"
  chmod 600 "$file"
  echo "Created scripts/lib/$(basename "$file"); fill in its values."
done
