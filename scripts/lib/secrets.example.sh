#!/usr/bin/env bash
# Secrets passed to the Cognito stack. ./scripts/init-env.sh copies this file to lib/secrets.sh
# (gitignored); fill it in there and never commit it.

# Required: the id of the Google OAuth client users sign in through (docs/sign-in.md).
export GOOGLE_CLIENT_ID="<google-oauth-client-id>"

# Required: that client's secret.
export GOOGLE_CLIENT_SECRET="<google-oauth-client-secret>"
