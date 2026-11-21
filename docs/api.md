# API endpoints

This document details every route behind the entry points that
[Architecture](architecture.md#the-entry-points) summarizes: who may call it, what it takes and
answers, and its limits. The browser reaches the app through one HTTP API, whose routes one
function serves, the api function. What the app does with the turn a route takes is in
[The conversation engine](conversation.md).

The HTTP API's Cognito authorizer checks the caller's token on every route.

## The api function

The api function (`src/handlers/api.py`, the api Lambda) answers three routes, each after
resolving the JWT's email to an account ([Sign-in](sign-in.md#accounts-and-roles)):

- `GET /chat` — the signed-in address's messages of one day, at most the latest 200, oldest
  first, and the snapshot
  ([Every flow is a stored state machine](design-patterns.md#every-flow-is-a-stored-state-machine)).
  The optional `day` query (`YYYY-MM-DD`) names the day, today by default; a malformed one is a
  400 `invalid_request`. Today's response also carries `earlier_failed`, the photos of earlier
  days still marked failed, read by a filtered query of the address's messages; it is empty for
  an earlier day. An account's first load adds a welcome, and a day's first load may add a
  greeting ([Period lifecycle](domain-rules.md#period-lifecycle)).
- `POST /turn` — one user action, exactly one of `{"text"}`, `{"image", "media_type", "note"?}`
  with a base64 JPEG or PNG of at most 4 MiB, or `{"choice": {"pending", "version", "option"}}`
  naming the card's pending item, its version and one of its options, or `{"retry"}` naming a
  failed message by its stamp. It returns the replies, the new snapshot, and for a photo its
  stored key (`image_key`), so the browser can show its own copy as the stored photo. A malformed
  body is a 400 (`invalid_request`, `invalid_image`, `image_too_large`,
  `unsupported_image_type`), checked before anything is stored. A choice whose card the item no
  longer shows, or whose option that card lacks, is a 409 `already_handled` carrying the current
  snapshot; so is the second of two requests answering one card at once. A text or photo is
  stored before it is handled. When handling fails for any reason, the cause is logged as an
  error, the message is marked `failed` and the answer is a 503 `message_failed`. A failed photo
  leaves no pending item of its own. A retry handles the same message again from its stored copy
  and records `retried_at`; a message not marked failed is a 409 `already_handled`, so two
  retries of one failure run once.
- `GET /files/{key+}` — a presigned GET URL, valid for `file_url_minutes`, for a photo whose
  key starts with the caller's account; any other key is a 403.

The function's time limit is 60 seconds, twice the HTTP API's 30-second limit on the browser's
request, so a photo the model is still reading when the request is dropped is still saved as a
pending card that the next `GET /chat` returns. The template defines the limit once and passes it to
the function as `LAMBDA_TIMEOUT_SECONDS`. Each model attempt gives up after 25 seconds, with one
retry. A photo still marked as being read once the limit has passed belonged to a killed invocation,
so the next snapshot turns it into an unreadable card asking for a retake, and the next photo sent
after that card replaces it.
