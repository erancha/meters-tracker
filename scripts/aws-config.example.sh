#!/usr/bin/env bash
# AWS credentials and region the ops scripts source into their shell. Copy this file to
# scripts/aws-config.sh (gitignored) and fill in real values; never commit the copy.
# AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY: an IAM user's access key.
# AWS_DEFAULT_REGION: the region both stacks and the Anthropic key parameter live in.
export AWS_ACCESS_KEY_ID="<access-key-id>"
export AWS_SECRET_ACCESS_KEY="<secret-access-key>"
export AWS_DEFAULT_REGION="eu-central-1"
