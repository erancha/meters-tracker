#!/usr/bin/env bash
# meters-tracker's own options, on the stack pair the branch selects (<stack> and
# <stack>-cognito):
#
#   --errors [--since <age>]    warning and error events of every function of both stacks, each
#                               under a "== <stack> <logical id> ==" line; the quickest health
#                               check
#   --all-logs [--since <age>] [--errors | --filter <pattern>]
#                               the events of every function of both stacks, all of them or only
#                               those the filter keeps
#   --log-group <function>      print the log group a function writes to; <function> as for --logs
#   --costs [--since <age>]     the api function's model calls by UTC day and by kind of call:
#                               calls, tokens in and out, dollars at the prices in
#                               src/common/model.py, which a provider price change does not reach
#                               until the table is updated and deployed
#   --ses-status                each address SES knows with its verification status (SUCCESS,
#                               PENDING, FAILED, ...), then whether the account is still in the
#                               SES sandbox, where mail reaches only a verified address
#   --ses-verify <email>...     mail each address a new verification link, valid 24 hours
#   --ses-unverify <email>...   delete each address's SES identity, so the sandbox refuses mail to
#                               it, e.g. to see a report fail; refuses SES_SENDER of
#                               lib/params.sh, whose removal stops every mail
#   --redirect-uri              print the address Google returns sign-in to, Cognito's
#                               /oauth2/idpresponse on the branch's stack pair, from the account
#                               and region alone, so the Google client can name it before any
#                               deploy (docs/sign-in.md)
#   --set-model-key <anthropic|openai>
#                               store a model provider's API key in SSM, prompting for it without
#                               echo (or reading it from stdin when piped)
#
# <age> is as for --logs (default 1h). SES identities and the model keys belong to the AWS account
# and region, not to a stack, so those options act the same from every branch.
#
# --set-model-key overwrites /meters-tracker/llm/<provider>/api-key. Each provider keeps its own
# key, so both can stay stored and deploy.sh passes the API Lambda the one LLM_MODEL names in
# lib/stack-params.sh. The Lambda reads the parameter on each turn, so replacing a key needs no
# redeploy.
#
# --delete-stack deletes the app stack and then the Cognito stack; it needs --empty while the
# frontend bucket holds the published app. The DynamoDB tables and the files bucket survive the
# delete with their generated names (<stack>-<LogicalId>-<suffix>): the tables are retained and
# deletion-protected, the bucket is retained and versioned, and --empty leaves its objects
# untouched. No option deletes them, and deploy.sh refuses to create the stack again while they
# exist.
# Find them with aws dynamodb list-tables and aws s3api list-buckets, filtered by the stack-name
# prefix, or note them from aws cloudformation describe-stack-resources before deleting.
# Recover: import them into the new stack as a CloudFormation resource import
# (aws cloudformation create-change-set --change-set-type IMPORT with their physical names).
# Remove for good: disable deletion protection (aws dynamodb update-table
# --no-deletion-protection-enabled) and delete each table; empty the bucket including every
# version and delete marker (the console's Empty, or aws s3api delete-objects over
# list-object-versions); then delete the bucket.
#
# Examples:
#   ./scripts/aws-helper.sh --errors --since 1d
#   ./scripts/aws-helper.sh --logs api --since 1d
#   ./scripts/aws-helper.sh --logs presignup --errors --since 1d
#   ./scripts/aws-helper.sh --ses-verify tenant@example.com owner@example.com
#   ./scripts/aws-helper.sh --costs --since 1d
#   ./scripts/aws-helper.sh --set-model-key openai < key.txt

# Python logging writes its levels as ERROR and WARNING; a filter term matches a whole word.
APP_ERRORS_FILTER='?ERROR ?WARNING'

# Prints the events of every function of both stacks, a stack and a function at a time.
all_logs() {
    local since=1h filter="" tail_arguments=() stacks stack index
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --since) need_value "$@"; since="$2"; shift 2 ;;
            --errors | --filter)
                [[ -z "$filter" ]] || usage_error "Give one of --errors and --filter"
                if [[ "$1" == --errors ]]; then
                    filter="$APP_ERRORS_FILTER"
                    shift
                else
                    need_value "$@"
                    filter="$2"
                    shift 2
                fi
                ;;
            *) usage_error "Unknown argument: $1" ;;
        esac
    done
    [[ -z "$filter" ]] || tail_arguments+=(--filter-pattern "$filter")
    connect
    stacks=("${STACK_NAMES[@]}")
    for stack in "${stacks[@]}"; do
        # The helper's lookups read STACK_NAMES, so narrowing it lists one stack's functions.
        local STACK_NAMES=("$stack")
        load_function_log_groups
        for index in "${!FUNCTION_IDS[@]}"; do
            echo "== $stack ${FUNCTION_IDS[$index]} =="
            aws logs tail "${FUNCTION_LOG_GROUPS[$index]}" --region "$REGION" --since "$since" \
                --format short "${tail_arguments[@]}"
        done
    done
}

