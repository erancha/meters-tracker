#!/usr/bin/env bash
# Deployment parameters that scripts/deploy.sh passes to the two stacks. Copy this file to
# scripts/params.sh (gitignored) and fill in real values; never commit the copy.
# GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET: the Google OAuth client the user pool signs in with.
# LLM_MODEL: the model that reads photos and classifies messages, as provider:model with an
# anthropic or openai provider; it must take image input. The provider's API key is not a deploy
# parameter; store it with scripts/set-model-key.sh before deploying.
# SES_SENDER: From address of every mail the app sends; must be a verified SES identity.
# ADMIN_EMAIL: the Google address that signs in as the deployment's admin; the browser locks its
# composer, so it manages no meters.
export GOOGLE_CLIENT_ID="<google-oauth-client-id>"
export GOOGLE_CLIENT_SECRET="<google-oauth-client-secret>"
export LLM_MODEL="anthropic:claude-opus-5-5"
export SES_SENDER="<verified-ses-identity>"
export ADMIN_EMAIL="<admin-google-address>"
# The OpenAI alternative: as of 2026-11, gpt-6.1-sol's token prices are half of claude-opus-5-5's.
# export LLM_MODEL="openai:gpt-6.1-sol"
