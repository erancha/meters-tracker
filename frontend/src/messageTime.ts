/**
 * Message timestamps: the wall clock in Asia/Jerusalem, the app's one zone, as the backend
 * stamps every stored message. Stamps carry no offset, so they are read as text, never through
 * Date, which would shift them to the viewer's own zone.
 */
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
