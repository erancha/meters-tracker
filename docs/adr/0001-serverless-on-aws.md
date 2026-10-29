# 0001. Serverless on AWS, laid out like diet-tracker

- Status: Accepted
- Date: 2026-10-28

## Context

One building owner photographs a few dozen water and electricity meters every few weeks. Between
those rounds the app has nothing to do, so a server that runs all month would be paid for while
idle. The owner already runs `diet-tracker`, a serverless Hebrew app on AWS whose layout, scripts
and deployment are known and proven, and a reader who knows one of the owner's repositories
should be able to operate the next.

## Decision

- Python 3.13 Lambdas behind an API Gateway HTTP API, with a shared core in `src/common/` and thin
  entry points in `src/handlers/`.
- DynamoDB with one table per kind of record, billed per request.
- A private S3 bucket for uploaded photos; S3 and CloudFront host the browser app.
- Cognito for sign-in (ADR 0002).
- Two CloudFormation stacks, the app and Cognito, deployed by `scripts/deploy.sh`, with the same
  script set and gitignored settings files as `diet-tracker`.
- Third-party libraries (the `anthropic` SDK, `pydantic`) are pinned in `requirements.txt` and
  installed into the Lambda package for the Linux arm64 platform at deploy time.

## Consequences

- Nothing runs between requests, so an idle month costs only storage.
- A request is bounded by the HTTP API's 30-second limit while the Lambda may run 60 seconds;
  a photo the model is still reading when the browser's request is dropped finishes anyway and
  writes its result, which the browser reads back from the chat history.
- Work that must happen without a request (a daily reminder) needs a scheduler of its own.
- Unlike `diet-tracker`, the package carries third-party wheels, so `deploy.sh` builds it with
  pip for the Lambda's platform instead of copying the source alone.
