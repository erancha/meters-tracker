#!/usr/bin/env bash
#
# Build and deploy the app to the stack pair the current git branch selects: main deploys
# "meters-tracker" and "meters-tracker-cognito", any other branch "meters-tracker-fb" and
# "meters-tracker-fb-cognito", an isolated pair beside them.
#
#   ./scripts/deploy.sh                    backend: the Lambda package, the Cognito stack, the app
#                                          stack, then frontend/public/config.js
#   ./scripts/deploy.sh --frontend         backend, then the frontend
#   ./scripts/deploy.sh --frontend-only    frontend: npm ci, npm run build, S3 sync, CloudFront
#                                          invalidation
#   -h, --help                             show this help
#
# Backend: the Cognito callback URL and the API's CORS origin need the CloudFront URL, which
# exists only after the app stack's first deploy, so each run converges: it deploys both stacks
# with the URL known at its start, then deploys both again when the app stack's URL differs. The
# Lambda package is byte-identical for an unchanged source (zip_lambda.py), so such a run leaves
# the functions untouched. It refuses to create the app stack while tables or a files bucket of
# an earlier stack of that name remain (./scripts/aws-helper.sh --help explains their recovery).
# Stack parameters come from lib/stack-params.sh and the gitignored lib/secrets.sh and
# lib/params.sh (create them and lib/aws-config.sh with scripts/init-env.sh); the API key of the
# provider LLM_MODEL names must be stored (./scripts/aws-helper.sh --set-model-key).
#
# A backend run ends by writing frontend/public/config.js for the stack pair from its outputs
# (lib/app-config.sh), and the frontend publish writes it again first, so a published config.js
# always targets this stack's API.
#
# Frontend: publishes the Vite build, config/app.json and docs/demo/demo.html to the frontend
# bucket. The browser reads config.js and app.json from its own origin, so the bundle is the same
# for every stack. Hashed bundle files are cached for a year; everything under a stable name is
# revalidated on every visit, then the CloudFront cache is invalidated. Needs lib/aws-config.sh
# and, for the admin's address in config.js, lib/params.sh.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/app-env.sh
source "$SCRIPT_DIR/lib/app-env.sh"

BUILD="$APP_FOLDER/build"
LOCAL_FRONTEND_URL='http://localhost:8000'

DEPLOY_BACKEND=true
DEPLOY_FRONTEND=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        -h | --help) print_help; exit 0 ;;
        --frontend) DEPLOY_FRONTEND=true; shift ;;
        --frontend-only) DEPLOY_BACKEND=false; DEPLOY_FRONTEND=true; shift ;;
        *) usage_error "Unknown argument: $1" ;;
    esac
done

connect_stack
step "deploy.sh: stacks '$STACK_NAME' and '$COGNITO_STACK_NAME' (branch '$BRANCH')"

# aws-helper.sh --delete-stack leaves the tables and the files bucket behind under generated
# names; a new stack of the same name would get empty replacements and orphan the stored data.
# The patterns match the generated names exactly, so a stack whose name extends this one (-fb)
# is not hit.
refuse_retained_leftovers() {
    local existing tables buckets retained
    existing=$(stack_query "$STACK_NAME" "Stacks[0].StackName")
    [ -z "$existing" ] || return 0
    tables=$(aws dynamodb list-tables --query 'TableNames[]' --output text)
    buckets=$(aws s3api list-buckets --query 'Buckets[].Name' --output text)
    retained=$(tr '\t' '\n' <<<"$tables"$'\n'"$buckets" |
        grep -E "^${STACK_NAME}-([A-Za-z]+Table-[A-Z0-9]+|filesbucket-[a-z0-9]+)$" || true)
    if [ -n "$retained" ]; then
        echo "$STACK_NAME does not exist, but these tables and buckets of an earlier stack of" \
            "that name do:" >&2
        echo "$retained" >&2
        die "Deploying would create empty replacements and orphan their data. Import them into" \
            "the new stack or delete them first; see ./scripts/aws-helper.sh --help"
    fi
}

build_lambda_package() {
    step "Building the Lambda package"
    rm -rf "$BUILD/lambda"
    mkdir -p "$BUILD/lambda"
    "$APP_FOLDER/.venv/bin/pip" install --quiet --disable-pip-version-check \
        -r "$APP_FOLDER/requirements.txt" --platform manylinux2014_aarch64 --only-binary=:all: \
        --python-version 3.13 -t "$BUILD/lambda"
    cp -r "$APP_FOLDER/src/." "$BUILD/lambda/"
    find "$BUILD/lambda" -type d -name __pycache__ -prune -exec rm -rf {} +
    cp "$APP_FOLDER/config/app.json" "$BUILD/lambda/"
    "$APP_FOLDER/.venv/bin/python" "$SCRIPT_DIR/zip_lambda.py" "$BUILD/lambda" "$BUILD/lambda.zip"
}

