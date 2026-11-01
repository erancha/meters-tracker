"""Runs the shell helpers of scripts/common.sh against a fake aws command that prints a canned
answer, so the stack lookups' handling of empty, missing and failed results is checked offline.
Also runs the scripts that act on a deployed stack or the real model with --help and with
arguments they refuse, both of which stop before any settings file or AWS call."""

import subprocess
from pathlib import Path

import pytest

COMMON = Path(__file__).resolve().parent.parent / "scripts" / "common.sh"
FAKE_AWS = """#!/usr/bin/env bash
printf '%s' "$FAKE_STDOUT"
printf '%s' "$FAKE_STDERR" >&2
exit "$FAKE_STATUS"
"""


@pytest.fixture
def run(tmp_path):
    aws = tmp_path / "aws"
    aws.write_text(FAKE_AWS)
    aws.chmod(0o755)

    def run(snippet, stdout="", stderr="", status=0):
        env = {
            "PATH": f"{tmp_path}:/usr/bin:/bin",
            "FAKE_STDOUT": stdout,
            "FAKE_STDERR": stderr,
            "FAKE_STATUS": str(status),
        }
        script = f'source "{COMMON}"\n{snippet}\n'
        return subprocess.run(
            ["bash", "-c", script], check=False, env=env, capture_output=True, text=True
        )

    return run


def test_stack_query_keeps_a_warning_out_of_the_value(run):
    result = run(
        'value=$(stack_output app ApiUrl); echo "[$value]"',
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
    assert "./scripts/set-model-key.sh openai" in result.stderr


def test_require_model_key_passes_on_a_failed_lookup(run):
    result = run("require_model_key openai", stderr="AccessDenied", status=254)
    assert result.returncode == 1
    assert "AccessDenied" in result.stderr
    assert "set-model-key" not in result.stderr


def test_require_params_names_the_missing_setting(run, tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "params.sh").write_text(
        "export GOOGLE_CLIENT_ID=id\nexport GOOGLE_CLIENT_SECRET=secret\n"
    )
    result = run(f'ROOT="{tmp_path}"; require_params; echo reached')
    assert result.returncode != 0
    assert result.stdout == ""
    assert "LLM_MODEL" in result.stderr and "params.example.sh" in result.stderr


def test_require_params_refuses_a_placeholder_left_from_the_example(run, tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "params.sh").write_text(
        'export GOOGLE_CLIENT_ID="<google-oauth-client-id>"\n'
        "export GOOGLE_CLIENT_SECRET=secret\nexport LLM_MODEL=anthropic:claude-opus-5-5\n"
    )
    result = run(f'ROOT="{tmp_path}"; require_params; echo reached')
    assert result.returncode != 0
    assert result.stdout == ""
    assert "GOOGLE_CLIENT_ID" in result.stderr and "placeholder" in result.stderr
    assert "google-oauth-client-id" not in result.stderr


def test_require_aws_config_refuses_a_placeholder_key(run, tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "aws-config.sh").write_text(
        'export AWS_ACCESS_KEY_ID="<access-key-id>"\n'
        "export AWS_SECRET_ACCESS_KEY=s\nexport AWS_DEFAULT_REGION=eu-central-1\n"
    )
    result = run(f'HOME="{tmp_path}"; ROOT="{tmp_path}"; require_aws_config; echo reached')
    assert result.returncode != 0
    assert result.stdout == ""
    assert "AWS_ACCESS_KEY_ID" in result.stderr and "aws-config.example.sh" in result.stderr


def test_require_aws_config_accepts_real_values(run, tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "aws-config.sh").write_text(
        "export AWS_ACCESS_KEY_ID=AKIAEXAMPLE\n"
        "export AWS_SECRET_ACCESS_KEY=s\nexport AWS_DEFAULT_REGION=eu-central-1\n"
    )
    result = run(f'HOME="{tmp_path}"; ROOT="{tmp_path}"; require_aws_config; echo reached')
    assert (result.returncode, result.stdout) == (0, "reached\n")


SCRIPTS = COMMON.parent
# Argument lists each script refuses before it reads any settings or calls AWS or the model.
REFUSED_ARGS = {
    "reset-account.sh": [[], ["dev", "a@x.com", "extra"], ["--force", "a@x.com"]],
    "set-model-key.sh": [[], ["google"], ["openai", "extra"]],
    "eval-intents.sh": [["extra"], ["--limit", "3"]],
    "replay.sh": [[], ["dev", "a@x.com", "extra"], ["a@x.com", "--open"], ["--bogus", "a@x.com"]],
}


@pytest.mark.parametrize("script", sorted(REFUSED_ARGS))
def test_help_prints_the_header_and_exits_zero(script):
    result = subprocess.run(
        [SCRIPTS / script, "--help"], check=False, capture_output=True, text=True
    )
    assert result.returncode == 0
    assert result.stdout.splitlines()[0] and not result.stdout.startswith("#")
    assert "set -euo pipefail" not in result.stdout


@pytest.mark.parametrize(
    ("script", "args"), [(s, a) for s, refused in REFUSED_ARGS.items() for a in refused]
)
def test_wrong_arguments_print_the_usage_and_exit_two(script, args):
    result = subprocess.run([SCRIPTS / script, *args], check=False, capture_output=True, text=True)
    assert result.returncode == 2
    assert f"./scripts/{script}" in result.stderr
