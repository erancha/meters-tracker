# 0006. Readings and photos outlive the stack that created them

- Status: Accepted
- Date: 2026-10-28

## Context

The readings are the owner's billing record, and a lost reading cannot be photographed again.
CloudFormation deletes a stack's tables and buckets with the stack, and replaces a table when a
key change forces it, which loses its contents. A redeploy after a stack delete would create
empty tables under new generated names, and the app would start with no data while the old
tables still existed beside it.

## Decision

- Every DynamoDB table is retained when its stack is deleted or the resource is replaced, has
  deletion protection on, and keeps point-in-time recovery.
- The photo bucket is retained, versioned, and expires a noncurrent version after 30 days, so an
  overwritten or deleted photo stays recoverable for that long.
- `scripts/delete-stack.sh` deletes both stacks and never the retained resources.
- `scripts/deploy.sh` refuses to create the app stack while tables or a photo bucket of an
  earlier stack of the same name exist, and stops on an AWS error rather than assume there is
  no stack.
- Recovery is a CloudFormation resource import of the retained tables and bucket into the new
  stack; removing them for good is a manual step that `delete-stack.sh --help` describes.

## Consequences

- Deleting a stack never deletes data, and a redeploy never silently starts empty.
- Retained resources cost storage until someone removes them by hand.
- Bringing a deleted stack back takes a resource import, not just `deploy.sh`.
- The frontend bucket holds only build output and is deleted with its stack.
