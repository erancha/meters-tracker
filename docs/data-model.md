# Data model

This document details the tables and the files bucket that [Architecture](architecture.md#storage)
summarizes: each table's keys and attributes, where photos are stored, and how the data outlives a
stack. Every table is read and written through `src/common/store.py` alone, with strongly consistent
reads ([Data access behind one gateway](design-patterns.md#data-access-behind-one-gateway)), and the
files bucket through `src/common/files.py`.

Nine DynamoDB tables, billed per request, each holding one kind of record under a string partition
key `pk` and sort key `sk` (`access` has no sort key). The account id is the owner's lowercase
email. No request of the app crosses accounts or scans a table. Scripts run by the owner with their
own credentials scan: `scripts/tools/reset-account.sh` finds every row one account owns in each
table, `scripts/tools/backup-restore.sh` copies every table and the files bucket to a local folder
and back, and a real-model check reads the readings that carry failed photos
([Tests](development.md#backend-tests)).

- `access` — pk address, no sk: `email`, `account`, `role` (`owner` or `delegate`).
- `properties` — pk account, sk id: `name`.
- `apartments` — pk account, sk id: `property_id`, `name`, `period_start`, `period_months`.
- `meters` — pk account, sk serial: `apartment_id`, `type` (`water` or `electricity`),
  `display_choice`, `label_hint`, `retired_at`.
- `periods` — pk `account#apartment_id`, sk start date: `months`, `closed_on`,
  `last_reminded_on`, and while its report waits to be sent again, `report_failed` and
  `report_ignored`.
- `readings` — pk `account#serial`, sk `date#seq`: `value`, `image_key`, `recorded_by`,
  `opens_period`, `closes_period`, `resets`, and, only when non-empty, `failed_photos` (the
  keys of the photos the model failed on) and `model` (the model that failed).
- `tariffs` — pk account, sk effective date: `electricity_per_kwh`, `water_per_m3` (either
  null when that type is unpriced), `source_image_key` (the statement photo, or null when
  typed).
- `pending` — pk address, sk seq: `kind`, `status`, `step`, `version`, `data`.
- `messages` — pk address, sk timestamp: `role`, `text`, `card`, `image_key`, `pdf_key`,
  `choice`. A choice is recorded as a user message whose `text` is the option's label and whose
  `choice` is `{pending, version}` of the card it answered; on any other message it is null.
  A text or photo whose handling failed also holds `failed`, until a retry clears it, and a
  retried one holds `retried_at`, the stamp of its latest retry. A meter photo's message holds
  `reading` once the model has answered: `state` (`awaiting`, `saved` or `dropped`), `meter_type`,
  `apartment` and `property`, updated each time the photo's flow shows a card or ends.

Photos are stored at `<account>/images/<seq>.jpg` (or `.png`) in the files bucket; the account
prefix is what `GET /files/{key+}` checks.

The tables and the files bucket survive a stack delete: tables are retained, deletion-protected and
point-in-time recoverable; the bucket is retained and versioned, and a noncurrent version expires
after 30 days. `deploy.sh` refuses to create the app stack while such leftovers exist
([ADR 0006](adr/0006-retain-data-on-delete.md)).
