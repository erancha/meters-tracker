#!/usr/bin/env bash
# Run the quality gate: bash syntax check of every script, ruff lint and format check, then the
# pytest suite, which leaves out the correction matrix. The matrix runs on request.
#
#   ./scripts/test.sh                          the gate
#   ./scripts/test.sh --matrix [slice ...]     the full correction matrix, or the slices named
#   ./scripts/test.sh --matrix-records         the quick matrix: its record slices, one per state
#   ./scripts/test.sh [pytest-arguments]       pytest with these arguments alone
#
# Creates .venv on the first run and installs requirements-dev.txt into it. Each matrix slice is
# a pytest run of its own, as many at once as the machine has processors (nproc), so its memory
# is returned when it ends; every slice runs, then those that failed are named with their output.
# Examples:
#   ./scripts/test.sh -q tests/test_api.py
#   ./scripts/test.sh --matrix matrix_cards_open_1 matrix_sequences
#
# It never calls the real model. The checks that do are run by hand only: docs/development.md.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=common.sh
source "$ROOT/scripts/common.sh"

case "${1:-}" in -h|--help) print_help; exit 0 ;; esac

# Runs each named slice in its own pytest process, at most nproc at once, then names every slice
# that failed. Returns non-zero when any did, and when it is given none, since then nothing ran.
run_matrix() {
  local logs slice failed=()
  if (( $# == 0 )); then
    echo "correction matrix: no slices to run; the slice lookup came back empty" >&2
    return 1
  fi
  logs="$(mktemp -d)"
  for slice in "$@"; do
    while (( $(jobs -rp | wc -l) >= $(nproc) )); do wait -n || true; done
    (
      status=0
      .venv/bin/pytest -q -p no:cacheprovider -m "$slice" tests/test_correction_matrix.py \
        > "$logs/$slice.log" 2>&1 || status=$?
      echo "$status" > "$logs/$slice.status"
    ) &
  done
  wait
  for slice in "$@"; do
    echo "correction matrix: $slice: $(tail -n 1 "$logs/$slice.log")"
    [[ "$(cat "$logs/$slice.status")" == 0 ]] || failed+=("$slice")
  done
  for slice in "${failed[@]}"; do
    echo "--- $slice failed:"
    tail -n 40 "$logs/$slice.log"
  done
  rm -rf "$logs"
  if (( ${#failed[@]} > 0 )); then
    echo "correction matrix: failed slices: ${failed[*]}" >&2
    return 1
  fi
}

cd "$ROOT"
for script in scripts/*.sh; do bash -n "$script"; done
require_venv
# The slice names, as the matrix module defines them: all of them, or the quick ones.
slices() {
  .venv/bin/python -c 'import sys; sys.path[:0] = ["tests", "src"]
import test_correction_matrix as matrix; print(*getattr(matrix, sys.argv[1]))' "$1"
}
# A lookup is assigned before it is used, so set -e stops the script when it fails.
case "${1:-}" in
  --matrix)
    shift
    if [[ $# -gt 0 ]]; then
      run_matrix "$@"
    else
      names=$(slices MATRIX_SLICES)
      # shellcheck disable=SC2086
      run_matrix $names
    fi
    exit ;;
  --matrix-records)
    [[ $# -eq 1 ]] || { print_help >&2; exit 2; }
    names=$(slices QUICK_SLICES)
    # shellcheck disable=SC2086
    run_matrix $names
    exit ;;
esac
.venv/bin/ruff check src/ tests/ scripts/*.py
.venv/bin/ruff format --check src/ tests/ scripts/*.py
if [[ $# -gt 0 ]]; then
  .venv/bin/pytest "$@"
  exit
fi
.venv/bin/pytest
