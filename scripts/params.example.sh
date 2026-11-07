#!/usr/bin/env bash
# Deployment parameters that scripts/deploy.sh passes to the two stacks. Copy this file to
# scripts/params.sh (gitignored) and fill in real values; never commit the copy.
# GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET: the Google OAuth client the user pool signs in with.
# LLM_MODEL: the model that reads photos and classifies messages, as provider:model with an
# anthropic or openai provider; it must take image input. The provider's API key is not a deploy
# parameter; store it with scripts/set-model-key.sh before deploying.
# SES_SENDER: From address of every mail the app sends; must be a verified SES identity.
# ADMIN_EMAIL: the Google address that signs in as the deployment's admin; the browser locks its
# composer, so it manages no meters. A refused sign-up names it as the address to ask for access,
# and the refusal page offers to copy it.
# ALLOWED_EMAILS: a regex a Google address must fully match to sign up, ignoring case. Join
# addresses with "|" and escape their dots; ".*" admits everyone. List the admin and every
# delegate too. Only a first sign-in is checked, so removing an address does not lock out an
# account that already signed in. Edit it before a deploy; the deploy passes it to the sign-up
# Lambda.
export GOOGLE_CLIENT_ID="<google-oauth-client-id>"
export GOOGLE_CLIENT_SECRET="<google-oauth-client-secret>"
export LLM_MODEL="anthropic:claude-opus-5-5"
export SES_SENDER="<verified-ses-identity>"
export ADMIN_EMAIL="<admin-google-address>"
export ALLOWED_EMAILS="<owner-address-regex>"
# The OpenAI alternative: as of 2026-11, gpt-6.1-sol's token prices are half of claude-opus-5-5's.
# export LLM_MODEL="openai:gpt-6.1-sol"
