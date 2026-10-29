#!/usr/bin/env bash
# Tail or locate the CloudWatch logs of the deployed Lambda functions.
#
#   ./scripts/aws-helper.sh --logs <target> [--since 1h] [--follow] [--errors | --filter <pattern>]
#   ./scripts/aws-helper.sh --log-group <target>
#   ./scripts/aws-helper.sh --functions
#
# CloudFormation appends random suffixes to Lambda names, so a target is resolved to the real
# resource: an alias (api, presignup) or <stack>:<logical-id> for any Lambda the configured
# credentials can describe.
#
# Options:
#   --logs <target>       Tail the function's CloudWatch log group
#   --log-group <target>  Print the function's log group path
#   --functions           List the deployed Lambda functions of both stacks
#   --since <duration>    How far back to read logs (aws logs tail syntax; default 1h)
#   --follow              Keep streaming new log events
#   --errors              Show only warning and error events (a --filter preset)
#   --filter <pattern>    CloudWatch filter pattern, e.g. '"reading saved"'
#   --env <suffix>        Target an isolated stack pair (same suffix as deploy.sh)
#   -h, --help            Show this usage
#
#   ./scripts/aws-helper.sh --logs api --since 1d
#   ./scripts/aws-helper.sh --logs presignup --errors --since 1d
#   ./scripts/aws-helper.sh --log-group api
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=common.sh
source "$ROOT/scripts/common.sh"

ACTION="" TARGET="" SINCE="1h" FOLLOW="" FILTER="" ENV_SUFFIX=""
while [ $# -gt 0 ]; do
  case "$1" in
    --logs|--log-group|--since|--filter|--env)
      [ $# -ge 2 ] || { print_help >&2; exit 2; } ;;
  esac
  case "$1" in
    --logs)      ACTION=logs; TARGET="$2"; shift 2 ;;
    --log-group) ACTION=log-group; TARGET="$2"; shift 2 ;;
    --functions) ACTION=functions; shift ;;
    --since)     SINCE="$2"; shift 2 ;;
    --follow)    FOLLOW=1; shift ;;
    --errors)    FILTER="?ERROR ?WARNING"; shift ;;
    --filter)    FILTER="$2"; shift 2 ;;
    --env)       ENV_SUFFIX="-$2"; shift 2 ;;
    -h|--help)   print_help; exit 0 ;;
    *)           print_help >&2; exit 2 ;;
  esac
done
[ -n "$ACTION" ] || { print_help >&2; exit 2; }

require_aws_config
APP="meters-tracker${ENV_SUFFIX}"

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

log_group() {
  aws cloudformation describe-stack-resources --stack-name "$STACK" \
    --logical-resource-id "$LOGICAL" \
    --query "StackResources[0].PhysicalResourceId" --output text \
    | sed 's|^|/aws/lambda/|'
}

case "$ACTION" in
  logs)
    resolve "$TARGET"
    args=(--since "$SINCE" --format short)
    [ -z "$FOLLOW" ] || args+=(--follow)
    [ -z "$FILTER" ] || args+=(--filter-pattern "$FILTER")
    aws logs tail "$(log_group)" "${args[@]}"
    ;;
  log-group)
    resolve "$TARGET"
    log_group
    ;;
  functions)
    for stack in "$APP" "${APP}-cognito"; do
      aws cloudformation describe-stack-resources --stack-name "$stack" \
        --query "StackResources[?ResourceType=='AWS::Lambda::Function'].\
[LogicalResourceId,PhysicalResourceId]" \
        --output text
    done
    ;;
esac
