#!/usr/bin/env bash
# This deployment's private settings that are not secrets, which lib/stack-params.sh passes to
# the two stacks. scripts/init-env.sh copies this file to lib/params.sh (gitignored); fill in real
# values there and never commit it.
# SES_SENDER: From address of every mail the app sends; must be a verified SES identity.
# ADMIN_EMAIL: the Google address that signs in as the deployment's admin; the browser locks its
# composer, so it manages no meters. A refused sign-up names it as the address to ask for access,
# and the refusal page offers to copy it.
# ALLOWED_EMAILS: a regex a Google address must fully match to sign up, ignoring case. Join
# addresses with "|" and escape their dots; ".*" admits everyone. List the admin and every
# delegate too. Only a first sign-in is checked, so removing an address does not lock out an
# account that already signed in. Edit it before a deploy; the deploy passes it to the sign-up
# Lambda.
export SES_SENDER="<verified-ses-identity>"
export ADMIN_EMAIL="<admin-google-address>"
export ALLOWED_EMAILS="<owner-address-regex>"
