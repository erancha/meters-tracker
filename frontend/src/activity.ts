/**
 * The day's activity, derived from the messages the chat holds: the photos whose reading was saved,
 * and the photos that still need something. Failed outranks anything the photo read; a stored
 * photo with neither mark nor reading is one the model has not answered yet.
 */
import type { Message } from "./types";

export type HeldState = "failed" | "awaiting" | "reading";

export interface Held {
  message: Message;
  state: HeldState;
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
