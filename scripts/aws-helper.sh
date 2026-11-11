#!/usr/bin/env bash
#
# AWS operations on the branch's stack or stacks (STACK_NAMES of lib/app-env.sh): the stack the
# current git branch selects, <app> on main and <app>-fb on any other branch, with each suffix of
# STACK_SUFFIXES appended; the app name and region come from lib/app-env.sh too, and an exported
# STACK_NAME wins.
#
#   ./scripts/aws-helper.sh <option>
#
#   --functions                 list the Lambda functions of the branch's stack or stacks:
#                               logical id, name, runtime
#   --logs <function> [--since <age>] [--follow] [--errors | --filter <pattern>]
#                               print a function's log events; <function> is a logical id or a
#                               part of one that no other function shares
#       --since <age>           how far back to start, e.g. 10m, 2h, 1d (default 1h)
#       --follow                keep printing new events as they arrive
#       --errors                only warning and error events
#       --filter <pattern>      only events matching a CloudWatch filter pattern
#   --trim-logs                 delete every stream of the log groups of the branch's stack or
#                               stacks
#   --log-retention [--all]     set the LOG_RETENTION_DAYS of lib/app-env.sh on the log groups of
#                               the branch's stack or stacks, or with --all on every log group of
#                               the region, wherever the retention differs
#   --delete-stack [--empty]    delete the branch's stack or stacks (asks for the name of the
#                               branch's stack), then any log group of their functions that
#                               outlives them; stops before deleting anything, naming the
#                               buckets, when a bucket of theirs holds objects
#       --empty                 empty those buckets first; accepted before or after
#                               --delete-stack
#   --list-resources [--region <region>]
#                               list the region's resources that are not AWS account defaults,
#                               e.g. to spot leftovers of a deleted stack (default: the app's
#                               region)
#   --xray on|off               X-Ray tracing for every Lambda function and REST API stage of
#                               the region, and Contributor Insights for every DynamoDB table
#                               there; it acts on the whole region, not only the branch's stack
#                               or stacks
#   --prune-layers [--yes]      delete the Lambda layer versions of the region that no function
#                               uses, counting every published version of every function; it
#                               never deletes a version a function uses. It lists them with their
#                               creation dates and asks to type prune first, unless --yes is
#                               given; it acts on the whole region, not only the branch's stack
#                               or stacks
#   -h, --help                  show this help
#
# The functions, log groups and buckets of the branch's stack or stacks are the ones
# CloudFormation lists as their resources, plus the log group each of those functions writes to,
# so no option depends on how they are named.
#
# CloudFormation cannot delete a bucket that holds objects, so --delete-stack looks at every
# bucket that is deleted with the branch's stack or stacks before it deletes anything. --empty
# deletes all their objects and, in a versioned bucket, every version and delete marker. A bucket
# the template keeps (DeletionPolicy Retain) outlives its stack, so it is neither looked at nor
# emptied.
#
# Examples:
#   ./scripts/aws-helper.sh --logs <function> --since 1d --errors
#   ./scripts/aws-helper.sh --logs <function> --since 3h --filter '"email sent"'
#   ./scripts/aws-helper.sh --log-retention --all
set -euo pipefail
# A failing command inside $(...) stops the function that runs there, not only the assignment
# that captures its output.
shopt -s inherit_errexit

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/app-env.sh
source "$SCRIPT_DIR/lib/app-env.sh"
# shellcheck source=lib/list-resources.sh
source "$SCRIPT_DIR/lib/list-resources.sh"

# The repo's own options, when it has any. The file defines app_option <option> [<argument>...],
# which returns 0 after handling an option it owns and 1 for any other option. An option checks
# its arguments, then calls connect for the credentials, STACK_NAME, STACK_NAMES and REGION; it
# may call the other functions of this script too. The file's header comment, after a shebang
# line, continues the --help text.
APP_OPTIONS_FILE="$SCRIPT_DIR/lib/aws-helper-app.sh"
HAS_APP_OPTIONS=false
if [[ -f "$APP_OPTIONS_FILE" ]]; then
    HAS_APP_OPTIONS=true
    # shellcheck source=/dev/null
    source "$APP_OPTIONS_FILE"
fi

