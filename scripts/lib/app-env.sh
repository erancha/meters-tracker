#!/usr/bin/env bash
# Shared helpers for the bash scripts in scripts/: status output, the app's identity (name, region,
# branch-selected stack pair) and loaders for the gitignored settings files, so every script
# resolves these values the same way and none re-declares them.
#
# Core responsibilities:
# - print_help / show_help: a script's header comment block, as its --help text
# - usage_error: the help and a message on stderr, exit 2, for an argument a script does not take
# - die/info/ok/warn/step: status lines; die exits 1 with the message on stderr, step prefixes the
#   wall-clock time and the time elapsed since the sourcing script started
# - split_lines: newline-separated command output into an array
# - load_aws_config / load_secrets: source the gitignored lib/aws-config.sh / lib/secrets.sh,
#   refusing a file git would track or a secret value that appears in a tracked file
# - assert_secrets_untracked: that check, also run on staged changes by .githooks/pre-commit
# - assert_addresses_untracked: the same for the email addresses of a private settings file
#   (PRIVATE_FILES), except the ones PUBLISHED_SETTINGS names
# - get_app_name / get_region / resolve_stack: the stack pair this checkout deploys to, and
#   STACK_NAMES, the stacks aws-helper.sh operates on
# - SAM_CLI_TELEMETRY: off for every sam command a script runs
# - LOG_RETENTION_DAYS: how long the stacks' log groups keep their events
# - stack_outputs / stack_output: CloudFormation outputs of the app stack
# - ensure_node_modules: npm ci in a package folder whose lockfile changed since its last install
# - this app's own helpers, at the end: load_params for the gitignored lib/params.sh,
#   connect_stack for its scripts, the Python virtualenv, the model key parameter, and lookups on
#   either stack of the pair
#
# Expects SCRIPT_DIR to point at scripts/ before it is sourced.

# The variables set here are read by the scripts that source this file.
# shellcheck disable=SC2034

APP_FOLDER="$(dirname "$SCRIPT_DIR")"
# The app name prefixes every stack name: <app> from main, <app>-fb from any other branch.
DEFAULT_APP_NAME='meters-tracker'
# The stacks aws-helper.sh operates on: the branch's stack name with each suffix appended ('' is
# that stack itself), in the order they can be deleted.
STACK_SUFFIXES=('' '-cognito')
DEFAULT_REGION='eu-central-1'
# Days every log group of the stacks keeps its events: lib/stack-params.sh passes it to the
# templates' LogRetentionDays, and aws-helper.sh --log-retention sets it on existing groups.
LOG_RETENTION_DAYS=14
# The Vite app (repo-relative) and the config file deploy.sh writes for it.
FRONTEND_FOLDER='frontend'
APP_CONFIG_FILE="$APP_FOLDER/$FRONTEND_FOLDER/public/config.js"
# The AWS CLI lives in ~/.local/bin, which a non-interactive shell does not have on its PATH.
export PATH="$HOME/.local/bin:$PATH"

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
WHITE_ON_BLUE='\033[1;37;44m'
NC='\033[0m'

# SAM CLI 1.165's usage telemetry crashes the command after it succeeds (FileNotFoundError in
# get_project_name), failing an otherwise good build.
export SAM_CLI_TELEMETRY=0

die() {
    echo -e "${RED}ERROR: $*${NC}" >&2
    exit 1
}

info() { echo -e "${CYAN}$*${NC}"; }
ok() { echo -e "${GREEN}$*${NC}"; }
warn() { echo -e "${YELLOW}$*${NC}"; }

# Prints the header comment block of a script, the single source of its usage: the comment lines
# between the shebang and the first other line. Without an argument, the executed script ($0).
print_help() { awk 'NR==1 {next} /^#/ {sub(/^# ?/, ""); print; next} {exit}' "${1:-$0}"; }

# The executed script's --help text. A script whose help is more than its header comment block
# defines its own show_help after sourcing this file.
show_help() { print_help "$0"; }

# Ends a script on an argument it does not take, or one it lacks: the help, then the message, on
# stderr, and exit status 2.
usage_error() {
    show_help >&2
    echo -e "\n${RED}ERROR: $*${NC}" >&2
    exit 2
}

