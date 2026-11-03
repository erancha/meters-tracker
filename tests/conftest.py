"""Fixtures shared by the suite: the environment the Lambdas read, mocked AWS tables and bucket,
a Store over them, and a fake SES client for the sign-up handler."""

from contextlib import contextmanager

import boto3
import pytest
from moto import mock_aws

from common import store as store_module
from common.store import Store

REGION = "eu-central-1"
FILES_BUCKET = "files"
SES_SENDER = "reports@meters.test"
KEYED_BY_PK_ONLY = ["access"]
KEYED_BY_PK_AND_SK = [
    "properties",
    "apartments",
    "meters",
    "periods",
    "readings",
    "tariffs",
    "pending",
    "messages",
]


def pytest_configure(config):
    """Registers the correction matrix's markers. Its slices are named in the matrix module, from
    its states and case counts, so they are read from there."""
    from test_correction_matrix import MATRIX_SLICES

    config.addinivalue_line("markers", "matrix: the exhaustive correction matrix")
    for name in MATRIX_SLICES:
        config.addinivalue_line("markers", f"{name}: one slice of the correction matrix")


def _table(ddb, name, with_sort_key=True):
    """Creates one app table under the shared pk/sk key shape."""
    key_schema = [{"AttributeName": "pk", "KeyType": "HASH"}]
    attrs = [{"AttributeName": "pk", "AttributeType": "S"}]
    if with_sort_key:
        key_schema.append({"AttributeName": "sk", "KeyType": "RANGE"})
        attrs.append({"AttributeName": "sk", "AttributeType": "S"})
    ddb.create_table(
        TableName=name,
        KeySchema=key_schema,
        AttributeDefinitions=attrs,
        BillingMode="PAY_PER_REQUEST",
    )


def _maps_reversed(convert):
    """DynamoDB hands a map back with its keys in an order of its own, while moto keeps the order
    written. Reversing it on every read makes code that depends on that order fail here too."""

    def from_dynamo(value):
        converted = convert(value)
        if isinstance(converted, dict):
            return dict(reversed(converted.items()))
        return converted

    return from_dynamo


def set_environment(monkeypatch):
    """Points the code under test at the mocked tables, the mocked files bucket and the repo's
    own app config, with the api function's time limit the template deploys, and makes the
    store read maps back with their keys reordered."""
    monkeypatch.setattr(store_module, "_from_dynamo", _maps_reversed(store_module._from_dynamo))
    for name in KEYED_BY_PK_ONLY + KEYED_BY_PK_AND_SK:
        monkeypatch.setenv(f"{name.upper()}_TABLE", name)
    monkeypatch.setenv("FILES_BUCKET", FILES_BUCKET)
    monkeypatch.setenv("SES_SENDER", SES_SENDER)
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("APP_CONFIG_PATH", "config/app.json")
    monkeypatch.setenv("LAMBDA_TIMEOUT_SECONDS", "60")
    monkeypatch.setenv("LLM_MODEL", "anthropic:claude-opus-5-5")


@pytest.fixture
def env(monkeypatch):
    set_environment(monkeypatch)


@contextmanager
def mocked_aws():
    """Mocked DynamoDB resource with the app's tables pre-created (access keyed by pk only, the
    rest by pk+sk), an empty mocked S3 bucket and a verified report sender in mocked SES. The AWS
    mock stays active while the context is open, so code under test may also build its own boto3
    clients."""
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name=REGION)
        for name in KEYED_BY_PK_ONLY:
            _table(resource, name, with_sort_key=False)
        for name in KEYED_BY_PK_AND_SK:
            _table(resource, name)
        boto3.client("s3", region_name=REGION).create_bucket(
            Bucket=FILES_BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION}
        )
        boto3.client("ses", region_name=REGION).verify_email_identity(EmailAddress=SES_SENDER)
        yield resource


@pytest.fixture
def ddb():
    with mocked_aws() as resource:
        yield resource


@pytest.fixture
def store(env, ddb):
    return Store.from_env()


class FakeSes:
    """Records address-verification requests; raises instead when primed with a failure.
    verification maps an address to the status SES reports for it ("Success", "Pending", ...); an
    address absent from it is one SES has never heard of. verification_lookups holds one entry per
    lookup, so a caller that caches its answer can be told apart from one that asks again."""

    def __init__(self):
        self.verification = {}
        self.verification_lookups = []
        self.verification_requested = []
        self.verification_failure = None

    def get_identity_verification_attributes(self, Identities):
        if self.verification_failure is not None:
            raise self.verification_failure
        self.verification_lookups.append(list(Identities))
        return {
            "VerificationAttributes": {
                identity: {"VerificationStatus": self.verification[identity]}
                for identity in Identities
                if identity in self.verification
            }
        }

    def verify_email_identity(self, EmailAddress):
        if self.verification_failure is not None:
            raise self.verification_failure
        self.verification_requested.append(EmailAddress)
