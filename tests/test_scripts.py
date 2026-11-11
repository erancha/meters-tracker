"""Runs the shell helpers of scripts/lib/app-env.sh, and aws-helper.sh, init-env.sh and the
frontend step of deploy.sh whole, against a fake aws command that prints canned answers, so the stack lookups' handling of empty,
missing and failed results is checked offline. The whole-script runs happen in a temporary git
checkout holding a copy of the scripts and stub settings files, since the scripts take the stack
from the checkout's branch and refuse settings files git would track. Also runs the scripts that
act on a deployed stack or the real model with --help and with arguments they refuse, both of
which stop before any settings file or AWS call."""

import json
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
APP_ENV = SCRIPTS / "lib" / "app-env.sh"
FAKE_AWS = """#!/usr/bin/env bash
printf '%s' "$FAKE_STDOUT"
printf '%s' "$FAKE_STDERR" >&2
exit "$FAKE_STATUS"
"""
# Records each call. A stack lists one function, named fn-<logical id>, which logs to
# /aws/lambda/group-of-fn-<logical id>; a logs call answers with the log events; every other
# call, and a stack listing when FAKE_STATUS is not 0, answers as FAKE_AWS does. An app stack
# owns the buckets <stack>-frontend and <stack>-files, the second retained by its template; the
# first holds an object version and a delete marker when BUCKET_HOLDS_OBJECTS is set, until a
# delete-objects call, whose stdin is kept in $AWS_CALLS.deleted. The region holds one Lambda
# layer, shared, with versions 2 and 3; its functions use the versions USED_LAYER_VERSIONS names.
FAKE_STACK_AWS = """#!/usr/bin/env bash
echo "$*" >> "$AWS_CALLS"
case "$1 $2" in
  "logs tail") printf '%s' "$FAKE_LOGS"; exit 0 ;;
  "cloudformation list-stack-resources")
    if [ "$FAKE_STATUS" = 0 ]; then
      case "$*" in
        *"-cognito "*) id=PreSignupFunction ;;
        *) id=ApiFunction ;;
      esac
      case "$*" in
        *AWS::Lambda::Function*)
          printf '[{"LogicalResourceId": "%s", "PhysicalResourceId": "fn-%s"}]' "$id" "$id" ;;
        *AWS::S3::Bucket*)
          while [ "$1" != --stack-name ]; do shift; done
          case "$2" in
            *-cognito) printf '[]' ;;
            *) printf '[{"LogicalResourceId": "%sBucket", "PhysicalResourceId": "%s"}]' \\
                 Frontend "$2-frontend" Files "$2-files" | jq -s add ;;
          esac ;;
        *) printf '[]' ;;
      esac
      exit 0
    fi ;;
  "lambda get-function-configuration")
    while [ "$1" != --function-name ]; do shift; done
    case "$*" in
      *LoggingConfig.LogGroup*) printf '/aws/lambda/group-of-%s\\n' "$2" ;;
      *) printf '{"runtime": "python3.13", "package": "Zip"}' ;;
    esac
    exit 0 ;;
  "cloudformation get-template")
    echo '{"Resources": {"FrontendBucket": {"Type": "AWS::S3::Bucket"},
           "FilesBucket": {"Type": "AWS::S3::Bucket", "DeletionPolicy": "Retain"}}}'
    exit 0 ;;
  "s3api list-object-versions")
    if [ -n "${BUCKET_HOLDS_OBJECTS:-}" ] && [ ! -e "$AWS_CALLS.emptied" ]; then
      echo '{"Versions": [{"Key": "index.html", "VersionId": "v1"}],
             "DeleteMarkers": [{"Key": "old.js", "VersionId": "v2"}]}'
    else
      echo '{"Versions": null, "DeleteMarkers": null}'
    fi
    exit 0 ;;
  "s3api delete-objects")
    cat > "$AWS_CALLS.deleted"
    touch "$AWS_CALLS.emptied"
    echo null
    exit 0 ;;
  "logs describe-log-groups") echo '[]'; exit 0 ;;
  "cloudformation delete-stack" | "cloudformation wait") exit 0 ;;
  "lambda list-layers") echo '["shared"]'; exit 0 ;;
  "lambda list-layer-versions")
    for version in 2 3; do
      printf '["%s:%s", "2026-0%s-01T00:00:00.000+0000"]' "$LAYER_ARN" "$version" "$version"
    done | jq -s .
    exit 0 ;;
  "lambda list-functions")
    for version in $USED_LAYER_VERSIONS; do
      printf '"%s:%s"' "$LAYER_ARN" "$version"
    done | jq -s .
    exit 0 ;;
  "lambda delete-layer-version") exit 0 ;;
esac
printf '%s' "$FAKE_STDOUT"
printf '%s' "$FAKE_STDERR" >&2
exit "$FAKE_STATUS"
"""
# Values no tracked file holds, as the scripts refuse a settings value that git also holds.
STUB_AWS_CONFIG = (
    "export AWS_ACCESS_KEY_ID=AKIASTUBKEYID\n"
    "export AWS_SECRET_ACCESS_KEY=stub-secret-access-key\n"
    "export AWS_DEFAULT_REGION=eu-central-1\n"
)
# The gitignored files init-env.sh creates: the two the secrets check covers, and the private
# settings that are not secrets.
SETTINGS_FILES = ("aws-config.sh", "secrets.sh", "params.sh")
REGION = "--region eu-central-1"


