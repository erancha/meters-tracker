/**
 * Moves the chat between days: a button for the previous and the next day around the shown day's
 * label, the browser's date picker on a tap on the label, and a chip back to today while an
 * earlier day is shown. No day after today can be shown.
 */
import { shiftDay } from "../messageTime";
import { texts } from "../texts";
import { Icon } from "./Icon";

export function DayNav({ day, today, label, onDay }: {
  /** The day shown, as a stamp's date part. */
  day: string;
  today: string;
  label: string;
  onDay: (day: string) => void;
}) {
  // A typed date may pass the picker's max; the empty value is a date field the owner cleared.
  const pick = (value: string) => {
    if (value !== "") onDay(value > today ? today : value);
  };
  return (
    <span className="day-nav">
      <button type="button" className="day-step" aria-label={texts.previousDay}
              onClick={() => onDay(shiftDay(day, -1))}>
        <Icon name="back" />
      </button>
      <label className="day-pick">
        <span>{label}</span>
        <input type="date" aria-label={texts.pickDay} value={day} max={today}
               onClick={(e) => e.currentTarget.showPicker()}
               onChange={(e) => pick(e.target.value)} />
      </label>
      <button type="button" className="day-step" aria-label={texts.nextDay}
              disabled={day === today} onClick={() => onDay(shiftDay(day, 1))}>
        <Icon name="forward" />
      </button>
      {day !== today && (
        <button type="button" className="chip today" onClick={() => onDay(today)}>
          {texts.backToToday}
        </button>
      )}
    </span>
  );
}
