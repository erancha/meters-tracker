"""Checks the CloudFormation templates against what the code and the test fixtures expect."""

import re
from pathlib import Path

import pytest
import yaml
from conftest import KEYED_BY_PK_AND_SK, KEYED_BY_PK_ONLY

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
STACK_PARAMS = (SCRIPTS / "lib" / "stack-params.sh").read_text()


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


def test_api_function_may_send_reports_from_the_deployed_sender(app):
    function = app["Resources"]["ApiFunction"]["Properties"]
    assert function["Environment"]["Variables"]["SES_SENDER"] == "SesSender"
    assert "Default" not in app["Parameters"]["SesSender"]
    statements = [s for p in function["Policies"] if "Statement" in p for s in p["Statement"]]
    [send] = [s for s in statements if s["Action"] == "ses:SendRawEmail"]
    # SES also checks the recipient's identity while the account is in its sandbox.
    assert send["Resource"].endswith(":identity/*")


def test_set_model_key_option_takes_the_providers_the_template_accepts(app):
    pattern = app["Parameters"]["LlmModel"]["AllowedPattern"]
    providers = re.fullmatch(r"\^\((.+)\):\.\+\$", pattern).group(1)
    assert f"{providers})" in (SCRIPTS / "lib" / "aws-helper-app.sh").read_text()


def test_deploy_passes_the_key_parameter_of_the_model_provider():
    assert 'LLM_PROVIDER="${LLM_MODEL%%:*}"' in STACK_PARAMS
    assert 'LLM_KEY_PARAM="$(model_key_param "$LLM_PROVIDER")"' in STACK_PARAMS
    assert '"LlmKeyParam=$LLM_KEY_PARAM"' in STACK_PARAMS


def test_stack_params_name_models_the_template_accepts(app):
    pattern = app["Parameters"]["LlmModel"]["AllowedPattern"]
    values = re.findall(r'^(?:# )?LLM_MODEL="([^"]+)"', STACK_PARAMS, re.MULTILINE)
    assert len(values) == 2
    assert all(re.fullmatch(pattern, value) for value in values)
    assert '"LlmModel=$LLM_MODEL"' in STACK_PARAMS
    assert "stack-params.sh" in (SCRIPTS / "deploy.sh").read_text()


@pytest.mark.parametrize(
    ("template", "parameters"),
    [
        ("template.yaml", "app_stack_parameters"),
        ("template-cognito.yaml", "cognito_stack_parameters"),
    ],
)
def test_each_function_logs_to_a_group_its_stack_declares(template, parameters):
    loaded = _load(template)
    resources = loaded["Resources"]
    functions = [n for n, r in resources.items() if r["Type"] == "AWS::Serverless::Function"]
    assert functions
    for name in functions:
        group = resources[f"{name}LogGroup"]
        assert group["Type"] == "AWS::Logs::LogGroup"
        assert group["Properties"]["LogGroupName"] == f"/aws/lambda/${{AWS::StackName}}/{name}"
        assert group["Properties"]["RetentionInDays"] == "LogRetentionDays"
        assert resources[name]["Properties"]["LoggingConfig"]["LogGroup"] == f"{name}LogGroup"
    assert loaded["Parameters"]["LogRetentionDays"]["Type"] == "Number"
    body = STACK_PARAMS.split(f"{parameters}() {{")[1].split("\n}")[0]
    assert '"LogRetentionDays=$LOG_RETENTION_DAYS"' in body


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


def test_cognito_runs_the_presignup_handler_with_the_allowed_list(cognito):
    assert "AllowedEmails" in cognito["Parameters"]
    function = cognito["Resources"]["PreSignupFunction"]["Properties"]
    assert function["Handler"] == "handlers/presignup.handler"
    assert function["Environment"]["Variables"] == {
        "ALLOWED_EMAILS": "AllowedEmails",
        "ADMIN_EMAIL": "AdminEmail",
    }
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


def test_deploy_requires_the_admin_and_passes_it_to_the_api(app):
    variables = app["Resources"]["ApiFunction"]["Properties"]["Environment"]["Variables"]
    assert variables["ADMIN_EMAIL"] == "AdminEmail"
    assert "Default" not in app["Parameters"]["AdminEmail"]
    body = STACK_PARAMS.split("app_stack_parameters() {")[1].split("\n}")[0]
    assert '"AdminEmail=$ADMIN_EMAIL"' in body
    assert re.search(r"^require_settings params\.sh .*\bADMIN_EMAIL\b", STACK_PARAMS, re.MULTILINE)
    assert 'export ADMIN_EMAIL="' in (SCRIPTS / "lib" / "params.example.sh").read_text()
