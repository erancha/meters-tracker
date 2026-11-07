/**
 * The day's activity, derived from the messages the chat holds: the photos whose reading was saved,
 * and the photos that still need something. Failed outranks anything the photo read; a stored
 * photo with neither mark nor reading is one the model has not answered yet. dayLines turns each
 * list into text lines, the held list followed by the photos still failed from earlier days, each
 * naming its date, and hasLines tells whether either list has one; opensNewAccount spots the
 * welcome of a new account; tariffsAsk finds the card asking for missing tariffs.
 */
import { shownClock, shownDayMonth } from "./messageTime";
import { texts } from "./texts";
import type { Message } from "./types";

export type HeldState = "failed" | "awaiting" | "reading";

export interface Held {
  message: Message;
  state: HeldState;
}

export interface Line {
  at: string;
  text: string;
}

const heldState = (m: Message): HeldState | null => {
  if (m.failed === true) return "failed";
  if (m.reading === undefined) return "reading";
  return m.reading.state === "awaiting" ? "awaiting" : null;
};

export function dayActivity(messages: Message[]): { saved: Message[]; held: Held[] } {
  const photos = messages
    .filter((m) => m.role === "user" && m.image_key !== null)
    .sort((a, b) => b.at.localeCompare(a.at));
  const held = photos.flatMap((message) => {
    const state = heldState(message);
    return state === null ? [] : [{ message, state }];
  });
  return {
    saved: photos.filter((m) => m.failed !== true && m.reading?.state === "saved"),
    held,
  };
}

const savedLine = (m: Message): Line => {
  const { property, apartment, meter_type: type } = m.reading!;
  return {
    at: m.at,
    text: `${shownClock(m.at)} · ${property} / ${apartment} · ${texts.meterTypes[type!]}`,
  };
};

const earlierLine = (m: Message): Line => ({
  at: m.at,
  text: `${shownDayMonth(m.at.slice(0, 10))} ${shownClock(m.at)} · ${texts.held.failed}`,
});

/** The day's lines; earlierFailed, oldest first, is empty on any day but today. */
export function dayLines(
  messages: Message[], earlierFailed: Message[],
): { saved: Line[]; held: Line[] } {
  const { saved, held } = dayActivity(messages);
  return {
    saved: saved.map(savedLine),
    held: [
      ...held.map(({ message, state }) => ({
        at: message.at, text: `${shownClock(message.at)} · ${texts.held[state]}`,
      })),
      ...[...earlierFailed].reverse().map(earlierLine),
    ],
  };
}

export function hasLines(messages: Message[], earlierFailed: Message[]): boolean {
  const { saved, held } = dayLines(messages, earlierFailed);
  return saved.length + held.length > 0;
}

/** The messages hold the welcome of a new owner's account, which has no other day yet. */
export const opensNewAccount = (messages: Message[]) =>
  messages.some((m) => m.role === "assistant" && m.text?.startsWith(texts.newAccountOpener));

export const tariffsAsk = (messages: Message[]) =>
  [...messages].reverse().find((m) => m.card?.kind === "tariffs_ask");