# SECONDS counts from the start of the sourcing script's shell.
step() {
    printf "\n${WHITE_ON_BLUE}%s, elapsed %02d:%02d:%02d : %s${NC}\n" \
        "$(date +%H:%M:%S)" $((SECONDS / 3600)) $((SECONDS % 3600 / 60)) $((SECONDS % 60)) "$*"
}

# Splits newline-separated text into the named array; empty text gives an empty array. Callers
# capture the command output first (out="$(cmd)") so a failing command still stops the script,
# which reading it through mapfile < <(cmd) would not.
# shellcheck disable=SC2034  # target is a reference to the caller's array
split_lines() {
    local -n target="$1"
    target=()
    if [[ -n "$2" ]]; then
        mapfile -t target <<<"$2"
    fi
}

# Settings a secrets file sets that are public, so a copy in a tracked file is no leak.
PUBLIC_SETTINGS=(AWS_DEFAULT_REGION)

# The gitignored files that hold secrets.
SECRETS_FILES=("$SCRIPT_DIR/lib/aws-config.sh" "$SCRIPT_DIR/lib/secrets.sh")

# Gitignored files of private settings that are no secret (addresses, limits, URLs): their other
# values may appear in git, but an email address they hold may not, unless PUBLISHED_SETTINGS names
# the setting that holds it.
PRIVATE_FILES=("$SCRIPT_DIR/lib/params.sh")
# Settings of a private file whose address the app shows its users, so a copy in a tracked file is
# no leak.
PUBLISHED_SETTINGS=()

# Fatal unless git ignores <file>.
assert_gitignored() {
    git -C "$APP_FOLDER" check-ignore -q "$1" ||
        die "$1 is tracked or not gitignored; it must stay out of git"
}

# The names of the settings <file> sets (KEY=value, with or without export).
setting_names() { grep -oP '^(?:export )?\K\w+(?==)' "$1"; }

# Fatal when <value> appears in git's files: <scope> "worktree" scans the tracked files as they are
# on disk, "index" the staged snapshot a commit records. <label> names the value in the message,
# which never holds the value itself; further arguments are git grep options.
refuse_copy_in_git() {
    local scope="$1" label="$2" value="$3" options=(-F "${@:4}") status=0
    [[ "$scope" == index ]] && options+=(--cached)
    git -C "$APP_FOLDER" grep "${options[@]}" -q -e "$value" || status=$?
    case "$status" in
        0) die "$label appears in git ($scope):" \
               "$(git -C "$APP_FOLDER" grep "${options[@]}" -l -e "$value" | tr '\n' ' ')" ;;
        1) ;;
        *) die "git grep failed while scanning for $label" ;;
    esac
}

# Fatal when git tracks or would track <file>, or when the value of a non-public setting it sets
# appears in git's files. <scope> as for refuse_copy_in_git.
assert_secrets_untracked() {
    local file="$1" scope="$2" name
    assert_gitignored "$file"
    (
        # shellcheck source=/dev/null
        source "$file"
        for name in $(setting_names "$file"); do
            [[ " ${PUBLIC_SETTINGS[*]} " == *" $name "* ]] && continue
            [[ -n "${!name}" ]] || die "$name is empty in $file"
            refuse_copy_in_git "$scope" "The value of $name (from $file)" "${!name}"
        done
    ) || exit 1
}

