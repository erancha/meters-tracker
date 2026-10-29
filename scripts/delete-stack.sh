#!/usr/bin/env bash
# Delete the app stack and then the Cognito stack, keeping the tables and the photos.
#
#   ./scripts/delete-stack.sh [env]
#
# env: optional suffix (e.g. dev) naming the isolated stack pair to delete, as for deploy.sh.
# The DynamoDB tables and the files bucket survive the delete with their generated names
# (<stack>-<LogicalId>-<suffix>): the tables are retained and deletion-protected, the bucket is
# retained and versioned. This script never deletes them, and deploy.sh refuses to create the
# stack again while they exist.
# Find them with aws dynamodb list-tables and aws s3api list-buckets, filtered by the stack-name
# prefix, or note them from aws cloudformation describe-stack-resources before deleting.
# Recover: import them into the new stack as a CloudFormation resource import
# (aws cloudformation create-change-set --change-set-type IMPORT with their physical names).
# Remove for good: disable deletion protection (aws dynamodb update-table
# --no-deletion-protection-enabled) and delete each table; empty the bucket including every version
# and delete marker (the console's Empty, or aws s3api delete-objects over list-object-versions);
# then delete the bucket.
# The frontend bucket is emptied and deleted with the stack.
# Requires scripts/aws-config.sh.
#
#   ./scripts/delete-stack.sh dev
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=common.sh
source "$ROOT/scripts/common.sh"

parse_env_suffix_arg "$@"
require_aws_config

# A bucket holding objects cannot be deleted with its stack.
FRONTEND_BUCKET=$(stack_output "$APP" FrontendBucket)
aws s3 rm "s3://${FRONTEND_BUCKET}" --recursive
aws cloudformation delete-stack --stack-name "$APP"
aws cloudformation wait stack-delete-complete --stack-name "$APP"
aws cloudformation delete-stack --stack-name "${APP}-cognito"
aws cloudformation wait stack-delete-complete --stack-name "${APP}-cognito"
echo "Both stacks deleted. The DynamoDB tables and the files bucket were retained;" \
     "see ./scripts/delete-stack.sh --help to recover or remove them."
