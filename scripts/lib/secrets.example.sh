#!/usr/bin/env bash
# Secrets that lib/stack-params.sh passes to the Cognito stack. scripts/init-env.sh copies this
# file to lib/secrets.sh (gitignored); fill in real values there and never commit it. The private
# settings that are not secrets are in lib/params.sh, the public parameters in
# lib/stack-params.sh.
# GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET: the Google OAuth client the user pool signs in with.
export GOOGLE_CLIENT_ID="<google-oauth-client-id>"
export GOOGLE_CLIENT_SECRET="<google-oauth-client-secret>"
