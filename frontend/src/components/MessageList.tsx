/**
 * The scrolling thread: history messages in order. A message with a card shows only the card
 * (its text repeats the card); the snapshot's card gets its buttons on the latest history
 * message carrying it, or at the end when none does. Choice messages are folded into their
 * cards. A photo shows as a thumbnail that opens the full-screen lightbox. A user message shows
 * its date and time.
 *
 * The thread scrolls to its end as it grows, except when a new live card is already in the
 * history (an earlier photo's card, after the card above it was answered): that card is
 * scrolled into view instead.
 */
import { useEffect, useRef } from "react";
import type { Api } from "../api";
import { foldChoices } from "../foldChoices";
import { shownTime } from "../messageTime";
import { texts } from "../texts";
import type { Card, CardOption, LocalMessage, Snapshot } from "../types";
import { CardView } from "./CardView";
import { Thumbnail } from "./Thumbnail";

export function MessageList({ api, messages, snapshot, onChoose, choosing, thinking, onOpen }: {
  api: Api;
  messages: LocalMessage[];
  snapshot: Snapshot;
  onChoose: (card: Card, option: CardOption) => void;
  /** A choice turn is in flight, so the live card's buttons are disabled. */
  choosing: boolean;
  /** A turn is in flight. */
  thinking: boolean;
  onOpen: (src: string) => void;
}) {
  const end = useRef<HTMLLIElement>(null);
  const liveEntry = useRef<HTMLLIElement>(null);
  const scrolledTo = useRef<string | null>(null);
  const live = snapshot.card;

  const entries = foldChoices(messages);
  const isLive = (c: Card | null) =>
    live !== null && c !== null && c.pending === live.pending && c.version === live.version;
  const liveIndex = entries.map((e) => isLive(e.message.card)).lastIndexOf(true);
  const liveKey = live === null ? null : `${live.pending}#${live.version}`;
  // With no card shown, every waiting item is a photo the model is still reading.
  const working = thinking || (live === null && snapshot.waiting > 0);

  useEffect(() => {
    const newLiveInHistory = liveKey !== scrolledTo.current && liveEntry.current !== null;
    scrolledTo.current = liveKey;
    if (newLiveInHistory) liveEntry.current!.scrollIntoView({ block: "start" });
    else end.current!.scrollIntoView({ block: "end" });
  }, [liveKey, entries.length, snapshot.waiting, working]);
  const isPhoto = (m: LocalMessage) => m.image_key !== null || m.localUrl !== undefined;
  const bubble = (m: LocalMessage) =>
    `msg ${m.role === "user" ? "user" : "bot"}${isPhoto(m) ? " photo" : ""}`;

  return (
    <ul className="msgs">
      {entries.map(({ message: m, answer }, i) => (
        <li key={i} className={bubble(m)} ref={i === liveIndex ? liveEntry : undefined}>
          {isPhoto(m) && (
            <div className="shots">
              {m.localUrl !== undefined
                ? <button type="button" className="shot" onClick={() => onOpen(m.localUrl!)}>
                    <img src={m.localUrl} alt={texts.photo} />
                  </button>
                : <Thumbnail imageKey={m.image_key!} api={api} onOpen={onOpen} />}
            </div>
          )}
          {m.card !== null
            ? <CardView card={m.card} answer={answer} disabled={choosing}
                        onChoose={i === liveIndex ? (o) => onChoose(m.card!, o) : undefined} />
            : m.text !== null && <p className="text">{m.text}</p>}
          {m.role === "user" && <time className="msg-time ltr">{shownTime(m.at)}</time>}
        </li>
      ))}
      {live !== null && liveIndex === -1 && (
        <li className="msg bot">
          <CardView card={live} onChoose={(o) => onChoose(live, o)} disabled={choosing} />
        </li>
      )}
      {live !== null && snapshot.waiting > 0 && (
        <li className="msg bot"><p className="muted">{texts.waiting(snapshot.waiting)}</p></li>
      )}
      {working && (
        <li className="msg bot thinking" role="status">
          <p className="muted">{texts.thinking}<span className="dots" aria-hidden="true" /></p>
        </li>
      )}
      <li ref={end} aria-hidden="true" />
    </ul>
  );
}
