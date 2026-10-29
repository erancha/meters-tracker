# meters-tracker

A Hebrew chat for a building owner who bills each apartment for the water and electricity it
uses. The owner sends a photo of a meter; the app reads the serial and the counter, proposes the
reading, and saves it once the owner confirms. It keeps each meter's history and each apartment's
billing periods, and after every reading reports the consumption, the forecast to the period's
end and the charge.

**[Walk through the planned chat](docs/demo/demo.html)** — an animated replay of one owner's
story on a phone screen, from linking an apartment's meters to the closing readings two months
later; it shows the whole planned product, including the parts not built yet. Open the file in a
browser.

## Overview

- Everything happens in one chat, in Hebrew. There are no forms: the app proposes, and the owner
  confirms with a button or corrects it in plain words ("the reading is 39269").
- A photo of a water or electricity meter is read for its serial number and its counter. Nothing
  is saved until the owner confirms, and a photo the app cannot read with certainty is never
  guessed at: it asks for a retake or a typed value.
- A meter seen for the first time is linked to its property and apartment, which the chat
  creates by name when they are new. A new meter in an apartment that already has one of the
  same kind replaces it.
- An electricity display that shows two numbers is asked about once; the app remembers which
  number counts for that meter.
- Each apartment bills by periods, two months by default, shared by its water and electricity
  meters. A reading at the period's end offers to close it, and the next period opens the day
  after. The owner can also close early, mark the apartment vacant, or set a new start date for
  a new tenant, all in plain words.
- After each reading the reply shows the previous reading, the consumption since then and over
  the period so far, the forecast to the period's end, and the charge when prices are on record.
- Readings that look wrong are questioned: a second reading on the same day, a value lower than
  the last one (misread, or a reset meter), or a late photo near a period's closing reading.
- On the first visit of a day, the chat names every apartment whose period has ended without
  closing readings.
- Sign-in is with a Google account. Each address has an account of its own, and an address the
  owner delegates works in that owner's account.
- What exists today is the backend behind the chat. The browser app, the emailed PDF report, a
  daily reminder, entering prices, questions about the data, corrections, managing delegates
  and a period total across a replaced meter and its successor are planned: see the
  [open items](docs/architecture.md#open-items).

## Tech stack

- **Backend** — Python 3.13 Lambdas on arm64 behind an API Gateway HTTP API, nine DynamoDB
  tables, a private S3 bucket for photos
- **Model** — Claude (`claude-opus-5-5`) by default, or an OpenAI model, chosen by one deploy
  setting; each provider's own SDK, each answer parsed into a typed schema, to read photos and
  classify messages; the API key in SSM Parameter Store
- **Auth** — Cognito with Google as the only sign-in; a pre-sign-up Lambda asks SES to verify
  each new address
- **Infrastructure** — two CloudFormation stacks deployed by shell scripts; data retained across
  stack deletes; S3 and CloudFront hosting provisioned for the browser app
- **Tests** — pytest with moto and a fake model; ruff for lint and format

## Architecture

Nothing runs between requests. Each chat turn wakes one Python function, which runs the turn
through a scripted conversation engine: code decides every step and writes every reply, and the
model is asked only to read a photo or to classify a message. The data lives in DynamoDB tables
and the photos in S3, both kept when a stack is deleted.

```mermaid
graph LR
    C((Chat client)) -->|Google sign-in| COG[Cognito]
    C -->|JWT| API[HTTP API]
    API --> L[api Lambda]
    L --> DB[(DynamoDB)]
    L --> S3[(S3 photos)]
    L --> CL[Claude API]
```

**[Architecture](docs/architecture.md)** continues from here: the full diagram, each route and
table, how the conversation engine queues confirmations, and what is not built yet.

## Getting started

Run the tests with `./scripts/test.sh`; it creates the virtualenv on its first run. To deploy,
run `./scripts/init-env.sh`, fill in the two settings files it creates, then run
`./scripts/deploy.sh` and `./scripts/set-model-key.sh`.
[Development](docs/development.md) has the details.

## Scripts

- `init-env.sh` — creates the gitignored AWS and deploy settings files from their examples
- `deploy.sh` — builds the Lambda package and deploys both stacks
- `delete-stack.sh` — deletes both stacks, keeping the tables and the photos
- `set-model-key.sh` — stores the model provider's API key in SSM
- `aws-helper.sh` — finds and tails the deployed functions' logs
- `test.sh` — lint, format check and the test suite
- `read-meter.sh` — reads one photo through the model and prints the result
- `eval-readings.sh` — scores a model on the photos the deployed model failed on

## Details

- [Architecture](docs/architecture.md) — what each part is for, the storage layout, the
  codebase, and the open items
- [Domain rules](docs/domain-rules.md) — how readings are checked, how periods open and close,
  and how consumption, forecast and charge are computed
- [Development & deployment](docs/development.md) — local setup, tests, the scripts, and the
  environment gotchas
- [Decision records](docs/adr/) — why the app is built this way, one decision per record

## License

Released under the MIT License. See [LICENSE](LICENSE).