# Fatal when git tracks or would track <file>, or when an email address one of its settings holds
# appears in git's files, ignoring case, unless it is the value of a setting PUBLISHED_SETTINGS
# names. Backslashes are dropped first, so an address inside a regex (name\.x@host\.com) counts;
# an "@" that is not part of a full address is fatal, since the address it stands for could not
# be checked. <scope> as for refuse_copy_in_git.
assert_addresses_untracked() {
    local file="$1" scope="$2"
    assert_gitignored "$file"
    (
        local name value found addresses address published=" "
        local pattern='[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+'
        # shellcheck source=/dev/null
        source "$file"
        for name in "${PUBLISHED_SETTINGS[@]}"; do
            published+="${!name,,} "
        done
        for name in $(setting_names "$file"); do
            value="${!name}"
            value="${value//\\/}"
            found="$(grep -oE "$pattern" <<<"$value")" || [[ $? -eq 1 ]] ||
                die "grep failed while reading the addresses of $name"
            split_lines addresses "$found"
            [[ "${#addresses[@]}" -eq "$(tr -cd @ <<<"$value" | wc -c)" ]] ||
                die "$name in $file holds an \"@\" outside a full address; write each address" \
                    "in full, e.g. a\\.b@host\\.com|c@host\\.com"
            for address in "${addresses[@]}"; do
                [[ "$published" == *" ${address,,} "* ]] && continue
                refuse_copy_in_git "$scope" "An address of $name (from $file)" "$address" -i
            done
        done
    ) || exit 1
}

# Sources lib/<file> after <check> (assert_secrets_untracked or assert_addresses_untracked), and
# points git at the repo's hooks so the pre-commit hook applies the same check to every commit
# made from a checkout that holds the file.
source_checked_file() {
    local file="$SCRIPT_DIR/lib/$1" check="$2"
    [[ -f "$file" ]] || die "Missing $file (copy lib/${1%.sh}.example.sh and fill it in)"
    "$check" "$file" worktree
    # shellcheck source=/dev/null
    source "$file"
    if [[ "$(git -C "$APP_FOLDER" config --default "" core.hooksPath)" != .githooks ]]; then
        git -C "$APP_FOLDER" config core.hooksPath .githooks
        # On stderr, so a caller that captures a script's output never reads this line.
        info "Enabled the repo's git hooks (core.hooksPath=.githooks)." >&2
    fi
}

source_secrets_file() { source_checked_file "$1" assert_secrets_untracked; }
source_private_file() { source_checked_file "$1" assert_addresses_untracked; }

# The deploying user's AWS credentials and region.
load_aws_config() { source_secrets_file aws-config.sh; }

# The Google OAuth client the Cognito stack signs users in through.
load_secrets() { source_secrets_file secrets.sh; }

get_app_name() {
    printf '%s' "${APP_NAME:-$DEFAULT_APP_NAME}"
}

get_region() {
    printf '%s' "${AWS_DEFAULT_REGION:-$DEFAULT_REGION}"
}

# The checkout's branch selects the stack: main deploys <app>, any other branch deploys <app>-fb.
# An exported STACK_NAME wins. STACK_NAMES, the stacks aws-helper.sh operates on, is that name
# with each of STACK_SUFFIXES appended.
resolve_stack() {
    local app_name suffix
    app_name="$(get_app_name)"
    BRANCH="$(git -C "$APP_FOLDER" rev-parse --abbrev-ref HEAD)" ||
        die "Could not read the git branch of $APP_FOLDER"
    if [ "$BRANCH" = main ]; then
        IS_MAIN=true
        STACK_NAME="${STACK_NAME:-$app_name}"
    else
        IS_MAIN=false
        STACK_NAME="${STACK_NAME:-${app_name}-fb}"
    fi
    STACK_NAMES=()
    for suffix in "${STACK_SUFFIXES[@]}"; do
        STACK_NAMES+=("$STACK_NAME$suffix")
    done
}

stack_outputs() {
    aws cloudformation describe-stacks --region "$(get_region)" --stack-name "$STACK_NAME" \
        --query 'Stacks[0].Outputs' --output json
}

# Reads a required output from the JSON stack_outputs printed; a missing key is fatal.
stack_output() {
    local outputs="$1" key="$2"
    jq -er --arg key "$key" '.[] | select(.OutputKey == $key) | .OutputValue' <<<"$outputs" ||
        die "Stack '$STACK_NAME' has no output '$key'"
}

# The stamp records the package-lock.json the folder's node_modules was installed from, so an
# unchanged lockfile skips the install and any lockfile change (pull, branch switch, upgrade)
# reinstalls. npm ci installs exactly what the lockfile pins and fails, rather than rewriting the
# lockfile, when it no longer matches package.json.
ensure_node_modules() {
    local folder="$APP_FOLDER/$1" stamp lock_hash
    stamp="$folder/node_modules/.package-lock.sha256"
    lock_hash="$(sha256sum "$folder/package-lock.json" | cut -d' ' -f1)"
    if [[ -f "$stamp" && "$(cat "$stamp")" == "$lock_hash" ]]; then
        return
    fi
    step "npm ci: $1"
    (cd "$folder" && npm ci --no-audit --no-fund)
    printf '%s\n' "$lock_hash" >"$stamp"
}

