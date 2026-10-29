"""Cognito PreSignUp trigger that requests SES verification of each new sign-up's address.

Every address is allowed in. While the SES account sits in its sandbox nothing can be delivered to
an address before it is verified, so the request goes out at sign-up. An account the operator
creates directly in the pool (AdminCreateUser) passes untouched: its address is not one SES should
mail."""

import boto3

from common import ses_identity
from common.log import get_logger

logger = get_logger(__name__)


def handler(event, context):
    if event["triggerSource"] == "PreSignUp_AdminCreateUser":
        return event
    email = event["request"]["userAttributes"]["email"].lower()
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