# Uploads the Lambda package a template names to the artifacts bucket and deploys the stack.
# The parameter values travel on the command line because cloudformation deploy has no file form
# for them, so the Google client secret is visible in process arguments while the call runs.
deploy_stack() {  # template-name stack parameter...
    local template="$1" stack="$2"
    shift 2
    step "Deploying stack '$stack'"
    aws cloudformation package --template-file "$SCRIPT_DIR/$template.yaml" \
        --s3-bucket "$ARTIFACTS_BUCKET" --output-template-file "$BUILD/$template.pkg.yaml"
    aws cloudformation deploy --template-file "$BUILD/$template.pkg.yaml" \
        --stack-name "$stack" --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND \
        --no-fail-on-empty-changeset --parameter-overrides "$@"
}

deploy_cognito() {  # frontend-url
    local callbacks="$LOCAL_FRONTEND_URL/"
    [ -n "$1" ] && callbacks="${callbacks},$1/"
    cognito_stack_parameters "$callbacks"
    deploy_stack template-cognito "$COGNITO_STACK_NAME" "${COGNITO_PARAMETERS[@]}"
}

deploy_app() {  # frontend-url
    local origins="$LOCAL_FRONTEND_URL" user_pool_id user_pool_client_id
    [ -n "$1" ] && origins="${origins},$1"
    user_pool_id=$(require_output "$COGNITO_STACK_NAME" UserPoolId)
    user_pool_client_id=$(require_output "$COGNITO_STACK_NAME" UserPoolClientId)
    app_stack_parameters "$origins" "$user_pool_id" "$user_pool_client_id"
    deploy_stack template "$STACK_NAME" "${APP_PARAMETERS[@]}"
}

deploy_backend() {
    local account frontend_url converged_url
    # shellcheck source=lib/stack-params.sh
    source "$SCRIPT_DIR/lib/stack-params.sh"
    require_model_key "$LLM_PROVIDER"
    require_venv
    refuse_retained_leftovers

    account=$(aws sts get-caller-identity --query Account --output text)
    ARTIFACTS_BUCKET="${STACK_NAME}-artifacts-${account}"
    aws s3api head-bucket --bucket "$ARTIFACTS_BUCKET" 2>/dev/null ||
        aws s3 mb "s3://${ARTIFACTS_BUCKET}"

    build_lambda_package

    frontend_url=$(optional_output "$STACK_NAME" FrontendUrl)
    deploy_cognito "$frontend_url"
    deploy_app "$frontend_url"

    converged_url=$(require_output "$STACK_NAME" FrontendUrl)
    if [ "$converged_url" != "$frontend_url" ]; then
        deploy_cognito "$converged_url"
        deploy_app "$converged_url"
    fi

    "$SCRIPT_DIR/lib/app-config.sh"
}

deploy_frontend() {
    local frontend="$APP_FOLDER/$FRONTEND_FOLDER" bucket distribution_id
    bucket=$(require_output "$STACK_NAME" FrontendBucket)
    distribution_id=$(require_output "$STACK_NAME" DistributionId)

    # config.js holds one stack pair's endpoints; publishing one left by another branch's deploy
    # would point this stack's users at that one's API, so it is written for this stack first.
    "$SCRIPT_DIR/lib/app-config.sh"

    step "Building the frontend"
    (cd "$frontend" && npm ci && npm run build)

    step "Uploading the build to s3://$bucket"
    # app.json and demo.html are not part of the Vite build, so the bulk sync excludes them from
    # --delete and they are published on their own.
    aws s3 sync "$frontend/dist/assets" "s3://${bucket}/assets" --delete \
        --cache-control 'public,max-age=31536000,immutable'
    aws s3 sync "$frontend/dist" "s3://${bucket}" --delete --exclude 'assets/*' \
        --exclude app.json --exclude demo.html --cache-control 'no-cache'
    aws s3 cp "$APP_FOLDER/config/app.json" "s3://${bucket}/app.json" --cache-control 'no-cache'
    aws s3 cp "$APP_FOLDER/docs/demo/demo.html" "s3://${bucket}/demo.html" \
        --cache-control 'no-cache'
    step "Invalidating CloudFront distribution $distribution_id"
    aws cloudfront create-invalidation --distribution-id "$distribution_id" --paths '/*' >/dev/null
}

if $DEPLOY_BACKEND; then
    deploy_backend
fi
if $DEPLOY_FRONTEND; then
    deploy_frontend
fi

FRONTEND_URL=$(require_output "$STACK_NAME" FrontendUrl)
step "deploy.sh completed for stack '$STACK_NAME'. Frontend: $FRONTEND_URL"