STUB_SECRETS = (
    "export GOOGLE_CLIENT_ID=stub-google-client-id\n"
    "export GOOGLE_CLIENT_SECRET=stub-google-client-secret\n"
)
STUB_PARAMS = (
    "export SES_SENDER=sender@stub.test\n"
    "export ADMIN_EMAIL=admin@stub.test\n"
    "export ALLOWED_EMAILS=stub-allowed-regex\n"
)


def _write_fake_aws(folder, body):
    folder.mkdir(exist_ok=True)
    aws = folder / "aws"
    aws.write_text(body)
    aws.chmod(0o755)


@pytest.fixture
def run(tmp_path):
    _write_fake_aws(tmp_path / "bin", FAKE_AWS)

    def run(snippet, stdout="", stderr="", status=0):
        env = {
            "HOME": str(tmp_path),
            "PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin",
            "FAKE_STDOUT": stdout,
            "FAKE_STDERR": stderr,
            "FAKE_STATUS": str(status),
        }
        script = f'set -euo pipefail\nSCRIPT_DIR="{SCRIPTS}"\nsource "{APP_ENV}"\n{snippet}\n'
        return subprocess.run(
            ["bash", "-c", script], check=False, env=env, capture_output=True, text=True
        )

    return run


def test_stack_query_keeps_a_warning_out_of_the_value(run):
    result = run(
        'value=$(optional_output app ApiUrl); echo "[$value]"',
        stdout="https://api",
        stderr="WARNING: old CLI",
    )
    assert (result.returncode, result.stdout) == (0, "[https://api]\n")
    assert "WARNING: old CLI" in result.stderr


def test_require_output_prints_the_value(run):
    result = run('value=$(require_output app-cognito UserPoolId); echo "[$value]"', stdout="p-1")
    assert (result.returncode, result.stdout) == (0, "[p-1]\n")


def test_require_output_stops_on_a_missing_output(run):
    result = run('value=$(require_output app-cognito UserPoolId); echo "[$value]"', stdout="None")
    assert result.returncode != 0
    assert result.stdout == ""
    assert "app-cognito" in result.stderr and "UserPoolId" in result.stderr


def test_require_output_stops_on_a_failed_lookup(run):
    result = run(
        'value=$(require_output app UserPoolId); echo "[$value]"', stderr="AccessDenied", status=255
    )
    assert result.returncode != 0
    assert result.stdout == ""
    assert "AccessDenied" in result.stderr


def test_model_key_param_is_one_per_provider(run):
    result = run("model_key_param openai")
    assert result.stdout == "/meters-tracker/llm/openai/api-key\n"


def test_require_model_key_passes_when_the_key_is_stored(run):
    assert run("require_model_key openai", stdout="3").returncode == 0


