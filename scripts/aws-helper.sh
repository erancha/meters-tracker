#!/usr/bin/env bash
# Tail or locate the CloudWatch logs of the deployed Lambda functions, or sum the model spend.
#
#   ./scripts/aws-helper.sh --logs <target> [env] [--since 1h] [--follow]
#                           [--errors | --filter <pattern>]
#   ./scripts/aws-helper.sh --costs [env] [--since 1h]
#   ./scripts/aws-helper.sh --log-group <target> [env]
#   ./scripts/aws-helper.sh --functions [env]
#
# env: optional suffix (e.g. dev) naming the stack pair, as in deploy.sh.
# CloudFormation appends random suffixes to Lambda names, so a target is resolved to the real
# resource: an alias (api, presignup) or <stack>:<logical-id> for any Lambda the configured
# credentials can describe.
#
# Options:
#   --logs <target>       Tail the function's CloudWatch log group
#   --log-group <target>  Print the function's log group path
#   --functions           List the deployed Lambda functions of both stacks
#   --costs               Sum the api function's model calls by UTC day and by kind of call:
#                         calls, tokens in and out, dollars at the prices in
#                         src/common/model.py, which a provider price change does not reach
#                         until the table is updated and deployed
#   --since <duration>    How far back to read logs (aws logs tail syntax; default 1h)
#   --follow              Keep streaming new log events
#   --errors              Show only warning and error events (a --filter preset)
#   --filter <pattern>    CloudWatch filter pattern, e.g. '"reading saved"'
#   -h, --help            Show this usage
#
#   ./scripts/aws-helper.sh --logs api --since 1d
#   ./scripts/aws-helper.sh --logs presignup --errors --since 1d
#   ./scripts/aws-helper.sh --logs api dev --errors
#   ./scripts/aws-helper.sh --log-group api
#   ./scripts/aws-helper.sh --costs --since 1d
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=common.sh
source "$ROOT/scripts/common.sh"

ACTION="" TARGET="" SINCE="1h" FOLLOW="" FILTER="" ENV_ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --logs|--log-group|--since|--filter)
      [ $# -ge 2 ] || { print_help >&2; exit 2; } ;;
  esac
  case "$1" in
    --logs)      ACTION=logs; TARGET="$2"; shift 2 ;;
    --log-group) ACTION=log-group; TARGET="$2"; shift 2 ;;
    --functions) ACTION=functions; shift ;;
    --costs)     ACTION=costs; TARGET=api; shift ;;
    --since)     SINCE="$2"; shift 2 ;;
    --follow)    FOLLOW=1; shift ;;
    --errors)    FILTER="?ERROR ?WARNING"; shift ;;
    --filter)    FILTER="$2"; shift 2 ;;
    -h|--help)   print_help; exit 0 ;;
    -*)          print_help >&2; exit 2 ;;
    *)           ENV_ARGS+=("$1"); shift ;;
  esac
done
[ -n "$ACTION" ] || { print_help >&2; exit 2; }
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
    resolve "$TARGET"
    args=(--since "$SINCE" --format short)
    [ -z "$FOLLOW" ] || args+=(--follow)
    [ -z "$FILTER" ] || args+=(--filter-pattern "$FILTER")
    group=$(log_group)
    aws logs tail "$group" "${args[@]}"
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
  log-group)
    resolve "$TARGET"
    group=$(log_group)
    echo "$group"
    ;;
  functions)
    for stack in "$APP" "${APP}-cognito"; do
      functions=$(cfn_query describe-stack-resources "$stack" \
        --query "StackResources[?ResourceType=='AWS::Lambda::Function'].\
[LogicalResourceId,PhysicalResourceId]")
      [ -n "$functions" ] || { echo "stack $stack has no functions or does not exist; deploy it:" \
                                    "./scripts/deploy.sh$ENV_ARG" >&2; exit 1; }
      echo "$functions"
    done
    ;;
esac
