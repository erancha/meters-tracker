#!/usr/bin/env bash
# AWS credentials and region the scripts source into their shell. scripts/init-env.sh copies
# this file to lib/aws-config.sh (gitignored); fill in real values there and never commit it.
# AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY: an IAM user's access key.
# AWS_DEFAULT_REGION: the region both stacks and the model key parameters live in.
export AWS_ACCESS_KEY_ID="<access-key-id>"
export AWS_SECRET_ACCESS_KEY="<secret-access-key>"
export AWS_DEFAULT_REGION="eu-central-1"