def test_require_model_key_names_the_fix_for_a_missing_key(run):
    result = run(
        "require_model_key openai",
        stderr="An error occurred (ParameterNotFound) when calling the GetParameter operation",
        status=254,
    )
    assert result.returncode == 1
    assert "./scripts/aws-helper.sh --set-model-key openai" in result.stderr


def test_require_model_key_passes_on_a_failed_lookup(run):
    result = run("require_model_key openai", stderr="AccessDenied", status=254)
    assert result.returncode == 1
    assert "AccessDenied" in result.stderr
    assert "set-model-key" not in result.stderr


class Checkout:
    """A temporary git checkout holding a copy of the scripts, stub settings files, the fake aws
    command first on its PATH and a file that records each aws call."""

    def __init__(self, root, branch):
        self.root = root
        self.lib = root / "scripts" / "lib"
        self.calls = root / "bin" / "calls"
        self.lib.mkdir(parents=True)
        (root / ".githooks").mkdir()
        (root / ".gitignore").write_text((REPO / ".gitignore").read_text())
        hook = REPO / ".githooks" / "pre-commit"
        (root / ".githooks" / "pre-commit").write_text(hook.read_text())
        for source in [*SCRIPTS.glob("*.sh"), *SCRIPTS.glob("*.py"), *SCRIPTS.glob("lib/*.sh")]:
            if source.name not in SETTINGS_FILES:
                target = root / source.relative_to(REPO)
                target.write_text(source.read_text())
                target.chmod(0o755)
        (self.lib / "aws-config.sh").write_text(STUB_AWS_CONFIG)
        (self.lib / "secrets.sh").write_text(STUB_SECRETS)
        (self.lib / "params.sh").write_text(STUB_PARAMS)
        _write_fake_aws(root / "bin", FAKE_STACK_AWS)
        identity = ["-c", "user.name=test", "-c", "user.email=test@stub.test"]
        for command in (
            ["init", "--quiet", "--initial-branch", branch],
            ["config", "core.hooksPath", ".githooks"],
            ["add", "--all"],
            [
                *identity,
                "-c",
                "commit.gpgsign=false",
                "commit",
                "--quiet",
                "--no-verify",
                "-m",
                "x",
            ],
        ):
            subprocess.run(["git", "-C", root, *command], check=True)

    def run(
        self,
        script,
        *args,
        stdout="",
        stderr="",
        status=0,
        logs="",
        stdin=None,
        holds_objects=False,
        used_layer_versions="3",
    ):
        env = {
            "HOME": str(self.root),
            "PATH": f"{self.root / 'bin'}:/usr/bin:/bin",
            "AWS_CALLS": str(self.calls),
            "FAKE_LOGS": logs,
            "FAKE_STDOUT": stdout,
            "FAKE_STDERR": stderr,
            "FAKE_STATUS": str(status),
            "BUCKET_HOLDS_OBJECTS": "1" if holds_objects else "",
            "LAYER_ARN": "arn:aws:lambda:eu-central-1:123456789012:layer:shared",
            "USED_LAYER_VERSIONS": used_layer_versions,
        }
        return subprocess.run(
            ["bash", self.root / "scripts" / script, *args],
            check=False,
            env=env,
            capture_output=True,
            text=True,
            input=stdin,
        )

    def source(self, snippet):
        """Runs a snippet with lib/app-env.sh of the checkout sourced."""
        (self.root / "scripts" / "snippet.sh").write_text(
            "set -euo pipefail\n"
            'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
            f'source "$SCRIPT_DIR/lib/app-env.sh"\n{snippet}\n'
        )
        return self.run("snippet.sh")

    def helper(self, *args, **answers):
        return self.run("aws-helper.sh", *args, **answers)

    def aws_calls(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []


@pytest.fixture
def checkout(tmp_path):
    return Checkout(tmp_path / "main", "main")


@pytest.fixture
def branch_checkout(tmp_path):
    return Checkout(tmp_path / "branch", "some-feature")


STACK_PARAMS = 'connect_stack; source "$SCRIPT_DIR/lib/stack-params.sh"; echo reached'


def test_stack_params_name_the_missing_setting(checkout):
    (checkout.lib / "params.sh").write_text(STUB_PARAMS.replace("export SES_SENDER", "#"))
    result = checkout.source(STACK_PARAMS)
    assert result.returncode != 0
    assert result.stdout == ""
    assert "SES_SENDER" in result.stderr and "params.example.sh" in result.stderr


def test_stack_params_refuse_a_private_setting_left_as_its_placeholder(checkout):
    (checkout.lib / "params.sh").write_text(
        STUB_PARAMS.replace("admin@stub.test", '"<admin-google-address>"')
    )
    result = checkout.source(STACK_PARAMS)
    assert result.returncode != 0
    assert result.stdout == ""
    assert "ADMIN_EMAIL" in result.stderr and "params.example.sh" in result.stderr


def test_load_params_refuses_a_file_git_would_track(checkout):
    gitignore = checkout.root / ".gitignore"
    gitignore.write_text(gitignore.read_text().replace("/scripts/lib/params.sh\n", ""))
    result = checkout.source("load_params; echo reached")
    assert result.returncode != 0
    assert result.stdout == ""
    assert "params.sh" in result.stderr and "gitignored" in result.stderr


def test_load_params_refuses_an_address_of_the_private_settings_in_git(checkout):
    (checkout.root / "notes.txt").write_text("mail comes from sender@stub.test\n")
    subprocess.run(["git", "-C", checkout.root, "add", "notes.txt"], check=True)
    result = checkout.source(STACK_PARAMS)
    assert result.returncode != 0
    assert result.stdout == ""
    assert "SES_SENDER" in result.stderr and "notes.txt" in result.stderr


def test_stack_params_refuse_a_placeholder_left_from_the_example(checkout):
    (checkout.lib / "secrets.sh").write_text(
        STUB_SECRETS.replace("stub-google-client-id", '"<google-oauth-client-id>"')
    )
    result = checkout.source(STACK_PARAMS)
    assert result.returncode != 0
    assert result.stdout == ""
    assert "GOOGLE_CLIENT_ID" in result.stderr and "secrets.example.sh" in result.stderr
    assert "google-oauth-client-id" not in result.stderr


def test_stack_params_vary_the_cognito_domain_with_the_branch_stack(checkout, branch_checkout):
    snippet = (
        STACK_PARAMS + '; cognito_stack_parameters x; printf "%s\\n" "${COGNITO_PARAMETERS[@]}"'
    )
    assert "DomainPrefix=meters-trk\n" in checkout.source(snippet).stdout
    assert "DomainPrefix=meters-trk-fb\n" in branch_checkout.source(snippet).stdout


def test_load_aws_config_refuses_a_placeholder_key(checkout):
    (checkout.lib / "aws-config.sh").write_text(
        STUB_AWS_CONFIG.replace("AKIASTUBKEYID", '"<access-key-id>"')
    )
    result = checkout.source("load_aws_config; echo reached")
    assert result.returncode != 0
    assert result.stdout == ""
    assert "AWS_ACCESS_KEY_ID" in result.stderr and "aws-config.example.sh" in result.stderr


def test_load_aws_config_refuses_a_file_git_would_track(checkout):
    (checkout.root / ".gitignore").write_text("")
    result = checkout.source("load_aws_config; echo reached")
    assert result.returncode != 0
    assert result.stdout == ""
    assert "aws-config.sh" in result.stderr and "gitignored" in result.stderr


def test_load_aws_config_accepts_real_values(checkout):
    result = checkout.source("load_aws_config; echo reached")
    assert (result.returncode, result.stdout) == (0, "reached\n")


def test_init_env_creates_the_settings_files_from_their_examples(checkout):
    for name in SETTINGS_FILES:
        (checkout.lib / name).unlink()
    result = checkout.run("init-env.sh")
    assert result.returncode == 0, result.stderr
    for name in SETTINGS_FILES:
        example = checkout.lib / name.replace(".sh", ".example.sh")
        assert (checkout.lib / name).read_text() == example.read_text()
        assert (checkout.lib / name).stat().st_mode & 0o777 == 0o600
    refused = checkout.run("init-env.sh")
    assert refused.returncode == 1 and "--force" in refused.stderr


# Argument lists each script refuses before it reads any settings or calls AWS or the model.
REFUSED_ARGS = {
    "backup-restore.sh": [
        [],
        ["dev"],
        ["dev", "backup"],
        ["restore"],
        ["backup", "a", "b"],
        ["restore", "--yes"],
        ["--cron", "a", "b"],
    ],
    "aws-helper.sh": [[], ["--logs"], ["--logs", "api", "dev", "extra"], ["--xray", "dev"]],
    "deploy.sh": [["dev"], ["--bogus"]],
    "reset-account.sh": [[], ["dev", "a@x.com"], ["--force", "a@x.com"]],
    "eval-intents.sh": [["extra"], ["--limit", "3"]],
    "eval-readings.sh": [["dev"], ["--limit"]],
    "replay.sh": [[], ["dev", "a@x.com"], ["a@x.com", "--open"], ["--bogus", "a@x.com"]],
}


@pytest.mark.parametrize("script", sorted(REFUSED_ARGS))
def test_help_prints_the_header_and_exits_zero(script):
    result = subprocess.run(
        [SCRIPTS / script, "--help"], check=False, capture_output=True, text=True
    )
    assert result.returncode == 0
    assert f"./scripts/{script}" in result.stdout and not result.stdout.startswith("#")
    assert "set -euo pipefail" not in result.stdout


def test_helper_help_continues_with_the_options_of_this_repo():
    result = subprocess.run(
        [SCRIPTS / "aws-helper.sh", "--help"], check=False, capture_output=True, text=True
    )
    assert result.returncode == 0
    for option in ("--delete-stack", "--set-model-key", "--costs", "--ses-status", "--errors"):
        assert f"  {option}" in result.stdout


@pytest.mark.parametrize(
    ("script", "args"), [(s, a) for s, refused in REFUSED_ARGS.items() for a in refused]
)
def test_wrong_arguments_print_the_usage_and_exit_two(script, args):
    result = subprocess.run([SCRIPTS / script, *args], check=False, capture_output=True, text=True)
    assert result.returncode == 2
    assert f"./scripts/{script}" in result.stderr


@pytest.mark.parametrize(
    "args",
    [
        ["--env", "dev", "--logs", "api"],
        ["--set-model-key"],
        ["--set-model-key", "google"],
        ["--set-model-key", "openai", "extra"],
        ["--log-group"],
        ["--costs", "dev"],
        ["--errors", "--follow"],
        ["--all-logs", "dev"],
        ["--all-logs", "--errors", "--filter", "x"],
        ["--ses-status", "dev"],
    ],
)
def test_helper_refuses_wrong_arguments_of_its_own_options_before_any_call(checkout, args):
    result = checkout.helper(*args)
    assert result.returncode == 2
    assert "./scripts/aws-helper.sh" in result.stderr
    assert checkout.aws_calls() == []


def test_helper_works_on_the_main_stack_pair_from_main(checkout):
    result = checkout.helper("--log-group", "api")
    assert (result.returncode, result.stdout) == (0, "/aws/lambda/group-of-fn-ApiFunction\n")
    calls = checkout.calls.read_text()
    assert "--stack-name meters-tracker " in calls
    assert "--stack-name meters-tracker-cognito " in calls


def test_helper_works_on_the_branch_stack_pair_from_another_branch(branch_checkout):
    result = branch_checkout.helper("--log-group", "presignup")
    assert (result.returncode, result.stdout) == (0, "/aws/lambda/group-of-fn-PreSignupFunction\n")
    calls = branch_checkout.calls.read_text()
    assert "--stack-name meters-tracker-fb " in calls
    assert "--stack-name meters-tracker-fb-cognito " in calls


def test_helper_lists_the_functions_of_both_stacks(checkout):
    result = checkout.helper("--functions")
    assert result.returncode == 0, result.stderr
    rows = [line.split() for line in result.stdout.splitlines()]
    assert ["ApiFunction", "fn-ApiFunction", "python3.13"] in rows
    assert ["PreSignupFunction", "fn-PreSignupFunction", "python3.13"] in rows


@pytest.mark.parametrize("action", ["--logs", "--log-group", "--functions"])
def test_helper_stops_on_a_missing_stack(checkout, action):
    missing = "Stack with id meters-tracker does not exist"
    args = [action] if action == "--functions" else [action, "api"]
    result = checkout.helper(*args, stderr=missing, status=254)
    assert result.returncode != 0
    assert missing in result.stderr
    assert not any(call.startswith("logs tail") for call in checkout.aws_calls())


def test_helper_stops_on_a_function_the_stacks_do_not_hold(checkout):
    result = checkout.helper("--logs", "reports")
    assert result.returncode == 1
    assert "--functions" in result.stderr
    assert not any(call.startswith("logs tail") for call in checkout.aws_calls())


def test_helper_tails_the_resolved_log_group(checkout):
    result = checkout.helper("--logs", "api", "--errors")
    assert result.returncode == 0, result.stderr
    assert checkout.aws_calls()[-1].startswith("logs tail /aws/lambda/group-of-fn-ApiFunction ")


def _model_call_event(timestamp, kind, cost, **cached):
    line = {
        "event": "model_call",
        "kind": kind,
        "model": "anthropic:claude-opus-5-5",
        "input_tokens": 1000,
        **cached,
        "output_tokens": 100,
        "seconds": 2.5,
        "cost": cost,
    }
    record = {"timestamp": timestamp, "level": "INFO", "message": json.dumps(line)}
    return f"{timestamp[:19]} {json.dumps(record)}\n"


def test_helper_sums_the_model_calls_by_day_and_kind(checkout):
    events = (
        _model_call_event("2026-11-02T22:00:00Z", "read_meter", 0.01)
        + _model_call_event("2026-11-03T08:00:00Z", "classify", 0.02)
        + _model_call_event(
            "2026-11-03T09:00:00Z", "classify", 0.03, cache_read_tokens=3000, cache_write_tokens=500
        )
    )
    result = checkout.helper("--costs", "--since", "2d", logs=events)
    assert result.returncode == 0, result.stderr
    tail = checkout.aws_calls()[-1]
    assert tail.startswith("logs tail /aws/lambda/group-of-fn-ApiFunction ")
    assert "--since 2d" in tail and "--filter-pattern model_call" in tail
    rows = [line.split() for line in result.stdout.splitlines()]
    assert ["2026-11-02", "1", "1000", "0", "0", "100", "0.0100"] in rows
    assert ["2026-11-03", "2", "2000", "3000", "500", "200", "0.0500"] in rows
    assert ["classify", "2", "2000", "3000", "500", "200", "0.0500"] in rows
    assert ["read_meter", "1", "1000", "0", "0", "100", "0.0100"] in rows
    assert ["total", "3", "3000", "3000", "500", "300", "0.0600"] in rows
    assert "PRICE_PER_MILLION in src/common/model.py" in result.stderr


def test_helper_lists_each_address_with_its_verification_status(checkout):
    result = checkout.helper("--ses-status", stdout="a@x.com\tSUCCESS\nb@x.com\tPENDING")
    assert result.returncode == 0, result.stderr
    assert "a@x.com\tSUCCESS\nb@x.com\tPENDING" in result.stdout
    calls = checkout.calls.read_text()
    assert "sesv2 list-email-identities " in calls
    assert "sesv2 get-account " in calls


def test_helper_requests_verification_of_each_named_address(checkout):
    result = checkout.helper("--ses-verify", "a@x.com", "b@x.com")
    assert result.returncode == 0, result.stderr
    calls = checkout.aws_calls()
    assert f"ses verify-email-identity {REGION} --email-address a@x.com" in calls
    assert f"ses verify-email-identity {REGION} --email-address b@x.com" in calls
    assert "verification email sent to b@x.com" in result.stdout


@pytest.mark.parametrize("option", ["--ses-verify", "--ses-unverify"])
@pytest.mark.parametrize("addresses", [[], ["a@x.com", "dev"]])
def test_helper_refuses_a_verify_without_addresses_only(checkout, option, addresses):
    result = checkout.helper(option, *addresses)
    assert result.returncode == 2
    assert checkout.aws_calls() == []


def test_helper_unverifies_each_named_address(checkout):
    result = checkout.helper("--ses-unverify", "a@x.com", "b@x.com")
    assert result.returncode == 0, result.stderr
    calls = checkout.aws_calls()
    assert f"ses delete-identity {REGION} --identity a@x.com" in calls
    assert f"ses delete-identity {REGION} --identity b@x.com" in calls
    assert "./scripts/aws-helper.sh --ses-verify b@x.com" in result.stdout


def test_helper_refuses_to_unverify_the_sender(checkout):
    result = checkout.helper("--ses-unverify", "a@x.com", "sender@stub.test")
    assert result.returncode == 1
    assert "SES_SENDER" in result.stderr
    assert checkout.aws_calls() == []


@pytest.mark.parametrize(
    "args, pattern",
    [
        (["--errors"], " --filter-pattern ?ERROR ?WARNING"),
        (["--all-logs", "--errors"], " --filter-pattern ?ERROR ?WARNING"),
        (["--all-logs", "--filter", '"reading saved"'], ' --filter-pattern "reading saved"'),
        (["--all-logs"], ""),
    ],
)
def test_helper_tails_every_function_of_both_stacks(checkout, args, pattern):
    result = checkout.helper(*args, "--since", "1d")
    assert result.returncode == 0, result.stderr
    tails = [call for call in checkout.aws_calls() if call.startswith("logs tail ")]
    assert tails == [
        f"logs tail /aws/lambda/group-of-fn-{name} {REGION} --since 1d --format short{pattern}"
        for name in ("ApiFunction", "PreSignupFunction")
    ]
    assert "== meters-tracker ApiFunction ==" in result.stdout
    assert "== meters-tracker-cognito PreSignupFunction ==" in result.stdout


def test_helper_stores_a_model_key_read_from_stdin(checkout):
    result = checkout.helper("--set-model-key", "openai", stdin="  key-123 \r\n")
    assert result.returncode == 0, result.stderr
    stored = (
        f"ssm put-parameter {REGION} --name /meters-tracker/llm/openai/api-key"
        " --type SecureString --overwrite --value file:///dev/stdin"
    )
    assert checkout.aws_calls() == [stored]
    assert "SSM /meters-tracker/llm/openai/api-key updated" in result.stdout
    assert "key-123" not in result.stdout + result.stderr


def test_helper_stores_no_empty_model_key(checkout):
    result = checkout.helper("--set-model-key", "openai", stdin=" \n")
    assert result.returncode == 1
    assert checkout.aws_calls() == []


def _calls(checkout, prefix):
    return [call for call in checkout.aws_calls() if call.startswith(prefix)]


def test_delete_stack_stops_at_a_bucket_that_holds_objects(checkout):
    result = checkout.helper("--delete-stack", stdin="meters-tracker\n", holds_objects=True)
    assert result.returncode == 1
    assert "s3://meters-tracker-frontend" in result.stderr
    assert "--delete-stack --empty" in result.stderr
    assert "meters-tracker-files" not in result.stderr
    assert _calls(checkout, "s3api delete-objects") == []
    assert _calls(checkout, "cloudformation delete-stack") == []


@pytest.mark.parametrize("args", [["--delete-stack", "--empty"], ["--empty", "--delete-stack"]])
def test_delete_stack_with_empty_empties_the_buckets_and_then_deletes(checkout, args):
    result = checkout.helper(*args, stdin="meters-tracker\n", holds_objects=True)
    assert result.returncode == 0, result.stderr
    calls = checkout.aws_calls()
    emptied = next(i for i, call in enumerate(calls) if call.startswith("s3api delete-objects"))
    deleted = [i for i, call in enumerate(calls) if call.startswith("cloudformation delete-stack")]
    assert "--bucket meters-tracker-frontend " in calls[emptied]
    assert len(deleted) == 2 and emptied < deleted[0]
    assert "--stack-name meters-tracker" in calls[deleted[0]]
    assert "--stack-name meters-tracker-cognito" in calls[deleted[1]]
    batch = json.loads(Path(f"{checkout.calls}.deleted").read_text())
    assert batch["Objects"] == [
        {"Key": "index.html", "VersionId": "v1"},
        {"Key": "old.js", "VersionId": "v2"},
    ]
    # The bucket the template retains is named and never listed or emptied.
    assert "Keeping s3://meters-tracker-files" in result.stdout
    assert not any("meters-tracker-files" in call for call in calls)


def test_delete_stack_deletes_stacks_whose_buckets_are_empty(checkout):
    result = checkout.helper("--delete-stack", stdin="meters-tracker\n")
    assert result.returncode == 0, result.stderr
    assert _calls(checkout, "s3api delete-objects") == []
    assert len(_calls(checkout, "cloudformation delete-stack")) == 2


def test_delete_stack_touches_nothing_unless_the_stack_is_named(checkout):
    result = checkout.helper("--delete-stack", "--empty", stdin="other\n", holds_objects=True)
    assert result.returncode == 1
    assert _calls(checkout, "s3api") == []
    assert _calls(checkout, "cloudformation delete-stack") == []


@pytest.mark.parametrize(
    "args", [["--empty"], ["--empty", "--functions"], ["--delete-stack", "--empty", "dev"]]
)
def test_empty_is_refused_without_delete_stack(checkout, args):
    result = checkout.helper(*args)
    assert result.returncode == 2
    assert checkout.aws_calls() == []


@pytest.mark.parametrize(
    "args, stdin", [(["--prune-layers", "--yes"], None), (["--prune-layers"], "prune\n")]
)
def test_prune_layers_deletes_the_versions_no_function_uses(checkout, args, stdin):
    result = checkout.helper(*args, stdin=stdin)
    assert result.returncode == 0, result.stderr
    assert "--function-version ALL" in _calls(checkout, "lambda list-functions")[0]
    assert "shared:2  created 2026-02-01" in result.stdout
    assert "shared:3" not in result.stdout
    (delete,) = _calls(checkout, "lambda delete-layer-version")
    assert "--layer-name shared --version-number 2" in delete


def test_prune_layers_deletes_nothing_when_every_version_is_used(checkout):
    result = checkout.helper("--prune-layers", used_layer_versions="2 3")
    assert result.returncode == 0, result.stderr
    assert "in use" in result.stdout
    assert _calls(checkout, "lambda delete-layer-version") == []


def test_prune_layers_deletes_nothing_on_a_wrong_confirmation(checkout):
    result = checkout.helper("--prune-layers", stdin="yes\n")
    assert result.returncode == 1
    assert "shared:2" in result.stdout
    assert "nothing deleted" in result.stderr
    assert _calls(checkout, "lambda delete-layer-version") == []


@pytest.mark.parametrize(
    "args",
    [
        ["--no-such-option"],
        ["--log-group"],
        ["--logs"],
        ["--prune-layers", "extra"],
        ["--prune-layers", "--yes", "extra"],
    ],
)
def test_a_usage_error_needs_no_credentials_file(checkout, args):
    (checkout.lib / "aws-config.sh").unlink()
    result = checkout.helper(*args)
    assert result.returncode == 2
    assert checkout.aws_calls() == []


def test_enabling_the_git_hooks_is_reported_outside_the_captured_output(checkout):
    subprocess.run(["git", "-C", checkout.root, "config", "--unset", "core.hooksPath"], check=True)
    result = checkout.helper("--log-group", "api")
    assert (result.returncode, result.stdout) == (0, "/aws/lambda/group-of-fn-ApiFunction\n")
    assert "core.hooksPath" in result.stderr


def test_frontend_deploy_refuses_a_missing_config(checkout):
    result = checkout.run("deploy.sh", "--frontend-only", stdout="https://api-1")
    assert result.returncode == 1
    assert "config.js is missing" in result.stderr and "./scripts/deploy.sh" in result.stderr
    assert not any(call.startswith("s3 ") for call in checkout.aws_calls())


def test_frontend_deploy_refuses_a_config_of_another_stack(checkout):
    public = checkout.root / "frontend" / "public"
    public.mkdir(parents=True)
    (public / "config.js").write_text('window.CONFIG = { apiUrl: "https://api-2" };\n')
    result = checkout.run("deploy.sh", "--frontend-only", stdout="https://api-1")
    assert result.returncode == 1
    assert "does not target meters-tracker's API (https://api-1)" in result.stderr
    assert not any(call.startswith("s3 ") for call in checkout.aws_calls())
