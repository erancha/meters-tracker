#!/usr/bin/env bash
# Create scripts/aws-config.sh and scripts/params.sh from their .example files.
#
#   ./scripts/init-env.sh [--force]
#
# Options:
#   --force      Overwrite a file that already exists (default: refuse and stop)
#   -h, --help   Show this usage
#
# Both copies are gitignored. Afterwards fill in the AWS access key pair in aws-config.sh and the
# Google OAuth client id and secret in params.sh.
#
#   ./scripts/init-env.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=common.sh
source "$ROOT/scripts/common.sh"

FORCE=""
case "${1:-}" in
  -h|--help) print_help; exit 0 ;;
  --force) FORCE=1 ;;
  "") ;;
  *) print_help >&2; exit 2 ;;
esac
[ $# -le 1 ] || { print_help >&2; exit 2; }

for name in aws-config params; do
  target="$ROOT/scripts/$name.sh"
  if [ -e "$target" ] && [ -z "$FORCE" ]; then
    echo "scripts/$name.sh exists; use --force to overwrite it" >&2
    exit 1
  fi
done
for name in aws-config params; do
  cp "$ROOT/scripts/$name.example.sh" "$ROOT/scripts/$name.sh"
  chmod 600 "$ROOT/scripts/$name.sh"
done
echo "Created scripts/aws-config.sh and scripts/params.sh. Fill in:"
echo "  scripts/aws-config.sh: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION"
echo "  scripts/params.sh: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET"
