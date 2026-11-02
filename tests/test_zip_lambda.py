"""Builds the Lambda package twice from the same files, as two deploys would."""

import os
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "zip_lambda.py"


def package(source, target):
    subprocess.run([sys.executable, SCRIPT, source, target], check=True)
    return target.read_bytes()


def write_tree(root, names, mtime):
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)
        os.utime(path, (mtime, mtime))


def test_same_files_give_identical_bytes_whatever_their_times_and_creation_order(tmp_path):
    names = ["handlers/api.py", "app.json", "common/store.py"]
    write_tree(tmp_path / "first", names, 1_000_000_000)
    write_tree(tmp_path / "second", reversed(names), 1_700_000_000)
    first = package(tmp_path / "first", tmp_path / "first.zip")
    assert first == package(tmp_path / "second", tmp_path / "second.zip")


def test_a_changed_file_changes_the_package(tmp_path):
    write_tree(tmp_path / "src", ["a.py"], 1_000_000_000)
    before = package(tmp_path / "src", tmp_path / "out.zip")
    (tmp_path / "src" / "a.py").write_text("changed")
    assert before != package(tmp_path / "src", tmp_path / "out.zip")


def test_entries_are_sorted_with_paths_relative_to_the_root_and_keep_the_executable_bit(tmp_path):
    write_tree(tmp_path / "src", ["b/run", "a.py"], 1_000_000_000)
    (tmp_path / "src" / "b" / "run").chmod(0o700)
    (tmp_path / "src" / "a.py").chmod(0o600)
    package(tmp_path / "src", tmp_path / "out.zip")
    with zipfile.ZipFile(tmp_path / "out.zip") as archive:
        modes = {i.filename: stat.S_IMODE(i.external_attr >> 16) for i in archive.infolist()}
    assert list(modes.items()) == [("a.py", 0o644), ("b/run", 0o755)]
