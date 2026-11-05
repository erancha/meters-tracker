"""The Cognito pre-sign-up handler: refusing an address off the allowed list, asking SES to
verify a new address once, letting sign-up through when SES fails, and passing operator-created
accounts untouched."""

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


@pytest.fixture(autouse=True)
def allowed(monkeypatch):
    monkeypatch.setenv("ALLOWED_EMAILS", r"new@gmail\.com|back@gmail\.com")
    monkeypatch.setenv("ADMIN_EMAIL", "admin@x.com")


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


def test_address_off_the_allowed_list_is_refused_before_ses_is_asked(ses):
    with pytest.raises(PermissionError, match="stranger@gmail.com") as refused:
        presignup.handler(event("Stranger@Gmail.com"), None)
    assert ses.verification_lookups == []
    assert str(refused.value).endswith(": admin@x.com.")


def test_allowed_list_must_match_the_whole_address(ses):
    with pytest.raises(PermissionError):
        presignup.handler(event("new@gmail.com.evil.io"), None)