model_costs() {
    local since=1h group
    case "$#:${1:-}" in
        0:) ;;
        2:--since) since="$2" ;;
        *) usage_error "--costs takes only --since <age>: $*" ;;
    esac
    connect
    resolve_function ApiFunction
    group="$(function_log_group "$FUNCTION_NAME")"
    aws logs tail "$group" --region "$REGION" --since "$since" --format short \
        --filter-pattern model_call | python3 "$SCRIPT_DIR/lib/aws_helper.py"
    warn "The dollars use the prices in PRICE_PER_MILLION in src/common/model.py, last checked" \
        "on the date noted there; a provider price change is not seen until that table is" \
        "updated and deployed" >&2
}

ses_status() {
    local production
    no_arguments --ses-status "$@"
    connect
    aws sesv2 list-email-identities --region "$REGION" --output text --query \
        "EmailIdentities[?IdentityType=='EMAIL_ADDRESS'].[IdentityName,VerificationStatus]"
    production="$(aws sesv2 get-account --region "$REGION" --query ProductionAccessEnabled \
        --output text)"
    if [[ "$production" == True ]]; then
        echo "SES production access: mail reaches any address"
    else
        echo "SES sandbox: mail reaches only addresses with status SUCCESS"
    fi
}

# Usage error unless the option in $1 is followed by addresses only.
need_addresses() {
    local option="$1" email
    shift
    [[ $# -gt 0 ]] || usage_error "$option needs an email address"
    for email in "$@"; do
        [[ "$email" == *@* ]] || usage_error "Not an email address: $email"
    done
}

ses_verify() {
    local email
    need_addresses --ses-verify "$@"
    connect
    for email in "$@"; do
        aws ses verify-email-identity --region "$REGION" --email-address "$email"
        echo "verification email sent to $email"
    done
}

ses_unverify() {
    local email
    need_addresses --ses-unverify "$@"
    connect
    load_params
    require_settings params.sh SES_SENDER
    for email in "$@"; do
        [[ "$email" != "$SES_SENDER" ]] ||
            die "$email is SES_SENDER in scripts/lib/params.sh; deleting it would stop every" \
                "mail the app sends; pick another address"
    done
    for email in "$@"; do
        aws ses delete-identity --region "$REGION" --identity "$email"
        echo "deleted the SES identity of $email; restore it:" \
            "./scripts/aws-helper.sh --ses-verify $email"
    done
}

set_model_key() {
    local parameter key
    [[ $# -eq 1 ]] || usage_error "--set-model-key takes one provider"
    case "$1" in
        anthropic|openai) ;;
        *) usage_error "Not a model provider: $1" ;;
    esac
    connect
    parameter="$(model_key_param "$1")"
    # A last line without a newline still fills key while read returns failure.
    read -rs -p "Model API key: " key || [[ -n "$key" ]]
    echo >&2
    key="$(printf '%s' "$key" | tr -d '\r' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
    [[ -n "$key" ]] || die "No key entered; nothing stored"
    # The key travels on stdin, so it never appears in the process arguments.
    printf '%s' "$key" | aws ssm put-parameter --region "$REGION" --name "$parameter" \
        --type SecureString --overwrite --value file:///dev/stdin >/dev/null
    echo "SSM $parameter updated"
}

# The Cognito domain's address follows from the account, the region and the prefix, as the
# UserPoolDomain of template-cognito.yaml builds it, so it is known before the stack exists.
print_redirect_uri() {
    local account_id domain
    connect
    account_id="$(aws sts get-caller-identity --query Account --output text)"
    domain="$(cognito_domain_prefix)-$account_id.auth.$REGION.amazoncognito.com"
    echo "https://$domain/oauth2/idpresponse"
}

app_option() {
    local option="$1"
    shift
    case "$option" in
        --errors) all_logs --errors "$@" ;;
        --all-logs) all_logs "$@" ;;
        --log-group)
            [[ $# -eq 1 ]] || usage_error "--log-group takes one function"
            connect
            resolve_function "$1"
            function_log_group "$FUNCTION_NAME"
            ;;
        --costs) model_costs "$@" ;;
        --ses-status) ses_status "$@" ;;
        --ses-verify) ses_verify "$@" ;;
        --ses-unverify) ses_unverify "$@" ;;
        --redirect-uri) no_arguments --redirect-uri "$@"; print_redirect_uri ;;
        --set-model-key) set_model_key "$@" ;;
        *) return 1 ;;
    esac
}
