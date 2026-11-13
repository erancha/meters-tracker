#!/usr/bin/env bash
# Parameter values the two templates are deployed with, for both branch stack pairs. Sourced (never
# executed) after lib/app-env.sh and its connect_stack.
#
# Core responsibilities:
# - LLM_MODEL, the model the app stack calls, and the SSM parameter of its provider's API key
# - cognito_stack_parameters / app_stack_parameters: fill COGNITO_PARAMETERS / APP_PARAMETERS
#   with the Key=Value list for aws cloudformation deploy; the Google OAuth client comes from
#   the gitignored lib/secrets.sh, the addresses and the sign-up allowed list from the
#   gitignored lib/params.sh

load_secrets
require_settings secrets.sh GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET
load_params
require_settings params.sh ALLOWED_EMAILS SES_SENDER ADMIN_EMAIL

# The model that reads photos and classifies messages, as provider:model with an anthropic or
# openai provider; it must take image input. The provider's API key is not a deploy parameter;
# store it with scripts/aws-helper.sh --set-model-key before deploying.
LLM_MODEL="anthropic:claude-opus-5-5"
# The OpenAI alternative: as of 2026-11, gpt-6.1-sol's token prices are half of claude-opus-5-5's.
# LLM_MODEL="openai:gpt-6.1-sol"
LLM_PROVIDER="${LLM_MODEL%%:*}"
LLM_KEY_PARAM="$(model_key_param "$LLM_PROVIDER")"

# aws cloudformation deploy reuses a parameter's previous stack value whenever the overrides leave
# it out, so a parameter must be listed here to be under this file's control.

cognito_stack_parameters() {  # callback-urls (comma-separated)
    COGNITO_PARAMETERS=(
        "GoogleClientId=$GOOGLE_CLIENT_ID"
        "GoogleClientSecret=$GOOGLE_CLIENT_SECRET"
        "CallbackUrls=$1"
        "DomainPrefix=$(cognito_domain_prefix)"
        "AllowedEmails=$ALLOWED_EMAILS"
        "AdminEmail=$ADMIN_EMAIL"
        "LogRetentionDays=$LOG_RETENTION_DAYS"
    )
}

app_stack_parameters() {  # allowed-origins (comma-separated) user-pool-id user-pool-client-id
    APP_PARAMETERS=(
        "AllowedOrigins=$1"
        "UserPoolId=$2"
        "UserPoolClientId=$3"
        "LlmModel=$LLM_MODEL"
        "LlmKeyParam=$LLM_KEY_PARAM"
        "SesSender=$SES_SENDER"
        "AdminEmail=$ADMIN_EMAIL"
        "LogRetentionDays=$LOG_RETENTION_DAYS"
    )
}
