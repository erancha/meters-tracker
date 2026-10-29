# 0004. A fixed email subject per apartment, so Gmail threads the reports

- Status: Accepted
- Date: 2026-10-28

## Context

After every confirmed reading that has a previous one, the owner is to receive a report by
email, with a PDF of the apartment's period. Over a year that is dozens of messages for each
apartment. The owner reads mail in Gmail, which groups messages into one conversation when their
subjects match. A subject carrying the date would scatter each apartment's reports into separate
conversations and bury the latest one. The report and its email are not built when this decision
is taken; it fixes the subject they will use.

## Decision

- The subject is fixed per apartment: `[<property> / <apartment>] קריאות מונים` ("meter
  readings"), with no date.
- The date of the reading and whether the figures are final or a forecast go in the body and the
  PDF, never the subject.

## Consequences

- Each apartment's reports form one Gmail conversation, newest last, with the history above it.
- Renaming a property or an apartment starts a new conversation.
- A re-closed period's corrected report lands in the same conversation as the report it
  replaces, so the body has to name the report to ignore.