# Replaces the show_help of lib/app-env.sh, so its usage_error prints this repo's options too.
show_help() {
    print_help
    if $HAS_APP_OPTIONS; then
        echo
        print_help "$APP_OPTIONS_FILE"
    fi
}

# Usage error unless the option in $1 is followed by a value.
need_value() {
    [[ $# -ge 2 ]] || usage_error "$1 needs a value"
}

# Usage error when an option that takes no arguments was given some.
no_arguments() {
    local option="$1"
    shift
    [[ $# -eq 0 ]] || usage_error "$option takes no argument: $*"
}

# Loads the credentials and resolves the stack. Every option calls it after checking its own
# arguments, so a usage error never depends on the credentials file. An option that works on
# another stack than the branch's sets STACK_NAME and STACK_NAMES before the call, and they stay.
connect() {
    local tool
    for tool in aws jq git; do
        command -v "$tool" >/dev/null || die "Required tool not found: $tool"
    done
    load_aws_config
    [[ -v STACK_NAMES ]] || resolve_stack
    REGION="$(get_region)"
}

# Prints the strings of the JSON array an AWS CLI call's query selects, one per line.
aws_list() {
    aws "$@" --region "$REGION" --output json | jq -r '.[]'
}

# Prints "<logical id><tab><physical id>" for every resource of a type in one stack. A resource
# CloudFormation already deleted, which a stack whose deletion failed still lists, is left out.
one_stack_resources() {
    local stack="$1" type="$2"
    aws cloudformation list-stack-resources --region "$REGION" --stack-name "$stack" \
        --output json --query \
        "StackResourceSummaries[?ResourceType=='$type' && ResourceStatus!='DELETE_COMPLETE']" |
        jq -r '.[] | "\(.LogicalResourceId)\t\(.PhysicalResourceId)"'
}

# Prints the same lines for every resource of a type in the stacks.
stack_resources() {
    local type="$1" stack
    for stack in "${STACK_NAMES[@]}"; do
        one_stack_resources "$stack" "$type"
    done
}

# Sets FUNCTION_IDS (logical ids) and FUNCTION_NAMES (deployed names), index-aligned.
load_stack_functions() {
    local text lines line
    text="$(stack_resources AWS::Lambda::Function)"
    split_lines lines "$text"
    FUNCTION_IDS=()
    FUNCTION_NAMES=()
    for line in "${lines[@]}"; do
        FUNCTION_IDS+=("${line%%$'\t'*}")
        FUNCTION_NAMES+=("${line#*$'\t'}")
    done
}

# Prints the log group a function writes to: the one its stack declares for it, or the
# /aws/lambda/<function name> group Lambda creates at the function's first run.
function_log_group() {
    aws lambda get-function-configuration --region "$REGION" --function-name "$1" \
        --query LoggingConfig.LogGroup --output text
}

# Sets FUNCTION_LOG_GROUPS to the log group of each of the stacks' functions.
load_function_log_groups() {
    local name group
    load_stack_functions
    FUNCTION_LOG_GROUPS=()
    for name in "${FUNCTION_NAMES[@]}"; do
        group="$(function_log_group "$name")"
        FUNCTION_LOG_GROUPS+=("$group")
    done
}

# Sets REGION_LOG_GROUPS to a JSON array of {name, days} for every log group of the region; days
# is null for a group that never expires its events.
load_region_log_groups() {
    REGION_LOG_GROUPS="$(aws logs describe-log-groups --region "$REGION" --output json \
        --query 'logGroups[].{name: logGroupName, days: retentionInDays}')"
}

# Sets the named array to the region's log groups that are among the given names. Needs
# REGION_LOG_GROUPS.
existing_log_groups() {
    local target_name="$1" text
    shift
    text="$(jq -r '.[].name | select(IN($ARGS.positional[]))' --args "$@" \
        <<<"$REGION_LOG_GROUPS")"
    split_lines "$target_name" "$text"
}

# Sets LOG_GROUPS to the stacks' log groups: the ones they declare and the one each function
# writes to. A function that has not run yet may have no log group, so only existing ones count.
load_stack_log_groups() {
    local text declared
    text="$(stack_resources AWS::Logs::LogGroup | cut -f2)"
    split_lines declared "$text"
    load_function_log_groups
    load_region_log_groups
    existing_log_groups LOG_GROUPS "${declared[@]}" "${FUNCTION_LOG_GROUPS[@]}"
}

list_functions() {
    no_arguments --functions "$@"
    connect
    local format='%-34s %-52s %s\n' index runtime
    load_stack_functions
    # shellcheck disable=SC2059  # the format is a constant
    printf "$format" 'LOGICAL ID' 'NAME' 'RUNTIME'
    for index in "${!FUNCTION_IDS[@]}"; do
        # A function deployed as a container image has no runtime.
        runtime="$(aws lambda get-function-configuration --region "$REGION" \
            --function-name "${FUNCTION_NAMES[$index]}" --output json \
            --query '{runtime: Runtime, package: PackageType}' |
            jq -r 'if .package == "Image" then "container image" else .runtime end')"
        # shellcheck disable=SC2059
        printf "$format" "${FUNCTION_IDS[$index]}" "${FUNCTION_NAMES[$index]}" "$runtime"
    done
}

# Sets FUNCTION_NAME to the deployed name of the function a target selects: the function whose
# logical id equals it, else the only one whose logical id contains it, ignoring case.
resolve_function() {
    local target="$1" index matched_ids=() matched_names=()
    load_stack_functions
    for index in "${!FUNCTION_IDS[@]}"; do
        if [[ "${FUNCTION_IDS[$index]}" == "$target" ]]; then
            FUNCTION_NAME="${FUNCTION_NAMES[$index]}"
            return
        fi
        if [[ "${FUNCTION_IDS[$index],,}" == *"${target,,}"* ]]; then
            matched_ids+=("${FUNCTION_IDS[$index]}")
            matched_names+=("${FUNCTION_NAMES[$index]}")
        fi
    done
    case "${#matched_ids[@]}" in
        1) FUNCTION_NAME="${matched_names[0]}" ;;
        0) die "No function of ${STACK_NAMES[*]} matches '$target'; list them: $0 --functions" ;;
        *) die "'$target' matches ${#matched_ids[@]} functions (${matched_ids[*]});" \
               "give more of the logical id" ;;
    esac
}

