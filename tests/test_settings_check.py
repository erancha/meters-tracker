"""Exercises the check that keeps the email addresses of the private settings file out of git: the
pre-commit hook on a staged snapshot, and assert_addresses_untracked of scripts/lib/app-env.sh on
the tracked files.

Each test runs in a scratch git checkout holding the repo's app-env.sh and hook, stub secrets
files, and a stub lib/params.sh whose addresses no tracked file holds, plus one address for each
setting the repo's PUBLISHED_SETTINGS names.
"""

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
APP_ENV = "scripts/lib/app-env.sh"
HOOK = ".githooks/pre-commit"
IDENTITY = ["-c", "user.name=test", "-c", "user.email=test@example.invalid"]


def _published_settings():
    script = f'SCRIPT_DIR="{REPO / "scripts"}"; source "{REPO / APP_ENV}"; '
    script += 'printf "%s\\n" "${PUBLISHED_SETTINGS[@]}"'
    result = subprocess.run(["bash", "-c", script], check=True, capture_output=True, text=True)
    return result.stdout.split()


PUBLISHED = _published_settings()
PARAMS = "".join(f"export {name}={name.lower()}@published.test\n" for name in PUBLISHED) + (
    "export ALLOWED_EMAILS='user\\.one@stub\\.test|two@stub\\.test'\n"
    "export LIMITS='{\"three@stub.test\": 9}'\n"
    "export EVERYONE='.*'\n"
)


class Checkout:
    def __init__(self, root):
        self.root = root
        for name in (APP_ENV, HOOK):
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text((REPO / name).read_text())
        (root / HOOK).chmod(0o755)
        lib = root / "scripts" / "lib"
        (lib / "aws-config.sh").write_text("export AWS_ACCESS_KEY_ID=stub-key-id\n")
        (lib / "secrets.sh").write_text("export STUB_SECRET=stub-secret-value\n")
        self.params = lib / "params.sh"
        self.params.write_text(PARAMS)
        (root / ".gitignore").write_text(
            "/scripts/lib/aws-config.sh\n/scripts/lib/secrets.sh\n/scripts/lib/params.sh\n"
        )
        self.git("init", "--quiet", "--initial-branch", "main")
        self.git("add", "--all")
        self.git(*IDENTITY, "commit", "--quiet", "--no-verify", "-m", "x")
        self.git("config", "core.hooksPath", ".githooks")

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.root), *args], check=False, capture_output=True, text=True
        )

    def commit(self, text):
        """Stages notes.txt holding text and commits it through the hook."""
        (self.root / "notes.txt").write_text(text)
        self.git("add", "notes.txt")
        return self.git(*IDENTITY, "commit", "--quiet", "-m", "notes")

    def check_tracked(self, published=None):
        """Runs assert_addresses_untracked on the tracked files, with PUBLISHED_SETTINGS replaced
        when published is given."""
        override = "" if published is None else f"PUBLISHED_SETTINGS=({published}); "
        script = (
            f'set -euo pipefail; SCRIPT_DIR="{self.root / "scripts"}"; '
            f'source "$SCRIPT_DIR/lib/app-env.sh"; {override}'
            f'assert_addresses_untracked "{self.params}" worktree; echo passed'
        )
        return subprocess.run(["bash", "-c", script], check=False, capture_output=True, text=True)


@pytest.fixture
def checkout(tmp_path):
    return Checkout(tmp_path / "checkout")


def test_the_repo_checks_its_private_settings_file():
    script = f'SCRIPT_DIR="{REPO / "scripts"}"; source "{REPO / APP_ENV}"; '
    script += 'printf "%s\\n" "${PRIVATE_FILES[@]}"'
    result = subprocess.run(["bash", "-c", script], check=True, capture_output=True, text=True)
    assert result.stdout == f"{REPO / 'scripts' / 'lib' / 'params.sh'}\n"


@pytest.mark.parametrize(
    ("text", "setting"),
    [
        ("ask user.one@stub.test\n", "ALLOWED_EMAILS"),
        ("ask User.One@Stub.Test\n", "ALLOWED_EMAILS"),
        ("ask two@stub.test\n", "ALLOWED_EMAILS"),
        ("limit of three@stub.test\n", "LIMITS"),
    ],
)
def test_a_commit_staging_a_private_address_is_refused(checkout, text, setting):
    result = checkout.commit(text)
    assert result.returncode != 0
    assert setting in result.stderr and "notes.txt" in result.stderr
    assert "stub.test" not in result.stderr.lower()
    assert checkout.git("log", "--oneline").stdout.count("\n") == 1


def test_a_commit_holding_the_other_private_values_passes(checkout):
    assert checkout.commit("matches .* and {} everywhere\n").returncode == 0


def test_an_address_a_published_setting_holds_may_appear_in_git(checkout):
    (checkout.root / "notes.txt").write_text("ask three@stub.test\n")
    checkout.git("add", "notes.txt")
    assert checkout.check_tracked().returncode != 0
    checkout.params.write_text(PARAMS + "export SENDER=three@stub.test\n")
    result = checkout.check_tracked(published="SENDER")
    assert (result.returncode, result.stdout) == (0, "passed\n")


def test_an_at_sign_outside_a_full_address_is_refused(checkout):
    checkout.params.write_text(PARAMS + "export GROUPED='(four|five)@stub\\.test'\n")
    result = checkout.check_tracked()
    assert result.returncode != 0
    assert "GROUPED" in result.stderr and "in full" in result.stderr
