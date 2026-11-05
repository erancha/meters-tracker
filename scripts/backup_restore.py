"""Copies a deployed app stack's data to a local folder and back. The logic behind
scripts/backup-restore.sh, which passes the app stack's name and the backup folder; the tables and
the files bucket are resolved from that stack.

A backup folder holds:
- tables/<name>.jsonl.gz: every row of one app table, one per line, in DynamoDB's typed JSON
- files.tar.gz: the current version of every object in the files bucket, under its key
- files.json: each object's content type by key, which the app reads back with the photo
- metadata.json: when and from which stack the backup was taken, with its row and file counts

A restore makes the stack hold exactly the backup: rows and objects the backup lacks are deleted,
and every row and object it holds is written. The files bucket keeps old versions, so objects a
restore replaces or deletes stay recoverable there.

- backup: writes a backup folder from the stack
- restore: replaces the stack's data with a backup folder's, once the operator types the stack
  name
- main: the command line
"""

import argparse
import gzip
import io
import json
import sys
import tarfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import app_stack

from common.paging import scan_all

TABLES_DIR = "tables"
FILES_ARCHIVE = "files.tar.gz"
FILES_MANIFEST = "files.json"
METADATA = "metadata.json"
NOT_CONFIRMED = "Restore cancelled; nothing was changed."
# The most keys one S3 DeleteObjects request takes.
S3_DELETE_BATCH = 1000


@dataclass
class Stack:
    """A deployed stack's data stores and the clients that reach them."""

    name: str
    # Key: app table name, as Store names it; value: the stack's physical table name
    tables: dict[str, str]
    bucket: str
    dynamodb: object
    s3: object


def resolve(stack: str) -> Stack:
    physical = app_stack.physical_ids(stack)
    return Stack(
        stack,
        app_stack.tables(physical),
        physical["FilesBucket"],
        boto3.resource("dynamodb"),
        boto3.client("s3"),
    )


def _table_path(folder: Path, name: str) -> Path:
    return folder / TABLES_DIR / f"{name}.jsonl.gz"


def _rows(stack: Stack, table: str, **scan) -> list[dict]:
    return scan_all(stack.dynamodb.Table(table), ConsistentRead=True, **scan)


def _objects(stack: Stack) -> list[str]:
    paginator = stack.s3.get_paginator("list_objects_v2")
    return [
        o["Key"]
        for page in paginator.paginate(Bucket=stack.bucket)
        for o in page.get("Contents", [])
    ]


def backup(stack: Stack, folder: Path) -> dict:
    """Writes the stack's rows and files into folder, which must be absent or empty; returns the
    metadata it wrote."""
    if folder.exists() and any(folder.iterdir()):
        raise SystemExit(f"{folder} already holds files; name a new or empty folder")
    (folder / TABLES_DIR).mkdir(parents=True, exist_ok=True)
    rows = {}
    for name, table in stack.tables.items():
        items = _rows(stack, table)
        serialize = TypeSerializer().serialize
        with gzip.open(_table_path(folder, name), "wt", encoding="utf-8") as out:
            for item in items:
                typed = {key: serialize(value) for key, value in item.items()}
                out.write(json.dumps(typed, ensure_ascii=False) + "\n")
        rows[name] = len(items)
    content_types = {}
    with tarfile.open(folder / FILES_ARCHIVE, "w:gz") as archive:
        for key in _objects(stack):
            stored = stack.s3.get_object(Bucket=stack.bucket, Key=key)
            body = stored["Body"].read()
            entry = tarfile.TarInfo(key)
            entry.size = len(body)
            archive.addfile(entry, io.BytesIO(body))
            content_types[key] = stored["ContentType"]
    (folder / FILES_MANIFEST).write_text(json.dumps(content_types, indent=2, ensure_ascii=False))
    metadata = {
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "stack": stack.name,
        "rows": rows,
        "files": len(content_types),
    }
    (folder / METADATA).write_text(json.dumps(metadata, indent=2))
    return metadata