tail_logs() {
    local target since=1h filter="" tail_arguments=() group
    [[ $# -gt 0 ]] || usage_error "--logs needs a function"
    target="$1"
    shift
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --since) need_value "$@"; since="$2"; shift 2 ;;
            --follow) tail_arguments+=(--follow); shift ;;
            --errors | --filter)
                [[ -z "$filter" ]] || usage_error "--logs takes one of --errors and --filter"
                if [[ "$1" == --errors ]]; then
                    # Python logging writes [ERROR] and [WARNING], Node's console methods ERROR
                    # and WARN; a filter term matches a whole word, case-sensitively.
                    filter='?ERROR ?WARN ?WARNING'
                    shift
                else
                    need_value "$@"
                    filter="$2"
                    shift 2
                fi
                ;;
            *) usage_error "Unknown --logs argument: $1" ;;
        esac
    done
    [[ -z "$filter" ]] || tail_arguments+=(--filter-pattern "$filter")
    connect
    resolve_function "$target"
    group="$(function_log_group "$FUNCTION_NAME")"
    info "Log group $group, since $since" >&2
    aws logs tail "$group" --region "$REGION" --since "$since" --format short \
        "${tail_arguments[@]}"
}

trim_logs() {
    no_arguments --trim-logs "$@"
    connect
    local group text streams stream
    load_stack_log_groups
    step "Deleting the log streams of ${STACK_NAMES[*]}: ${#LOG_GROUPS[@]} log group(s)"
    for group in "${LOG_GROUPS[@]}"; do
        text="$(aws_list logs describe-log-streams --log-group-name "$group" \
            --query 'logStreams[].logStreamName')"
        split_lines streams "$text"
        echo "Deleting ${#streams[@]} stream(s) of $group"
        for stream in "${streams[@]}"; do
            aws logs delete-log-stream --region "$REGION" --log-group-name "$group" \
                --log-stream-name "$stream"
        done
    done
}

