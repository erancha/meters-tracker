#!/usr/bin/env bash
# Deploy the Cognito stack and the app stack, and generate frontend/public/config.js.
#
#   ./scripts/deploy.sh [env]
#
# env: optional suffix (e.g. dev) that deploys an isolated stack pair, meters-tracker-dev and
# meters-tracker-dev-cognito, beside the default one; the Cognito domain prefix varies with it
# because domains are global.
# The Cognito callback URL and the API's CORS origin need the CloudFront URL, which exists only
# after the app stack's first deploy, so each run converges: it deploys with what is known, then
# deploys both stacks again with the now-known URL (a no-op when unchanged).
# It ends by publishing the frontend with sync-frontend.sh.
# Requires scripts/aws-config.sh and scripts/params.sh (create them with scripts/init-env.sh),
# and the API key of the provider LLM_MODEL names (store it with scripts/set-model-key.sh).
#
#   ./scripts/deploy.sh dev
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=common.sh
source "$ROOT/scripts/common.sh"

parse_env_suffix_arg "$@"
require_aws_config
require_params
PROVIDER="${LLM_MODEL%%:*}"
require_model_key "$PROVIDER"
KEY_PARAM=$(model_key_param "$PROVIDER")
require_venv

BUILD="$ROOT/build"

# delete-stack.sh leaves the tables and the files bucket behind under generated names; a new
# stack of the same name would get empty replacements and orphan the stored data. The patterns
# match the generated names exactly, so a stack whose name extends this one (-dev) is not hit.
existing=$(stack_query "$APP" "Stacks[0].StackName")
if [ -z "$existing" ]; then
  tables=$(aws dynamodb list-tables --query 'TableNames[]' --output text)
  buckets=$(aws s3api list-buckets --query 'Buckets[].Name' --output text)
  retained=$(tr '\t' '\n' <<<"$tables"$'\n'"$buckets" \
    | grep -E "^${APP}-([A-Za-z]+Table-[A-Z0-9]+|filesbucket-[a-z0-9]+)$" || true)
  if [ -n "$retained" ]; then
    echo "$APP does not exist, but these tables and buckets of an earlier stack of that name do:" \
      >&2
    echo "$retained" >&2
    echo "Deploying would create empty replacements and orphan their data. Import them into the" \
         "new stack or delete them first; see ./scripts/delete-stack.sh --help" >&2
    exit 1
  fi
fi

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
ARTIFACTS_BUCKET="${APP}-artifacts-${ACCOUNT}"
aws s3api head-bucket --bucket "$ARTIFACTS_BUCKET" 2>/dev/null \
  || aws s3 mb "s3://${ARTIFACTS_BUCKET}"

rm -rf "$BUILD/lambda"
mkdir -p "$BUILD/lambda"
"$ROOT/.venv/bin/pip" install --quiet --disable-pip-version-check -r "$ROOT/requirements.txt" \
  --platform manylinux2014_aarch64 --only-binary=:all: --python-version 3.13 -t "$BUILD/lambda"
cp -r "$ROOT/src/." "$BUILD/lambda/"
find "$BUILD/lambda" -type d -name __pycache__ -prune -exec rm -rf {} +
cp "$ROOT/config/app.json" "$BUILD/lambda/"

deploy_cognito() {  # frontend-url
  local callbacks="http://localhost:8000/"
  [ -n "$1" ] && callbacks="${callbacks},$1/"
  aws cloudformation package --template-file "$ROOT/scripts/template-cognito.yaml" \
    --s3-bucket "$ARTIFACTS_BUCKET" --output-template-file "$BUILD/template-cognito.pkg.yaml"
  # The Google client secret is passed on the command line because cloudformation deploy has no
  # file form for parameter values, so it is visible in process arguments while the call runs.
  aws cloudformation deploy --template-file "$BUILD/template-cognito.pkg.yaml" \
    --stack-name "${APP}-cognito" --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND \
    --no-fail-on-empty-changeset \
    --parameter-overrides GoogleClientId="$GOOGLE_CLIENT_ID" \
      GoogleClientSecret="$GOOGLE_CLIENT_SECRET" CallbackUrls="$callbacks" \
      DomainPrefix="meters-trk${ENV_SUFFIX}"
}

deploy_main() {  # frontend-url
  local origins="http://localhost:8000" user_pool_id user_pool_client_id
  [ -n "$1" ] && origins="${origins},$1"
  user_pool_id=$(require_output "${APP}-cognito" UserPoolId)
  user_pool_client_id=$(require_output "${APP}-cognito" UserPoolClientId)
  aws cloudformation package --template-file "$ROOT/scripts/template.yaml" \
    --s3-bucket "$ARTIFACTS_BUCKET" --output-template-file "$BUILD/template.pkg.yaml"
  aws cloudformation deploy --template-file "$BUILD/template.pkg.yaml" \
    --stack-name "$APP" --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND \
    --no-fail-on-empty-changeset \
    --parameter-overrides AllowedOrigins="$origins" UserPoolId="$user_pool_id" \
      UserPoolClientId="$user_pool_client_id" LlmModel="$LLM_MODEL" LlmKeyParam="$KEY_PARAM" \
      SesSender="$SES_SENDER"
}

FRONTEND_URL=$(stack_output "$APP" FrontendUrl)
deploy_cognito "$FRONTEND_URL"
deploy_main "$FRONTEND_URL"

CONVERGED_URL=$(require_output "$APP" FrontendUrl)
if [ "$CONVERGED_URL" != "$FRONTEND_URL" ]; then
  deploy_cognito "$CONVERGED_URL"
  deploy_main "$CONVERGED_URL"
fi

COGNITO_DOMAIN=$(require_output "${APP}-cognito" HostedUiDomain)
CLIENT_ID=$(require_output "${APP}-cognito" UserPoolClientId)
API_URL=$(require_output "$APP" ApiUrl)
mkdir -p "$ROOT/frontend/public"
cat > "$ROOT/frontend/public/config.js" <<CONFIG
// Generated by scripts/deploy.sh — do not edit by hand.
window.CONFIG = {
  cognitoDomain: "$COGNITO_DOMAIN",
  clientId: "$CLIENT_ID",
  apiUrl: "$API_URL",
  redirectUri: window.location.origin + "/",
};
CONFIG

[ -x "$ROOT/scripts/sync-frontend.sh" ] && "$ROOT/scripts/sync-frontend.sh" "${1:-}"
echo "Deployed. Frontend: ${CONVERGED_URL}"
