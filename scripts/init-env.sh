#!/usr/bin/env bash
# Create the gitignored settings files scripts/lib/aws-config.sh, scripts/lib/secrets.sh and
# scripts/lib/params.sh from their .example files.
#
#   ./scripts/init-env.sh [--force]
#
# Options:
#   --force      Overwrite a file that already exists (default: refuse and stop)
#   -h, --help   Show this usage
#
# Each copy is readable by its owner only. Afterwards fill in the AWS access key pair in
# aws-config.sh and every value in secrets.sh and params.sh.
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

SETTINGS_FILES=("${SECRETS_FILES[@]}" "$SCRIPT_DIR/lib/params.sh")
for file in "${SETTINGS_FILES[@]}"; do
  if [ -e "$file" ] && [ -z "$FORCE" ]; then
    echo "scripts/lib/$(basename "$file") exists; use --force to overwrite it" >&2
    exit 1
  fi
done
for file in "${SETTINGS_FILES[@]}"; do
  cp "${file%.sh}.example.sh" "$file"
  chmod 600 "$file"
done
echo "Created the settings files. Fill in:"
echo "  scripts/lib/aws-config.sh: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION"
echo "  scripts/lib/secrets.sh: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET"
echo "  scripts/lib/params.sh: SES_SENDER, ADMIN_EMAIL, ALLOWED_EMAILS"