log_retention() {
    local all=false scope text groups differing group
    case "$#:${1:-}" in
        0:) ;;
        1:--all) all=true ;;
        *) usage_error "--log-retention takes only --all: $*" ;;
    esac
    connect
    if $all; then
        scope="region $REGION"
        load_region_log_groups
        text="$(jq -r '.[].name' <<<"$REGION_LOG_GROUPS")"
        split_lines groups "$text"
    else
        scope="${STACK_NAMES[*]}"
        load_stack_log_groups
        groups=("${LOG_GROUPS[@]}")
    fi
    text="$(jq -r --argjson days "$LOG_RETENTION_DAYS" \
        '.[] | select(.days != $days and (.name | IN($ARGS.positional[]))) | .name' \
        --args "${groups[@]}" <<<"$REGION_LOG_GROUPS")"
    split_lines differing "$text"
    step "Retention of $LOG_RETENTION_DAYS day(s) for $scope:" \
        "${#differing[@]} of ${#groups[@]} log group(s) differ"
    for group in "${differing[@]}"; do
        echo "Retention $LOG_RETENTION_DAYS day(s): $group"
        aws logs put-retention-policy --region "$REGION" --log-group-name "$group" \
            --retention-in-days "$LOG_RETENTION_DAYS"
    done
}

# S3 deletes at most this many objects in one call.
S3_DELETE_BATCH=1000

# Prints up to <limit> of the object versions and delete markers a bucket holds, as the JSON
# document s3api delete-objects takes. An object of a bucket that never had versioning carries
# the version id "null", so the listing covers both kinds of bucket.
bucket_batch() {
    local bucket="$1" limit="$2"
    # Versions and DeleteMarkers are absent from the listing when the bucket has none.
    aws s3api list-object-versions --region "$REGION" --bucket "$bucket" --max-items "$limit" \
        --output json --query '{Versions: Versions, DeleteMarkers: DeleteMarkers}' |
        jq -c --argjson limit "$limit" \
            '{Objects: ((.Versions // []) + (.DeleteMarkers // []) | .[:$limit]
                | map({Key, VersionId})), Quiet: true}'
}

# Deletes everything a bucket holds: each round lists a batch from the start of the bucket and
# deletes it, until a round lists nothing.
empty_bucket() {
    local bucket="$1" batch count errors
    while true; do
        batch="$(bucket_batch "$bucket" "$S3_DELETE_BATCH")"
        count="$(jq '.Objects | length' <<<"$batch")"
        [[ "$count" -gt 0 ]] || break
        echo "Deleting $count object version(s) of s3://$bucket"
        # The batch travels on stdin: a thousand keys can exceed the size of one argument.
        errors="$(aws s3api delete-objects --region "$REGION" --bucket "$bucket" \
            --delete file:///dev/stdin --output json --query 'Errors' <<<"$batch")"
        [[ "$errors" == null ]] ||
            die "S3 refused to delete objects of s3://$bucket: $(jq -c '.[:3]' <<<"$errors")"
    done
}

# Sets DELETED_BUCKETS to the buckets CloudFormation deletes with the stacks: their
# AWS::S3::Bucket resources whose template entry has no DeletionPolicy, or Delete. A bucket the
# template retains outlives its stack with its objects, so it is named and left out.
load_deleted_buckets() {
    local stack text lines line logical_id bucket template policy
    DELETED_BUCKETS=()
    for stack in "${STACK_NAMES[@]}"; do
        text="$(one_stack_resources "$stack" AWS::S3::Bucket)"
        split_lines lines "$text"
        [[ ${#lines[@]} -gt 0 ]] || continue
        # The processed template is the one CloudFormation acts on, after any transform.
        template="$(aws cloudformation get-template --region "$REGION" --stack-name "$stack" \
            --template-stage Processed --output json --query TemplateBody)"
        for line in "${lines[@]}"; do
            logical_id="${line%%$'\t'*}"
            bucket="${line#*$'\t'}"
            policy="$(jq -r --arg id "$logical_id" \
                '.Resources[$id] | if has("DeletionPolicy") then .DeletionPolicy | tojson
                 else "\"Delete\"" end' <<<"$template")"
            case "$policy" in
                '"Delete"') DELETED_BUCKETS+=("$bucket") ;;
                '"Retain"' | '"RetainExceptOnCreate"')
                    echo "Keeping s3://$bucket ($logical_id of $stack): the template retains it" ;;
                *) die "Unexpected DeletionPolicy $policy of $logical_id in stack '$stack'" ;;
            esac
        done
    done
}

