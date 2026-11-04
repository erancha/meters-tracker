# 0009. The admin is a stack parameter

- Status: Accepted
- Date: 2026-11-05

## Context

Every address that signed in was an owner or a delegate, and both act inside one account, so no
one could hold a deployment-wide role in the app. The `role` field of the `access` table
describes an address's place in one account, so it cannot also mark such a role. The operator
wants an admin identity that manages no meters today, as the place for deployment-wide
operations to come, with as little of the ordinary path blocked as possible.

## Decision

`scripts/params.sh` names the admin in `ADMIN_EMAIL`; `scripts/deploy.sh` stops before any change
when it is missing, and passes it to the api Lambda through the `AdminEmail` stack parameter.
`access.is_admin` compares the signed-in address with it, ignoring case, independently of the
`access` table. The admin keeps its ordinary owner or delegate account; the chat response flags
it, and the frontend disables the composer on that flag. The backend refuses nothing, so a later
admin operation rides the same turn path as any other message.

## Consequences

- Changing the admin is an edit to `params.sh` and a deploy; no table row moves.
- The lock is in the browser only: a hand-made request from the admin acts in its ordinary
  account, as any owner's would.
- Admin operations, when added, ask the same `is_admin` check inside the turn; anything they
  show about other accounts is decided per operation.
