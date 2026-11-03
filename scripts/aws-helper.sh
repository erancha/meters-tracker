#!/usr/bin/env bash
# Read the deployed stack's CloudWatch logs, manage the SES verification of users' addresses, and
# sum the model spend.
#
# LOGS
#   All warnings and errors of the entire stack (every function of both stacks, each under a
#   "== <stack> <logical-id> ==" line):
#     ./scripts/aws-helper.sh --errors [env] [--since 1h]
#   All events of the entire stack:
#     ./scripts/aws-helper.sh --logs [env] [--since 1h]
#   One function's log, or only its warnings and errors:
#     ./scripts/aws-helper.sh --logs <target> [env] [--since 1h] [--follow] [--errors]
#   The events matching a CloudWatch filter pattern, of the entire stack or of one function:
#     ./scripts/aws-helper.sh --filter <pattern> [--logs [<target>]] [env] [--since 1h]
#   A function's log group path, and the deployed functions of both stacks:
#     ./scripts/aws-helper.sh --log-group <target> [env]
#     ./scripts/aws-helper.sh --functions [env]
#
#   <target>              api, presignup, or <stack>:<logical-id>; CloudFormation appends random
#                         suffixes to Lambda names, so the target is resolved to the real one.
#                         After --logs, any other word is not a target: --logs dev is the
#                         entire dev stack
#   --since <duration>    How far back to read (aws logs tail syntax: 30m, 1h, 1d; default 1h)
#   --follow              Keep streaming new events; needs a <target>
#   --errors              Keep only warning and error events
#   --filter <pattern>    Keep only events matching the pattern, e.g. '"reading saved"'
#
# SES
#   SES identities belong to the AWS account and region, not to a stack, so the SES commands
#   take no env.
#   While the account is in the SES sandbox, mail reaches only a verified address.
#   Each address with its verification status (SUCCESS, PENDING, FAILED, ...), then whether the
#   account is still in the sandbox:
#     ./scripts/aws-helper.sh --ses-status
#   Mail each address a new verification link, valid 24 hours:
#     ./scripts/aws-helper.sh --ses-verify <email> [<email> ...]
#   Delete each address's SES identity, so the sandbox refuses mail to it, e.g. to see a report
#   fail; refuses SES_SENDER of scripts/params.sh, whose removal stops every mail:
#     ./scripts/aws-helper.sh --ses-unverify <email> [<email> ...]
#
# COSTS
#   The api function's model calls by UTC day and by kind of call: calls, tokens in and out,
#   dollars at the prices in src/common/model.py, which a provider price change does not reach
#   until the table is updated and deployed:
#     ./scripts/aws-helper.sh --costs [env] [--since 1h]
#
# env: optional suffix (e.g. dev) naming the stack pair, as in deploy.sh.
# -h, --help: show this usage.
#
# Examples:
#   ./scripts/aws-helper.sh --errors --since 1d
#   ./scripts/aws-helper.sh --errors dev --since 1d
#   ./scripts/aws-helper.sh --logs --since 1d
#   ./scripts/aws-helper.sh --logs api --since 1d
#   ./scripts/aws-helper.sh --logs presignup --errors --since 1d
#   ./scripts/aws-helper.sh --ses-status
#   ./scripts/aws-helper.sh --ses-verify tenant@example.com owner@example.com
#   ./scripts/aws-helper.sh --costs --since 1d
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=common.sh
source "$ROOT/scripts/common.sh"

ACTION="" TARGET="" EVERY_FUNCTION="" SINCE="1h" FOLLOW="" FILTER="" ENV_ARGS=() EMAILS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --log-group|--since|--filter)
      [ $# -ge 2 ] || { print_help >&2; exit 2; } ;;
  esac
  case "$1" in
    --logs)      ACTION=logs; shift
                 case "${1:-}" in api|presignup|*:*) TARGET="$1"; shift ;; esac ;;
    --log-group) ACTION=log-group; TARGET="$2"; shift 2 ;;
    --functions) ACTION=functions; shift ;;
    --costs)     ACTION=costs; TARGET=api; shift ;;
    --ses-status) ACTION=ses-status; shift ;;
    --ses-verify|--ses-unverify)
      ACTION="${1#--}"; shift
      [ $# -gt 0 ] || { print_help >&2; exit 2; }
      for email in "$@"; do
        [[ "$email" == *@* ]] || { echo "not an email address: $email" >&2; exit 2; }
      done
      EMAILS=("$@"); shift $# ;;
    --since)     SINCE="$2"; shift 2 ;;
    --follow)    FOLLOW=1; shift ;;
    --errors)    FILTER="?ERROR ?WARNING"; shift ;;
    --filter)    FILTER="$2"; shift 2 ;;
    -h|--help)   print_help; exit 0 ;;
    -*)          print_help >&2; exit 2 ;;
    *)           ENV_ARGS+=("$1"); shift ;;
  esac
