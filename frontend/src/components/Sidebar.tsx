/**
 * The day's activity beside the chat, under the day's name: a collapsible section of photos still
 * waiting, open from the start, and one of saved readings, each shown only when it has lines, each
 * line focusing its message in the thread.
 * An open request for tariffs shows above them, and focuses its card when that is in the thread.
 * LineList and TariffsAlert render the lines and the alert wherever the day's activity is shown.
 */
import { dayLines, tariffsAsk, type Line } from "../activity";
import { shownDay } from "../messageTime";
import { texts } from "../texts";
import type { Message } from "../types";

export function LineList({ lines, onFocus }: { lines: Line[]; onFocus: (at: string) => void }) {
  return (
    <ul className="day-lines">
      {lines.map((line) => (
        <li key={line.at}>
          <button type="button" onClick={() => onFocus(line.at)}>{line.text}</button>
        </li>
      ))}
    </ul>
  );
}

export function TariffsAlert({ ask, onFocus }: {
  /** The card asking for the tariffs, when the chat holds it. */
  ask: Message | undefined;
  onFocus: (at: string) => void;
}) {
  return ask === undefined
    ? <p className="alert-line">{texts.tariffsMissing}</p>
    : <button type="button" className="alert-line" onClick={() => onFocus(ask.at)}>
        {texts.tariffsMissing}
      </button>;
}

function Section({ kind, title, lines, onFocus }: {
  kind: "held" | "saved";
  title: string;
  lines: Line[];
  onFocus: (at: string) => void;
}) {
  return (
    <details className={kind} open={kind === "held"}>
      <summary>{title}</summary>
      <LineList lines={lines} onFocus={onFocus} />
    </details>
  );
}

export function Sidebar({ messages, tariffsMissing, onFocus, day }: {
  messages: Message[];
  tariffsMissing: boolean;
  onFocus: (at: string) => void;
  /** The day the chat shows, as a stamp's date part. */
  day: string;
}) {
  const { saved, held } = dayLines(messages);
  return (
    <aside className="sidebar" aria-label={texts.dayTitle}>
      <h2>{texts.dayTitle} · {shownDay(day)}</h2>
      {tariffsMissing && <TariffsAlert ask={tariffsAsk(messages)} onFocus={onFocus} />}
      {held.length > 0 && (
        <Section kind="held" title={texts.heldCount(held.length)} lines={held} onFocus={onFocus} />
      )}
      {saved.length > 0 && (
        <Section kind="saved" title={texts.savedCount(saved.length)} lines={saved} onFocus={onFocus} />
      )}
      {saved.length === 0 && held.length === 0 && <p>{texts.noReadingsToday}</p>}
    </aside>
  );
}
