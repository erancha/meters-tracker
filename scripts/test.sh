#!/usr/bin/env bash
# Run the whole quality gate: bash syntax check of every script, ruff lint and format check, then
# the pytest suite.
#
#   ./scripts/test.sh [pytest-arguments]
#
# Creates .venv on the first run and installs requirements-dev.txt into it. Every argument other
# than -h/--help goes to pytest, e.g. ./scripts/test.sh -q tests/test_api.py
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=common.sh
source "$ROOT/scripts/common.sh"

case "${1:-}" in -h|--help) print_help; exit 0 ;; esac

cd "$ROOT"
for script in scripts/*.sh; do bash -n "$script"; done
require_venv
.venv/bin/ruff check src/ tests/ scripts/*.py
.venv/bin/ruff format --check src/ tests/ scripts/*.py
.venv/bin/pytest "$@"