# The helpers below are this app's own; everything above is shared with the other AWS repos.

# The deployment's private settings: the SES sender, the admin address and the sign-up allowed
# list. The app shows the sender on every mail and the admin address on the refusal page, but
# no address of it is in git.
load_params() { source_private_file params.sh; }

# Stops unless each named variable is set, naming the settings file under lib/ that exports it.
require_settings() {  # settings-file variable...
    local file="$1" name
    shift
    for name in "$@"; do
        [[ -v "$name" ]] ||
            die "$name is not set in scripts/lib/$file; add it (see lib/${file%.sh}.example.sh)"
    done
}

# Creates .venv on the first call and installs requirements-dev.txt into it on every call, so a
# script that runs repo Python never meets a missing or stale dependency.
require_venv() {
    [ -d "$APP_FOLDER/.venv" ] || python3 -m venv "$APP_FOLDER/.venv"
    "$APP_FOLDER/.venv/bin/pip" install --quiet --disable-pip-version-check \
        -r "$APP_FOLDER/requirements-dev.txt"
}

# The branch's stack pair, without credentials: STACK_NAME, COGNITO_STACK_NAME, and STACK_SUFFIX,
# the part of the stack name after the app name ("" on main, "-fb" on any other branch), for the
# names that must differ between the pairs.
resolve_stack_pair() {
    local app_name
    app_name="$(get_app_name)"
    resolve_stack
    [[ "$STACK_NAME" == "$app_name"* ]] ||
        die "Stack name '$STACK_NAME' does not start with the app name '$app_name'"
    COGNITO_STACK_NAME="${STACK_NAMES[1]}"
    STACK_SUFFIX="${STACK_NAME#"$app_name"}"
}

# What a script of this repo needs to work on the deployed stacks: the credentials, the branch's
# stack pair, and the region exported for every aws call and boto3 client the script makes.
connect_stack() {
    load_aws_config
    resolve_stack_pair
    AWS_DEFAULT_REGION="$(get_region)"
    export AWS_DEFAULT_REGION
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
                echo "no $1 API key is stored; store it first:" \
                    "./scripts/aws-helper.sh --set-model-key $1" >&2 ;;
            *) echo "looking up the $1 API key failed: $err" >&2 ;;
        esac
        exit 1
    fi
}

# The text result of a CloudFormation lookup on one stack; empty when the stack does not exist yet
# or the lookup matches nothing. Any other failure (credentials, network, access denied) stops the
# script. The CLI's stderr is kept apart from the value and passed on, so a warning never becomes
# part of it.
cfn_query() {  # subcommand stack [argument...]
    local out err errors
    errors=$(mktemp)
    if ! out=$(aws cloudformation "$1" --stack-name "$2" "${@:3}" --output text 2>"$errors"); then
        err=$(<"$errors")
        rm -f "$errors"
        case "$err" in
            *"does not exist"*) return 0 ;;
            *) echo "$1 $2 failed: $err" >&2; return 1 ;;
        esac
    fi
    cat "$errors" >&2
    rm -f "$errors"
    [ "$out" = "None" ] || printf '%s' "$out"
}

stack_query() {  # stack query
    cfn_query describe-stacks "$1" --query "$2"
}

# The value of an output of either stack of the pair; empty when the stack does not exist yet or
# has no such output.
optional_output() {  # stack key
    stack_query "$1" "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue"
}

# The value of a stack output the script cannot go on without. Fails, naming the stack and key,
# when the output is empty; call it in an assignment of its own so the failure stops the script.
require_output() {  # stack key
    local value
    value=$(optional_output "$1" "$2") || return 1
    [ -n "$value" ] || { echo "stack $1 has no output $2" >&2; return 1; }
    printf '%s' "$value"
}
