"""Cognito PreSignUp trigger that admits only the addresses on the allowed list and requests SES
verification of each one it admits.

ALLOWED_EMAILS is one regex the lowercase address must fully match, ignoring case; alternation
lists several addresses and ".*" admits everyone. An exception refuses the sign-up, and its
message, which names ADMIN_EMAIL as the address to ask for access, reaches the browser through
Cognito's error redirect. While the SES account sits in its
sandbox nothing can be delivered to an address before it is verified, so the request goes out at
sign-up. An account the operator creates directly in the pool (AdminCreateUser) passes untouched:
creating it already takes AWS credentials, and its address is not one SES should mail."""

import os
import re

import boto3

from common import ses_identity, texts
from common.log import get_logger

logger = get_logger(__name__)


def handler(event, context):
    if event["triggerSource"] == "PreSignUp_AdminCreateUser":
        return event
    email = event["request"]["userAttributes"]["email"].lower()
    if not re.fullmatch(os.environ["ALLOWED_EMAILS"], email, re.IGNORECASE):
        raise PermissionError(texts.signup_not_allowed(email, os.environ["ADMIN_EMAIL"]))
    _request_verification(boto3.client("ses"), email)
    return event


def _request_verification(ses, email):
    """Asks SES to mail the user its address-verification request, unless the address is already
    verified or the request is already out. Best effort: a failure is logged and must never deny
    the sign-up."""
    try:
        status = ses_identity.verification_status(ses, email)
        if status not in (ses_identity.VERIFIED, ses_identity.PENDING):
            ses.verify_email_identity(EmailAddress=email)
    except Exception:
        logger.exception("verification request failed for new user %s", email)
