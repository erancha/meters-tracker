"""Deletes everything one account owns in a deployed app stack, so the owner's story can start
again from an empty account. The logic behind scripts/tools/reset-account.sh, which passes the app
stack's name; the tables and the files bucket are resolved from that stack.

An account's rows are keyed by its address: the pk is the address itself (access, properties,
apartments, meters, tariffs, and the owner's own pending items and messages) or starts with the
address and a "#" (periods by apartment, readings by meter). Its photos are the files bucket's
objects under "<address>/", every version included, since the bucket keeps old versions.

- plan: the keys of every row and photo version the account owns, read by a scan of each table
  and a listing of the bucket prefix
- describe: the plan as the count per table and the count of photo versions
- execute: deletes the plan's rows and photo versions
- reset: prints the plan, then deletes it when confirmed
- main: reset, confirmed only by --yes
"""

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import boto3
from boto3.dynamodb.conditions import Attr

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
# app_stack.py stays in scripts/, beside deploy.sh, which uses it too.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app_stack

from common.paging import scan_all

NOT_CONFIRMED = "Nothing was deleted; run again with --yes to delete the above."
# The most keys one S3 DeleteObjects request takes.
S3_DELETE_BATCH = 1000


@dataclass
class Plan:
    # Key: app table name; value: the key attributes of each row the account owns there
    rows: dict[str, list[dict]]
    # Key and VersionId of every object version and delete marker under the account's prefix
    files: list[dict]


def _owned_keys(table, address) -> list[dict]:
    key_names = [k["AttributeName"] for k in table.key_schema]
    names = {f"#k{i}": name for i, name in enumerate(key_names)}
    owned = Attr("pk").eq(address) | Attr("pk").begins_with(f"{address}#")
    items = scan_all(
        table,
        FilterExpression=owned,
        ProjectionExpression=", ".join(names),
        ExpressionAttributeNames=names,
        ConsistentRead=True,
    )
    return [{name: item[name] for name in key_names} for item in items]


def _versions(s3, bucket, address) -> list[dict]:
    files = []
    paginator = s3.get_paginator("list_object_versions")
    for page in paginator.paginate(Bucket=bucket, Prefix=f"{address}/"):
        for entry in [*page.get("Versions", []), *page.get("DeleteMarkers", [])]:
            files.append({"Key": entry["Key"], "VersionId": entry["VersionId"]})
    return files


def plan(tables: dict, s3, bucket: str, address: str) -> Plan:
    """tables maps each app table name to its boto3 Table."""
    rows = {name: _owned_keys(table, address) for name, table in tables.items()}
    return Plan(rows, _versions(s3, bucket, address))


def describe(planned: Plan, address: str) -> str:
    lines = [f"{name}: {len(keys)}" for name, keys in planned.rows.items()]
    lines.append(f"files under {address}/: {len(planned.files)}")
    return "\n".join(lines)


def execute(planned: Plan, tables: dict, s3, bucket: str):
    for name, keys in planned.rows.items():
        with tables[name].batch_writer() as batch:
            for key in keys:
                batch.delete_item(Key=key)
    for start in range(0, len(planned.files), S3_DELETE_BATCH):
        chunk = planned.files[start : start + S3_DELETE_BATCH]
        response = s3.delete_objects(Bucket=bucket, Delete={"Objects": chunk, "Quiet": True})
        if response.get("Errors"):
            raise RuntimeError(f"S3 refused to delete: {response['Errors']}")


def resources(stack: str) -> tuple[dict, str]:
    """The boto3 Table of each app table, and the files bucket's name."""
    physical = app_stack.physical_ids(stack)
    dynamodb = boto3.resource("dynamodb")
    tables = {name: dynamodb.Table(table) for name, table in app_stack.tables(physical).items()}
    return tables, physical["FilesBucket"]


def account_address(email: str) -> str:
    """The key an account's rows are stored under: the lowercase address the sign-in resolves
    to."""
    return email.lower()


def reset(stack: str, address: str, confirmed: bool) -> bool:
    """Prints what the account owns in the stack, then deletes it when confirmed; returns
    whether it deleted."""
    tables, bucket = resources(stack)
    s3 = boto3.client("s3")
    planned = plan(tables, s3, bucket, address)
    print(f"{stack}, account {address}:")
    print(describe(planned, address))
    if confirmed:
        execute(planned, tables, s3, bucket)
        print(f"Deleted everything {address} owned in {stack}.")
    return confirmed


def main() -> None:
    parser = argparse.ArgumentParser(prog="reset-account.sh", usage="%(prog)s <email> [--yes]")
    parser.add_argument("stack")
    parser.add_argument("email")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    if not reset(args.stack, account_address(args.email), args.yes):
        print(NOT_CONFIRMED, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
