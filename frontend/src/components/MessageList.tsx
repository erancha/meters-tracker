/**
 * The scrolling thread: history messages in order. A message with a card shows only the card
 * (its text repeats the card); the snapshot's card gets its buttons on the latest history
 * message carrying it, or at the end when none does. Choice messages are folded into their
 * cards. A photo shows as a thumbnail that opens the full-screen lightbox. A user message shows
 * its date and time, its latest retry's time, and a retry button while it has failed.
 *
 * The thread keeps its end in view: whenever the list changes size (a bubble or the thinking
 * line appears, a photo decodes, the window reflows) the page scrolls to its bottom. The
 * composer sticks to the viewport's bottom, so the end is the page's bottom, not the last
 * bubble's edge. The thread places the page itself, so a reload does not restore the browser's
 * remembered position over it. A focus scrolls the thread to the message it names and highlights
 * the message for a moment, and the end is not followed during that moment.
 */
import { useEffect, useRef, useState } from "react";
import type { Api } from "../api";
import { foldChoices } from "../foldChoices";
import { shownClock, shownTime } from "../messageTime";
import { texts } from "../texts";
import type { Card, CardOption, Focus, LocalMessage, Snapshot } from "../types";
import { CardView } from "./CardView";
import { Thumbnail } from "./Thumbnail";

const FOCUS_MS = 1500;

export function MessageList({
  api, messages, snapshot, onChoose, choosing, thinking, onOpen, onRetry, focus,
}: {
  api: Api;
  messages: LocalMessage[];
  snapshot: Snapshot;
  onChoose: (card: Card, option: CardOption) => void;
  /** A choice turn is in flight, so the live card's buttons are disabled. */
  choosing: boolean;
  /** A turn is in flight. */
  thinking: boolean;
  onOpen: (src: string) => void;
  onRetry: (message: LocalMessage) => void;
  focus: Focus | null;
}) {
  const list = useRef<HTMLUListElement>(null);
  const focusing = useRef(false);
  const [focused, setFocused] = useState<string | null>(null);
  const live = snapshot.card;

  const entries = foldChoices(messages);
  const isLive = (c: Card | null) =>
    live !== null && c !== null && c.pending === live.pending && c.version === live.version;
  const liveIndex = entries.map((e) => isLive(e.message.card)).lastIndexOf(true);
  // With no card shown, every waiting item is a photo the model is still reading.
  const working = thinking || (live === null && snapshot.waiting > 0);
  useEffect(() => {
    history.scrollRestoration = "manual";
    const follow = new ResizeObserver(() => {
      if (!focusing.current) window.scrollTo({ top: document.documentElement.scrollHeight });
    });
    follow.observe(list.current!);
    return () => follow.disconnect();
  }, []);
  useEffect(() => {
    if (focus === null) return;
    const target = list.current!.querySelector(`[data-at="${focus.at}"]`);
    if (target === null) return;
    focusing.current = true;
    target.scrollIntoView({ block: "center" });
    setFocused(focus.at);
    const clear = setTimeout(() => {
      focusing.current = false;
      setFocused(null);
    }, FOCUS_MS);
    return () => clearTimeout(clear);
  }, [focus]);
  const isPhoto = (m: LocalMessage) => m.image_key !== null || m.localUrl !== undefined;
  const bubble = (m: LocalMessage) =>
    `msg ${m.role === "user" ? "user" : "bot"}${isPhoto(m) ? " photo" : ""}`;

  return (
    <ul className="msgs" ref={list}>
      {entries.map(({ message: m, answer }, i) => (
        <li key={i} className={`${bubble(m)}${m.at === focused ? " focused" : ""}`} data-at={m.at}>
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
          {m.role === "user" && (
            <p className="msg-time">
              <time className="ltr">{shownTime(m.at)}</time>
              {m.retried_at !== undefined && (
                <> · <span>{texts.retried(shownClock(m.retried_at))}</span></>
              )}
            </p>
          )}
          {(m.failed === true || m.resend !== undefined) && (
            <div className="msg-failed">
              <span>{texts.notHandled}</span>
              <button type="button" onClick={() => onRetry(m)}>{texts.retry}</button>
            </div>
          )}
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
    </ul>
  );
}
