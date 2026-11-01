/**
 * Folds the user messages that record a card choice into the card they answered.
 *
 * The backend stores a choice as a user message whose text is the chosen option's label and
 * whose choice names the answered card by pending item and version. The chat shows the choice in
 * place on that card, so such a message gets no bubble of its own. Typed text never folds, even
 * when it equals a label.
 */
import type { Message } from "./types";

export interface Entry<M extends Message = Message> {
  message: M;
  /** The label of the option chosen on this card, or null while unanswered or for no card. */
  answer: string | null;
}

export function foldChoices<M extends Message>(messages: M[]): Entry<M>[] {
  const entries: Entry<M>[] = [];
  for (const message of messages) {
    const answered = message.choice;
    if (answered !== null) {
      // A card re-sent at the same version (a reply the backend did not understand) repeats in
      // the history; the choice answers its latest copy.
      const target = [...entries].reverse().find((e) =>
        e.answer === null && e.message.card?.pending === answered.pending &&
          e.message.card.version === answered.version);
      if (target !== undefined) {
        target.answer = message.text;
        continue;
      }
    }
    entries.push({ message, answer: null });
  }
  return entries;
}
