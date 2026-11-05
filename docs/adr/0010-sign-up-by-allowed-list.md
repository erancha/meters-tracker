# 0010. Sign-up is limited to an allowed list kept in the deploy settings

- Status: Accepted
- Date: 2026-11-05

## Context

ADR 0002 let every Google address sign up and open an account of its own. The operator wants
to decide which addresses get in, keeping the list by hand and changing it with a deploy.
Delegation from ADR 0002 stays as it is.

## Decision

`scripts/params.sh` holds `ALLOWED_EMAILS`, one regex a Google address must fully match,
ignoring case, to sign up; alternation lists several addresses and `.*` admits everyone.
`scripts/deploy.sh` stops before any change when it is missing, and passes it to the Cognito
stack as the `AllowedEmails` parameter. The presignup Lambda refuses an address that does not
match by failing, so Cognito turns the sign-up away and the browser shows the Hebrew reason.
This copies diet-tracker's gate, so both repositories take the same setting.

## Consequences

- Admitting an address is an edit to `params.sh` and a deploy; no table changes.
- Only a first sign-in runs the check. Removing an address from the list does not lock out an
  account Cognito already holds; deleting that user from the pool does.
- The admin and every delegate must be on the list before their first sign-in. An owner can add
  a delegate in the chat, but the delegate cannot sign in until the operator lists the address.
