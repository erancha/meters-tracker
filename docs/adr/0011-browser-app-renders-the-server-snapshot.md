# 0011. The browser app is a React single-page app that renders the server's snapshot

- Status: Accepted
- Date: 2026-11-01

## Context

The chat backend exists and answers each action with its replies and a snapshot: the card the
next message answers and the cards queued behind it ([ADR 0005](0005-code-led-conversation.md)).
Owners have no way to reach it yet. They work a billing round on a phone beside the meters, or
at a desktop window, and the demo page `docs/demo/demo.html` already shows the intended chat on a
phone screen. Sign-in is Cognito's hosted page with Google
([ADR 0002](0002-google-sign-in-and-delegation.md)), and the app stack already provisions an S3
bucket and a CloudFront distribution for a browser app.

## Decision

- `frontend/` is a React and TypeScript single-page app built with Vite, in Hebrew, right to
  left, with its look taken from the demo page and every visible string in `frontend/src/texts.ts`.
- It decides nothing about the conversation: it shows the server's replies, renders the
  snapshot's card with the options the server lists, and posts a choice with the card's version.
- Sign-in uses the hosted page with the authorization-code flow and PKCE, so no secret lives in
  the browser; the ID token authorizes each API call.
- `scripts/deploy.sh --frontend` builds it and publishes it to the frontend bucket behind
  CloudFront, with a `config.js` generated from the stack's outputs and the demo page beside it.

## Consequences

- A change to a flow, a card or a reply ships with the backend alone; the browser app changes
  only for a new kind of surface.
- The frontend has its own suite (Vitest with Testing Library), and CI runs it beside the
  backend gate.
- Local frontend work needs a deployed stack, since the dev server talks to its API and
  Cognito only accepts the local callback on port 8000.