delete_stack() {
    local empty=false
    case "$#:${1:-}" in
        0:) ;;
        1:--empty) empty=true ;;
        *) usage_error "--delete-stack takes only --empty: $*" ;;
    esac
    connect
    local confirmation stack leftovers group bucket batch holding=()
    # Read while the stacks still list their functions.
    load_function_log_groups
    echo "This deletes: ${STACK_NAMES[*]}"
    read -r -p "Type the stack name '$STACK_NAME' to confirm: " confirmation
    [[ "$confirmation" == "$STACK_NAME" ]] || die "Confirmation did not match; nothing deleted."
    load_deleted_buckets
    if $empty; then
        for bucket in "${DELETED_BUCKETS[@]}"; do
            step "Emptying s3://$bucket"
            empty_bucket "$bucket"
        done
    else
        for bucket in "${DELETED_BUCKETS[@]}"; do
            batch="$(bucket_batch "$bucket" 1)"
            [[ "$(jq '.Objects | length' <<<"$batch")" -eq 0 ]] || holding+=("s3://$bucket")
        done
        [[ ${#holding[@]} -eq 0 ]] ||
            die "Nothing deleted: ${holding[*]} of ${STACK_NAMES[*]} hold(s) objects, and a" \
                "bucket that holds objects cannot be deleted. Pass --empty together with" \
                "--delete-stack to delete them too: $0 --delete-stack --empty"
    fi
    for stack in "${STACK_NAMES[@]}"; do
        step "Deleting stack '$stack'"
        aws cloudformation delete-stack --region "$REGION" --stack-name "$stack"
        aws cloudformation wait stack-delete-complete --region "$REGION" --stack-name "$stack"
    done
    # A log group Lambda created by itself belongs to no stack, and a function that logs while
    # its stack is being deleted creates its group again.
    load_region_log_groups
    existing_log_groups leftovers "${FUNCTION_LOG_GROUPS[@]}"
    for group in "${leftovers[@]}"; do
        echo "Deleting log group: $group"
        aws logs delete-log-group --region "$REGION" --log-group-name "$group"
    done
    ok "Deleted ${STACK_NAMES[*]}."
}

list_region_resources() {
    local region=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --region) need_value "$@"; region="$2"; shift 2 ;;
            *) usage_error "Unknown --list-resources argument: $1" ;;
        esac
    done
    connect
    [[ -n "$region" ]] || region="$REGION"
    step "Listing the resources of region $region"
    list_resources "$region"
}

# The update calls answer with the whole changed resource, a function's environment variables
# included, so their output is discarded.
toggle_xray() {
    local tracing_mode tracing_enabled insights_verb text names name stages stage
    case "$#:${1:-}" in
        1:on) tracing_mode=Active; tracing_enabled=true; insights_verb=enable ;;
        1:off) tracing_mode=PassThrough; tracing_enabled=false; insights_verb=disable ;;
        *) usage_error "--xray takes on or off" ;;
    esac
    connect

    step "Lambda tracing $tracing_mode for every function of region $REGION"
    text="$(aws_list lambda list-functions --query 'Functions[].FunctionName')"
    split_lines names "$text"
    for name in "${names[@]}"; do
        echo "Lambda function: $name"
        aws lambda update-function-configuration --region "$REGION" --function-name "$name" \
            --tracing-config "Mode=$tracing_mode" >/dev/null
    done

    step "Stage tracing $tracing_enabled for every REST API of region $REGION"
    text="$(aws_list apigateway get-rest-apis --query 'items[].id')"
    split_lines names "$text"
    for name in "${names[@]}"; do
        text="$(aws_list apigateway get-stages --rest-api-id "$name" --query 'item[].stageName')"
        split_lines stages "$text"
        for stage in "${stages[@]}"; do
            echo "REST API stage: $name/$stage"
            aws apigateway update-stage --region "$REGION" --rest-api-id "$name" \
                --stage-name "$stage" \
                --patch-operations "op=replace,path=/tracingEnabled,value=$tracing_enabled" \
                >/dev/null
        done
    done

    step "Contributor Insights $1 for every DynamoDB table of region $REGION"
    text="$(aws_list dynamodb list-tables --query 'TableNames')"
    split_lines names "$text"
    for name in "${names[@]}"; do
        echo "DynamoDB table: $name"
        aws dynamodb "$insights_verb-contributor-insights" --region "$REGION" \
            --table-name "$name" >/dev/null
    done
    ok "X-Ray tracing is $1 in region $REGION."
}

