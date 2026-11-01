#!/usr/bin/env bash
# Shared helpers for the meters-tracker scripts: repo root, help text, AWS credentials and
# deployment parameters, and stack lookups. Sourced, never run.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Print the calling script's header comment block as --help text (single source of usage docs).
print_help() { awk 'NR==1 {next} /^#/ {sub(/^# ?/, ""); print; next} {exit}' "$0"; }

# The AWS CLI lives in ~/.local/bin, which a non-interactive shell does not have on its PATH.
require_aws_config() {
  export PATH="$HOME/.local/bin:$PATH"
  [ -f "$ROOT/scripts/aws-config.sh" ] \
    || { echo "scripts/aws-config.sh not found; run scripts/init-env.sh and fill in the" \
              "credentials" >&2; exit 1; }
  # shellcheck source=/dev/null
  source "$ROOT/scripts/aws-config.sh"
  require_values aws-config.sh AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_DEFAULT_REGION
}

# Each named variable must hold a real value: set, and not the <placeholder> that the settings
# file's .example sibling carries. The message names the variable, never its value.
require_values() {  # settings-file variable...
  local file="$1" name
  shift
  for name in "$@"; do
    case "${!name:-}" in
      "" | *"<"*">"*)
        echo "$name in scripts/$file is not set or still holds its <placeholder>; fill it in" \
             "(see scripts/${file%.sh}.example.sh)" >&2
        exit 1 ;;
    esac
  done
}

# Creates .venv on the first call and installs requirements-dev.txt into it on every call, so a
# script that runs repo Python never meets a missing or stale dependency.
require_venv() {
  [ -d "$ROOT/.venv" ] || python3 -m venv "$ROOT/.venv"
  "$ROOT/.venv/bin/pip" install --quiet --disable-pip-version-check -r "$ROOT/requirements-dev.txt"
}

require_params() {
  [ -f "$ROOT/scripts/params.sh" ] \
    || { echo "scripts/params.sh not found; run scripts/init-env.sh and fill in the" \
              "values" >&2; exit 1; }
  # shellcheck source=/dev/null
  source "$ROOT/scripts/params.sh"
  require_values params.sh GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET LLM_MODEL
}

# The SSM parameter of a model provider's API key. Each provider has its own, so changing LLM_MODEL
# leaves the other provider's key stored.
model_key_param() {  # provider
  echo "/meters-tracker/llm/$1/api-key"
}

# Stops unless the provider's key is stored. The lookup reads only the parameter's version, so the
# key is never decrypted here.
require_model_key() {  # provider
  local err
  if ! err=$(aws ssm get-parameter --name "$(model_key_param "$1")" \
      --query Parameter.Version --output text 2>&1 >/dev/null); then
    case "$err" in
      *ParameterNotFound*)
        echo "no $1 API key is stored; store it first: ./scripts/set-model-key.sh $1" >&2 ;;
      *) echo "looking up the $1 API key failed: $err" >&2 ;;
    esac
    exit 1
  fi
}

# For the scripts taking an optional environment suffix ("dev" targets the isolated stack pair
# meters-tracker-dev): handles -h and unknown arguments, then sets APP and ENV_SUFFIX.
parse_env_suffix_arg() {
  case "${1:-}" in
    -h|--help) print_help; exit 0 ;;
    -*) print_help >&2; exit 2 ;;
  esac
  [ $# -le 1 ] || { print_help >&2; exit 2; }
  ENV_SUFFIX="${1:+-$1}"
  APP="meters-tracker${ENV_SUFFIX}"
}

# A describe-stacks query result; empty when the stack does not exist yet or the query matches
# nothing. Any other failure (credentials, network, access denied) stops the script. The CLI's
# stderr is kept apart from the value and passed on, so a warning never becomes part of it.
stack_query() {  # stack query
  local out err errors
  errors=$(mktemp)
  if ! out=$(aws cloudformation describe-stacks --stack-name "$1" --query "$2" \
      --output text 2>"$errors"); then
    err=$(<"$errors")
    rm -f "$errors"
    case "$err" in
      *"does not exist"*) return 0 ;;
      *) echo "describe-stacks $1 failed: $err" >&2; return 1 ;;
    esac
  fi
  cat "$errors" >&2
  rm -f "$errors"
  [ "$out" = "None" ] || printf '%s' "$out"
}

# The value of a stack output; empty when the stack does not exist yet or has no such output.
stack_output() {  # stack key
  stack_query "$1" "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue"
}

# The value of a stack output the script cannot go on without. Fails, naming the stack and key,
# when the output is empty; call it in an assignment of its own so the failure stops the script.
require_output() {  # stack key
  local value
  value=$(stack_output "$1" "$2") || return 1
  [ -n "$value" ] || { echo "stack $1 has no output $2" >&2; return 1; }
  printf '%s' "$value"
}
