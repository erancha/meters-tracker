"""The deployed app stack's resources by their template logical ids, for the scripts that work on
a deployed stack: the nine app tables, the files bucket and the api function."""

import boto3

from common.store import TABLE_NAMES


def physical_ids(stack: str) -> dict[str, str]:
    """Logical id -> physical id of every resource in the stack."""
    resources = boto3.client("cloudformation").describe_stack_resources(StackName=stack)
    return {r["LogicalResourceId"]: r["PhysicalResourceId"] for r in resources["StackResources"]}


def tables(physical: dict[str, str]) -> dict[str, str]:
    """App table name (as Store names it) -> the stack's physical table name."""
    return {name: physical[f"{name.capitalize()}Table"] for name in TABLE_NAMES}