# Prints "<version ARN><tab><creation date>" for every version of every Lambda layer of the
# region. The AWS CLI follows every page of a listing by itself, here and in prune_layers.
region_layer_versions() {
    local text layers layer
    text="$(aws_list lambda list-layers --query 'Layers[].LayerName')"
    split_lines layers "$text"
    for layer in "${layers[@]}"; do
        aws lambda list-layer-versions --region "$REGION" --layer-name "$layer" --output json \
            --query 'LayerVersions[].[LayerVersionArn, CreatedDate]' | jq -r '.[] | @tsv'
    done
}

prune_layers() {
    local yes=false
    case "$#:${1:-}" in
        0:) ;;
        1:--yes) yes=true ;;
        *) usage_error "--prune-layers takes only --yes: $*" ;;
    esac
    connect
    local text arns versions line arn name confirmation unused=()
    # Key: a layer version ARN some function uses.
    local -A used=()
    step "Lambda layer versions no function of region $REGION uses"
    # A published function version keeps the layer versions it was published with, so every
    # version counts, not only the $LATEST one.
    text="$(aws_list lambda list-functions --function-version ALL \
        --query 'Functions[].Layers[].Arn')"
    split_lines arns "$text"
    for arn in "${arns[@]}"; do
        used["$arn"]=1
    done
    text="$(region_layer_versions)"
    split_lines versions "$text"
    for line in "${versions[@]}"; do
        [[ -v used["${line%%$'\t'*}"] ]] || unused+=("$line")
    done
    if [[ ${#unused[@]} -eq 0 ]]; then
        ok "Every one of the ${#versions[@]} layer version(s) of region $REGION is in use."
        return
    fi
    echo "${#unused[@]} of ${#versions[@]} layer version(s) are used by no function:"
    for line in "${unused[@]}"; do
        arn="${line%%$'\t'*}"
        echo "  ${arn#*:layer:}  created ${line#*$'\t'}"
    done
    if ! $yes; then
        read -r -p "Type 'prune' to delete them: " confirmation
        [[ "$confirmation" == prune ]] || die "Confirmation did not match; nothing deleted."
    fi
    for line in "${unused[@]}"; do
        name="${line%%$'\t'*}"
        name="${name#*:layer:}"
        echo "Deleting layer version: $name"
        aws lambda delete-layer-version --region "$REGION" --layer-name "${name%:*}" \
            --version-number "${name##*:}"
    done
    ok "Deleted ${#unused[@]} layer version(s) of region $REGION."
}

[[ $# -gt 0 ]] || usage_error "No option given"
OPTION="$1"
shift
case "$OPTION" in
    -h | --help) show_help ;;
    --functions) list_functions "$@" ;;
    --logs) tail_logs "$@" ;;
    --trim-logs) trim_logs "$@" ;;
    --log-retention) log_retention "$@" ;;
    --delete-stack) delete_stack "$@" ;;
    --empty)
        [[ "$#:${1:-}" == 1:--delete-stack ]] ||
            usage_error "--empty is only accepted together with --delete-stack"
        delete_stack --empty
        ;;
    --list-resources) list_region_resources "$@" ;;
    --xray) toggle_xray "$@" ;;
    --prune-layers) prune_layers "$@" ;;
    *)
        $HAS_APP_OPTIONS || usage_error "Unknown option: $OPTION"
        # app_option runs as a plain command, so a failing command inside it stops the script
        # as it would anywhere else. Only its own return of 1, for an option it does not own,
        # reaches this trap: bash runs a caller's ERR trap for no command inside a function.
        trap '[[ $? -ne 1 ]] || usage_error "Unknown option: $OPTION"' ERR
        app_option "$OPTION" "$@"
        ;;
esac
