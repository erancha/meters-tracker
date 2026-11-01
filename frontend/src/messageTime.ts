/**
 * Message timestamps: the wall clock in Asia/Jerusalem, the app's one zone, as the backend
 * stamps every stored message. Stamps carry no offset, so they are read as text, never through
 * Date, which would shift them to the viewer's own zone.
 */
import { texts } from "./texts";

const JERUSALEM_CLOCK = new Intl.DateTimeFormat("sv-SE", {
  timeZone: "Asia/Jerusalem", dateStyle: "short", timeStyle: "medium",
});

/** A local message's stamp, in the stored format, shown until the server's history replaces it. */
export const jerusalemStamp = (moment = new Date()) =>
  JERUSALEM_CLOCK.format(moment).replace(" ", "T");

export function shownTime(stamp: string) {
  const parts = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}:\d{2})/.exec(stamp);
  if (parts === null) throw new Error(`not a message stamp: ${stamp}`);
  const [, year, month, day, time] = parts;
  return `${day}/${month}/${year} ${time}`;
}

export const shownClock = (stamp: string) => shownTime(stamp).split(" ")[1];

/** The day's name and date, from a stamp's date part (YYYY-MM-DD). */
export function shownDay(day: string) {
  const [year, month, date] = day.split("-").map(Number);
  const weekday = new Date(Date.UTC(year, month - 1, date)).getUTCDay();
  return `${texts.weekdays[weekday]} ${shownTime(`${day}T00:00`).split(" ")[0]}`;
}

/** The day and month, from a stamp's date part (YYYY-MM-DD). */
export const shownDayMonth = (day: string) => shownTime(`${day}T00:00`).slice(0, 5);

/** Today's date in Jerusalem, as a stamp's date part. */
export const jerusalemDay = () => jerusalemStamp().slice(0, 10);
