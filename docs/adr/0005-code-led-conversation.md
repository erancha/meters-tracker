# 0005. Code leads the conversation; the model only reads photos and classifies text

- Status: Accepted
- Date: 2026-10-28

## Context

The whole app is a Hebrew chat: the owner sends meter photos and short messages, and the app
saves readings and closes periods. The flows are small and fixed: a photo becomes a confirmed
reading, an unknown meter is linked to an apartment, and a period is closed or changed. An agent
that chose the next step and wrote replies itself would make every turn depend on a model call,
make the data writes hard to test, and put the wording out of the owner's reach, while the owner
corrects terms often.

## Decision

- The chat is a scripted workflow. Each flow is a small state machine in `src/common/flows/`,
  persisted between turns as a pending item, and code decides every next step.
- The model has two jobs, both returning typed results through structured output: read a meter
  photo (type, serial, candidate counter values with their position on the display, unreadable
  digits, a handwritten label), and classify one free-text message into an intent from a closed
  list, with typed arguments.
- Every reply is rendered by code from Hebrew templates kept in one module, `src/common/texts.py`.
  The model never writes text the user sees.
- Every data write waits behind a confirmation card. A typed answer to a card is classified with
  the card as context; text the classifier cannot place gets a reply listing what the chat can
  do.
- The model sits behind an interface; every test runs the engine against a fake model.

## Consequences

- The conversation is predictable and testable without network calls, and a wording change
  touches one file.
- A new kind of request means a new intent in the prompt and the result schema, plus its handler
  and its reply template in code.
- Requests outside the closed intent list are answered with a fixed message, never improvised.
