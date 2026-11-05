/**
 * The shown day's activity on a narrow window: a strip under the header that stays while the chat
 * scrolls, holding the controls that move to another day and a button per list of the day panel.
 * A tap drops that one list over the chat; a tap outside it closes it. Missing tariffs show beside
 * the list buttons. On the narrowest phones the list buttons show only their counts.
 */
import { useState } from "react";
import { dayLines, tariffsAsk } from "../activity";
import { shownDayMonth } from "../messageTime";
import { texts } from "../texts";
import { DayNav } from "./DayNav";
import { LineList, TariffsAlert, type DayView } from "./Sidebar";

type List = "held" | "saved";

export function DayStrip(
  { messages, earlierFailed, tariffsMissing, onFocus, day, today, onDay }: DayView,
) {
  const [open, setOpen] = useState<List | null>(null);
  const lines = dayLines(messages, earlierFailed);
  // A list left with no lines shows as closed: its button is gone, so nothing else could close it.
  const shown = open === null ? [] : lines[open];
  const listButton = (list: List, word: string, label: string) => (
    <button type="button" className={`chip ${list}`} aria-label={label}
            aria-expanded={shown.length > 0 && open === list}
            onClick={() => setOpen((o) => (o === list ? null : list))}>
      <span className="chip-word">{word}</span> {lines[list].length}
    </button>
  );
  return (
    <section className={shown.length > 0 ? "day-strip open" : "day-strip"}
             aria-label={texts.dayTitle}>
      <div className="day-strip-row">
        <DayNav day={day} today={today} onDay={onDay}
                label={day === today
                  ? `${texts.dayTitle} ${shownDayMonth(day)}` : shownDayMonth(day)} />
        <span className="chips">
          {tariffsMissing && <TariffsAlert ask={tariffsAsk(messages)} onFocus={onFocus} />}
          {lines.held.length > 0
            && listButton("held", texts.heldWord, texts.heldShort(lines.held.length))}
          {lines.saved.length > 0
            && listButton("saved", texts.savedWord, texts.savedShort(lines.saved.length))}
        </span>
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
