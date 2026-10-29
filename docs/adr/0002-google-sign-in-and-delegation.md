# 0002. Google-only sign-in; an address owns its account or works in exactly one other

- Status: Accepted
- Date: 2026-10-28

## Context

The owner signs in with a Google address and sometimes sends someone else to photograph the
meters. That person must work in the owner's data, not in an account of their own, and an
address may act for at most one owner. Any other address that signs in becomes the owner of its
own, empty account. No password handling is wanted.

## Decision

- Cognito user pool with Google as the only identity provider. Every Google address may sign up;
  the pre-sign-up Lambda only asks SES to verify the address, so mail can reach it later while
  the SES account is sandboxed.
- An `access` table keyed by the lowercase address holds `account` and `role` (`owner` or
  `delegate`). Because the address is the key, it can name only one account.
- The account id is the owner's lowercase email address. Every data table's partition key
  starts with it.
- The API resolves the JWT's email on every request: a row maps it to its account; an address
  with no row gets an owner row for its own account, written only if still absent, so two first
  requests at once agree.
- A delegate can do everything an owner can except manage delegates. Reports always go to the
  owner's address.
- The chat history and the queue of pending confirmations belong to the signed-in address, the
  data to the account, so an owner and a delegate each keep their own conversation over the same
  readings.

## Consequences

- No user list to maintain and no allowlist: anyone can sign in, and sees only their own account.
- Turning an address that already owns data into a delegate must be refused, since one key
  cannot hold both roles.
- Removing a delegate deletes its row; its next sign-in makes it the owner of an empty account.
