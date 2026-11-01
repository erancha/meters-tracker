"""Checks the CloudFormation templates against what the code and the test fixtures expect."""

import re
from pathlib import Path

import pytest
import yaml
from conftest import KEYED_BY_PK_AND_SK, KEYED_BY_PK_ONLY

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


class _TemplateLoader(yaml.SafeLoader):
    """Reads CloudFormation's short-form tags (!Ref, !Sub, !GetAtt, !If, !Equals) as the plain
    value they wrap, so a reference to a resource compares as that resource's name."""


def _plain_value(loader, tag_suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    return loader.construct_mapping(node, deep=True)


_TemplateLoader.add_multi_constructor("!", _plain_value)


def _load(name):
    return yaml.load((SCRIPTS / name).read_text(), Loader=_TemplateLoader)


@pytest.fixture(scope="module")
def app():
    return _load("template.yaml")


@pytest.fixture(scope="module")
def cognito():
    return _load("template-cognito.yaml")


def _logical_name(table):
    return f"{table.capitalize()}Table"


def _schema(properties):
    return (
        [(k["AttributeName"], k["KeyType"]) for k in properties["KeySchema"]],
        sorted(
            (a["AttributeName"], a["AttributeType"]) for a in properties["AttributeDefinitions"]
        ),
    )


def test_tables_match_conftest(app):
    resources = app["Resources"]
    declared = {n for n, r in resources.items() if r["Type"] == "AWS::DynamoDB::Table"}
    assert declared == {_logical_name(t) for t in KEYED_BY_PK_ONLY + KEYED_BY_PK_AND_SK}
    for table in KEYED_BY_PK_ONLY:
        assert _schema(resources[_logical_name(table)]["Properties"]) == (
            [("pk", "HASH")],
            [("pk", "S")],
        )
    for table in KEYED_BY_PK_AND_SK:
        assert _schema(resources[_logical_name(table)]["Properties"]) == (
            [("pk", "HASH"), ("sk", "RANGE")],
            [("pk", "S"), ("sk", "S")],
        )


def test_data_survives_deletes_and_replacement(app):
    resources = app["Resources"]
    for table in KEYED_BY_PK_ONLY + KEYED_BY_PK_AND_SK:
        resource = resources[_logical_name(table)]
        assert resource["DeletionPolicy"] == resource["UpdateReplacePolicy"] == "Retain"
        properties = resource["Properties"]
        assert properties["DeletionProtectionEnabled"] is True
        assert properties["PointInTimeRecoverySpecification"] == {
            "PointInTimeRecoveryEnabled": True
        }
    bucket = resources["FilesBucket"]
    assert bucket["DeletionPolicy"] == bucket["UpdateReplacePolicy"] == "Retain"
    assert bucket["Properties"]["VersioningConfiguration"] == {"Status": "Enabled"}
    rules = bucket["Properties"]["LifecycleConfiguration"]["Rules"]
    assert [r["NoncurrentVersionExpiration"]["NoncurrentDays"] for r in rules] == [30]
    assert all(r["Status"] == "Enabled" for r in rules)


def test_every_function_is_told_each_table_name(app):
    variables = app["Globals"]["Function"]["Environment"]["Variables"]
    for table in KEYED_BY_PK_ONLY + KEYED_BY_PK_AND_SK:
        assert variables[f"{table.upper()}_TABLE"] == _logical_name(table)
    assert variables["APP_CONFIG_PATH"] == "/var/task/app.json"


def test_api_function_environment_names_the_bucket_and_model(app):
    variables = app["Resources"]["ApiFunction"]["Properties"]["Environment"]["Variables"]
    assert variables["FILES_BUCKET"] == "FilesBucket"
    assert variables["LLM_MODEL"] == "LlmModel"
    assert variables["LLM_KEY_PARAM"] == "LlmKeyParam"
    assert "Default" not in app["Parameters"]["LlmKeyParam"]
    assert "Default" not in app["Parameters"]["LlmModel"]


def test_set_model_key_script_takes_the_providers_the_template_accepts(app):
    pattern = app["Parameters"]["LlmModel"]["AllowedPattern"]
    providers = re.fullmatch(r"\^\((.+)\):\.\+\$", pattern).group(1)
    assert f"{providers})" in (SCRIPTS / "set-model-key.sh").read_text()


def test_deploy_passes_the_key_parameter_of_the_model_provider():
    deploy = (SCRIPTS / "deploy.sh").read_text()
    assert 'KEY_PARAM=$(model_key_param "$PROVIDER")' in deploy
    assert 'LlmKeyParam="$KEY_PARAM"' in deploy


def test_params_example_names_a_model_the_template_accepts(app):
    pattern = app["Parameters"]["LlmModel"]["AllowedPattern"]
    example = (SCRIPTS / "params.example.sh").read_text()
    values = re.findall(r'^(?:# )?export LLM_MODEL="([^"]+)"', example, re.MULTILINE)
    assert len(values) == 2
    assert all(re.fullmatch(pattern, value) for value in values)
    assert "deploy.sh" in example and 'LlmModel="$LLM_MODEL"' in (SCRIPTS / "deploy.sh").read_text()


def test_api_routes(app):
    function = app["Resources"]["ApiFunction"]["Properties"]
    routes = {
        (e["Properties"]["Method"], e["Properties"]["Path"]) for e in function["Events"].values()
    }
    assert routes == {("GET", "/chat"), ("POST", "/turn"), ("GET", "/files/{key+}")}
    assert function["Handler"] == "handlers/api.handler"
    assert function["Timeout"] == "ApiTimeoutSeconds"
    assert function["Environment"]["Variables"]["LAMBDA_TIMEOUT_SECONDS"] == "ApiTimeoutSeconds"
    assert app["Parameters"]["ApiTimeoutSeconds"]["Default"] == 60


def test_files_bucket_is_private(app):
    block = app["Resources"]["FilesBucket"]["Properties"]["PublicAccessBlockConfiguration"]
    assert all(block[k] is True for k in block) and len(block) == 4


def test_outputs_feed_the_deploy_scripts(app):
    assert {"ApiUrl", "FrontendUrl", "DistributionId", "FrontendBucket"} <= set(app["Outputs"])


def test_cognito_admits_everyone_and_runs_the_presignup_handler(cognito):
    assert "AllowedEmails" not in cognito["Parameters"]
    function = cognito["Resources"]["PreSignupFunction"]["Properties"]
    assert function["Handler"] == "handlers/presignup.handler"
    assert "Environment" not in function
    actions = {
        a
        for s in function["Policies"][0]["Statement"]
        for a in ([s["Action"]] if isinstance(s["Action"], str) else s["Action"])
    }
    assert actions == {"ses:VerifyEmailIdentity", "ses:GetIdentityVerificationAttributes"}
    assert {"UserPoolId", "UserPoolClientId", "HostedUiDomain"} <= set(cognito["Outputs"])


def test_cognito_admits_only_google_verified_addresses(cognito):
    resources = cognito["Resources"]
    pool = resources["UserPool"]["Properties"]
    assert pool["AdminCreateUserConfig"]["AllowAdminCreateUserOnly"] is True
    client = resources["UserPoolClient"]["Properties"]
    assert client["ExplicitAuthFlows"] == ["ALLOW_REFRESH_TOKEN_AUTH"]
    mapping = resources["GoogleProvider"]["Properties"]["AttributeMapping"]
    assert mapping["email_verified"] == "email_verified"
