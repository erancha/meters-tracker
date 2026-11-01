/**
 * The day's activity on a narrow window: a strip under the header that stays while the chat
 * scrolls, naming the day and offering a button per list of the day panel. A tap drops that one
 * list over the chat; a tap outside it closes it. Missing tariffs take the day's place.
 */
import { useState } from "react";
import { dayLines, tariffsAsk } from "../activity";
import { shownDayMonth } from "../messageTime";
import { texts } from "../texts";
import type { Message } from "../types";
import { LineList, TariffsAlert } from "./Sidebar";

type List = "held" | "saved";

export function DayStrip({ messages, tariffsMissing, onFocus, day }: {
  messages: Message[];
  tariffsMissing: boolean;
  onFocus: (at: string) => void;
  /** The day the chat shows, as a stamp's date part. */
  day: string;
}) {
  const [open, setOpen] = useState<List | null>(null);
  const lines = dayLines(messages);
  // A list left with no lines shows as closed: its button is gone, so nothing else could close it.
  const shown = open === null ? [] : lines[open];
  const listButton = (list: List, label: string) => (
    <button type="button" className={`chip ${list}`}
            aria-expanded={shown.length > 0 && open === list}
            onClick={() => setOpen((o) => (o === list ? null : list))}>
      {label}
    </button>
  );
  return (
    <section className={shown.length > 0 ? "day-strip open" : "day-strip"}
             aria-label={texts.dayTitle}>
      <div className="day-strip-row">
        {tariffsMissing
          ? <TariffsAlert ask={tariffsAsk(messages)} onFocus={onFocus} />
          : <span className="muted">{texts.dayTitle} {shownDayMonth(day)}</span>}
        {lines.held.length === 0 && lines.saved.length === 0
          ? <span className="muted">{texts.noReadingsToday}</span>
          : (
            <span className="chips">
              {lines.held.length > 0 && listButton("held", texts.heldShort(lines.held.length))}
              {lines.saved.length > 0 && listButton("saved", texts.savedShort(lines.saved.length))}
            </span>
          )}
      </div>
      {shown.length > 0 && (
        <>
          <button type="button" className="day-strip-shade" aria-label={texts.closeList}
                  onClick={() => setOpen(null)} />
          <div className="day-strip-list">
            <LineList lines={shown} onFocus={(at) => { setOpen(null); onFocus(at); }} />
          </div>
        </>
      )}
    </section>
  );
}
