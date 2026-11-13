#!/usr/bin/env bash
# This deployment's private settings that are not secrets, which lib/stack-params.sh passes to
# the two stacks. ./scripts/init-env.sh copies this file to lib/params.sh (gitignored); fill it in
# there and never commit it.

# Required: a regex a Google address must fully match to sign up, ignoring case. Join addresses
# with "|" and escape their dots; ".*" admits everyone. List the admin and every delegate too.
# Only a first sign-in is checked, so removing an address does not lock out an account that
# already signed in. Edit it before a deploy; the deploy passes it to the sign-up Lambda.
export ALLOWED_EMAILS="<owner-address-regex>"

# Required: the From address of every mail the app sends; must be a verified SES identity.
export SES_SENDER="<verified-ses-identity>"

# Required: the Google address that signs in as the deployment's admin; the browser locks its
# composer, so it manages no meters. A refused sign-up names it as the address to ask for access,
# and the refusal page offers to copy it.
export ADMIN_EMAIL="<admin-google-address>"

# Required: your email address for commercial-license requests, the one NOTICE and the README
# give. Nothing in the app reads it; naming it here lets that one address appear in git, which
# the address check of lib/app-env.sh otherwise refuses for every address of this file.
export LICENSE_CONTACT="<commercial-license-address>"
