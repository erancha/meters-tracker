/**
 * The day's activity beside the chat, under the day's name: a collapsible section of photos still
 * waiting and one of saved readings, each shown only when it has lines, each line focusing its
 * message in the thread.
 * An open request for tariffs shows above them, and focuses its card when that is in the thread.
 */
import { dayActivity } from "../activity";
import { shownClock, shownDay } from "../messageTime";
import { texts } from "../texts";
import type { Message } from "../types";

interface Line {
  at: string;
  text: string;
}

function Section({ title, lines, onFocus }: {
  title: string;
  lines: Line[];
  onFocus: (at: string) => void;
}) {
  return (
    <details>
      <summary>{title}</summary>
      <ul>
        {lines.map((line) => (
          <li key={line.at}>
            <button type="button" onClick={() => onFocus(line.at)}>{line.text}</button>
          </li>
        ))}
      </ul>
    </details>
  );
}

const savedLine = (m: Message): Line => {
  const { property, apartment, meter_type: type } = m.reading!;
  return {
    at: m.at,
    text: `${shownClock(m.at)} · ${property} / ${apartment} · ${texts.meterTypes[type!]}`,
  };
};

export function Sidebar({ messages, tariffsMissing, onFocus, day }: {
  messages: Message[];
  tariffsMissing: boolean;
  onFocus: (at: string) => void;
  /** The day the chat shows, as a stamp's date part. */
  day: string;
}) {
  const { saved, held } = dayActivity(messages);
  const ask = [...messages].reverse().find((m) => m.card?.kind === "tariffs_ask");
  const heldLines = held.map(({ message, state }) => ({
    at: message.at, text: `${shownClock(message.at)} · ${texts.held[state]}`,
  }));
  return (
    <aside className="sidebar" aria-label={texts.dayTitle}>
      <h2>{texts.dayTitle} · {shownDay(day)}</h2>
      {tariffsMissing && (ask === undefined
        ? <p className="alert-line">{texts.tariffsMissing}</p>
        : <button type="button" className="alert-line" onClick={() => onFocus(ask.at)}>
            {texts.tariffsMissing}
          </button>)}
      {held.length > 0 && (
        <Section title={texts.heldCount(held.length)} lines={heldLines} onFocus={onFocus} />
      )}
      {saved.length > 0 && (
        <Section title={texts.savedCount(saved.length)} lines={saved.map(savedLine)}
                 onFocus={onFocus} />
      )}
      {saved.length === 0 && held.length === 0 && <p>{texts.noReadingsToday}</p>}
    </aside>
  );
}
