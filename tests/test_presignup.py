"""The Cognito pre-sign-up handler: asking SES to verify a new address once, letting sign-up
through when SES fails, and passing operator-created accounts untouched."""

import logging

import pytest
from conftest import FakeSes

from handlers import presignup


def event(email, trigger="PreSignUp_ExternalProvider"):
    return {
        "triggerSource": trigger,
        "request": {"userAttributes": {"email": email}},
        "response": {},
    }


@pytest.fixture
def ses(monkeypatch):
    fake = FakeSes()
    monkeypatch.setattr(presignup.boto3, "client", lambda service: fake)
    return fake


def test_presignup_requests_verification_once(ses):
    assert presignup.handler(event("New@Gmail.com"), None) == event("New@Gmail.com")
    assert ses.verification_requested == ["new@gmail.com"]
    ses.verification = {"new@gmail.com": "Pending"}
    presignup.handler(event("new@gmail.com"), None)
    assert ses.verification_requested == ["new@gmail.com"]


def test_verified_address_is_not_asked_again(ses):
    ses.verification = {"back@gmail.com": "Success"}
    presignup.handler(event("back@gmail.com"), None)
    assert ses.verification_requested == []


def test_verification_failure_is_logged_and_does_not_block_signup(ses, caplog):
    ses.verification_failure = RuntimeError("ses down")
    with caplog.at_level(logging.ERROR):
        assert presignup.handler(event("new@gmail.com"), None) == event("new@gmail.com")
    assert "new@gmail.com" in caplog.text


def test_operator_created_account_passes_untouched(ses):
    created = event("fictive@example.invalid", trigger="PreSignUp_AdminCreateUser")
    assert presignup.handler(created, None) == created
    assert ses.verification_lookups == []