done
[ -n "$ACTION" ] || [ -z "$FILTER" ] || ACTION=logs
[ -n "$ACTION" ] || { print_help >&2; exit 2; }
[ "$ACTION" != logs ] || [ -n "$TARGET" ] || EVERY_FUNCTION=1
[ -z "$EVERY_FUNCTION" ] || [ -z "$FOLLOW" ] \
  || { echo "--follow streams one function; name it: --logs api or --logs presignup" >&2; exit 2; }
parse_env_suffix_arg "${ENV_ARGS[@]}"
# The environment as the caller typed it, for the commands the messages suggest.
ENV_ARG="${ENV_SUFFIX:+ ${ENV_SUFFIX#-}}"

require_aws_config

# Sets STACK and LOGICAL for a target; runs in the parent shell so an unknown target aborts the
# whole script.
resolve() {  # target
  case "$1" in
    api)       STACK="$APP";           LOGICAL=ApiFunction ;;
    presignup) STACK="${APP}-cognito"; LOGICAL=PreSignupFunction ;;
    *:*)       STACK="${1%%:*}";       LOGICAL="${1#*:}" ;;
    *)         echo "unknown target: $1 (expected api, presignup, or <stack>:<logical-id>)" >&2
               exit 1 ;;
  esac
}

# The logical and physical name of each Lambda function of a stack, one tab-separated pair per
# line; stops the script when the stack holds none or does not exist. Call it in an assignment of
# its own, so a failed lookup stops the script.
stack_functions() {  # stack
  local functions
  functions=$(cfn_query describe-stack-resources "$1" \
    --query "StackResources[?ResourceType=='AWS::Lambda::Function'].\
[LogicalResourceId,PhysicalResourceId]") || return 1
  [ -n "$functions" ] || { echo "stack $1 has no functions or does not exist; deploy it:" \
                                "./scripts/deploy.sh$ENV_ARG" >&2; return 1; }
  echo "$functions"
}

# Call it in an assignment of its own, so a failed lookup stops the script.
log_group() {
  local name
  name=$(cfn_query describe-stack-resources "$STACK" --logical-resource-id "$LOGICAL" \
    --query "StackResources[0].PhysicalResourceId") || return 1
  [ -n "$name" ] || { echo "no function $LOGICAL in stack $STACK; list the deployed ones:" \
                           "./scripts/aws-helper.sh --functions$ENV_ARG" >&2; return 1; }
  echo "/aws/lambda/$name"
}

case "$ACTION" in
  logs)
    args=(--since "$SINCE" --format short)
    [ -z "$FOLLOW" ] || args+=(--follow)
    [ -z "$FILTER" ] || args+=(--filter-pattern "$FILTER")
    if [ -n "$EVERY_FUNCTION" ]; then
      for stack in "$APP" "${APP}-cognito"; do
        functions=$(stack_functions "$stack")
        while IFS=$'\t' read -r logical physical; do
          echo "== $stack $logical =="
          aws logs tail "/aws/lambda/$physical" "${args[@]}"
        done <<< "$functions"
      done
    else
      resolve "$TARGET"
      group=$(log_group)
      aws logs tail "$group" "${args[@]}"
    fi
    ;;
  costs)
    resolve "$TARGET"
    group=$(log_group)
    aws logs tail "$group" --since "$SINCE" --format short --filter-pattern model_call \
      | python3 "$ROOT/scripts/aws_helper.py"
    echo "warning: the dollars use the prices in PRICE_PER_MILLION in src/common/model.py, last" \
         "checked on the date noted there; a provider price change is not seen until that table" \
         "is updated and deployed" >&2
    ;;
  ses-status)
    aws sesv2 list-email-identities --output text \
      --query "EmailIdentities[?IdentityType=='EMAIL_ADDRESS'].[IdentityName,VerificationStatus]"
    production=$(aws sesv2 get-account --query ProductionAccessEnabled --output text)
    if [ "$production" = True ]; then
      echo "SES production access: mail reaches any address"
    else
      echo "SES sandbox: mail reaches only addresses with status SUCCESS"
    fi
    ;;
  ses-verify)
    for email in "${EMAILS[@]}"; do
      aws ses verify-email-identity --email-address "$email"
      echo "verification email sent to $email"
    done
    ;;
  ses-unverify)
    require_params
    for email in "${EMAILS[@]}"; do
      [ "$email" != "$SES_SENDER" ] \
        || { echo "$email is SES_SENDER in scripts/params.sh; deleting it would stop every mail" \
                  "the app sends; pick another address" >&2; exit 1; }
    done
    for email in "${EMAILS[@]}"; do
      aws ses delete-identity --identity "$email"
      echo "deleted the SES identity of $email; restore it:" \
           "./scripts/aws-helper.sh --ses-verify $email"
    done
    ;;
  log-group)
    resolve "$TARGET"
    group=$(log_group)
    echo "$group"
    ;;
  functions)
    for stack in "$APP" "${APP}-cognito"; do
      functions=$(stack_functions "$stack")
      echo "$functions"
    done
    ;;
esac