def read_metadata(folder: Path, tables) -> dict:
    """The backup's metadata, after checking that the folder holds every file a restore reads."""
    needed = [METADATA, FILES_ARCHIVE, FILES_MANIFEST]
    needed += [str(_table_path(folder, name).relative_to(folder)) for name in tables]
    missing = [name for name in needed if not (folder / name).is_file()]
    if missing:
        raise SystemExit(f"{folder} is not a complete backup; missing: {', '.join(missing)}")
    return json.loads((folder / METADATA).read_text())


def _restore_table(stack: Stack, folder: Path, name: str):
    table = stack.dynamodb.Table(stack.tables[name])
    key_names = [k["AttributeName"] for k in table.key_schema]
    deserialize = TypeDeserializer().deserialize
    with gzip.open(_table_path(folder, name), "rt", encoding="utf-8") as lines:
        items = [
            {key: deserialize(value) for key, value in json.loads(line).items()} for line in lines
        ]
    kept = {tuple(item[k] for k in key_names) for item in items}
    names = {f"#k{i}": key for i, key in enumerate(key_names)}
    current = _rows(
        stack,
        stack.tables[name],
        ProjectionExpression=", ".join(names),
        ExpressionAttributeNames=names,
    )
    with table.batch_writer() as batch:
        for key in current:
            if tuple(key[k] for k in key_names) not in kept:
                batch.delete_item(Key=key)
    with table.batch_writer() as batch:
        for item in items:
            batch.put_item(Item=item)


def _restore_files(stack: Stack, folder: Path):
    content_types = json.loads((folder / FILES_MANIFEST).read_text())
    stale = [{"Key": key} for key in _objects(stack) if key not in content_types]
    for start in range(0, len(stale), S3_DELETE_BATCH):
        chunk = stale[start : start + S3_DELETE_BATCH]
        response = stack.s3.delete_objects(
            Bucket=stack.bucket, Delete={"Objects": chunk, "Quiet": True}
        )
        if response.get("Errors"):
            raise RuntimeError(f"S3 refused to delete: {response['Errors']}")
    with tarfile.open(folder / FILES_ARCHIVE, "r:gz") as archive:
        for entry in archive:
            body = archive.extractfile(entry).read()
            stack.s3.put_object(
                Bucket=stack.bucket,
                Key=entry.name,
                Body=body,
                ContentType=content_types[entry.name],
            )


def restore(stack: Stack, folder: Path, confirm) -> bool:
    """Replaces the stack's rows and files with the backup's once confirm, called with the
    prompt, returns the stack name; returns whether it restored."""
    metadata = read_metadata(folder, stack.tables)
    prompt = (
        f"Restore replaces every row of the {len(stack.tables)} tables and every file of"
        f" {stack.name} with the backup of {metadata['stack']} taken at"
        f" {metadata['created_at']}: {sum(metadata['rows'].values())} rows,"
        f" {metadata['files']} files.\nType the stack name ({stack.name}) to continue: "
    )
    if confirm(prompt).strip() != stack.name:
        return False
    for name in stack.tables:
        _restore_table(stack, folder, name)
        print(f"{name}: {metadata['rows'][name]} rows")
    _restore_files(stack, folder)
    print(f"files: {metadata['files']}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(prog="backup-restore.sh")
    parser.add_argument("command", choices=["backup", "restore"])
    parser.add_argument("stack")
    parser.add_argument("folder", type=Path)
    args = parser.parse_args()
    stack = resolve(args.stack)
    folder = args.folder.resolve()
    if args.command == "backup":
        print(f"Backing up {stack.name} into {folder}")
        metadata = backup(stack, folder)
        for name, count in metadata["rows"].items():
            print(f"{name}: {count} rows")
        print(f"files: {metadata['files']}")
        print("Backup completed.")
    elif restore(stack, folder, input):
        print(f"Restored {stack.name} from {folder}.")
    else:
        print(NOT_CONFIRMED, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
