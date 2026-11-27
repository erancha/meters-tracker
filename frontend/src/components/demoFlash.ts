/**
 * Picks how long the landing page's demo link flashes, from how many times this browser has
 * opened the page: five one-second flashes on the first visit, two on the second, none after.
 * The count lives in localStorage, so each device and browser counts its own visits.
 */

const VISITS_KEY = "landing-visits";
// Key: the visit's number in this browser. Value: the landing.css class that flashes the link.
const FLASH_BY_VISIT: Record<number, string> = { 1: "demo-flash-first", 2: "demo-flash-second" };

/** Counts one more visit in the storage and returns that visit's flash class, if it has one. */
export function countVisit(storage: Storage): string | undefined {
  const visit = Number(storage.getItem(VISITS_KEY)) + 1;
  storage.setItem(VISITS_KEY, String(visit));
  return FLASH_BY_VISIT[visit];
}

// Counted once per page load, so a remount of the landing page (React's development double
// render, a sign-out) is not another visit.
let pageLoad: { flash: string | undefined } | undefined;

/**
 * The demo link's flash class for this page load. A browser that blocks site storage throws a
 * DOMException on access; its visitors see no flash.
 */
export function demoFlashClass(): string | undefined {
  if (pageLoad === undefined) {
    let flash: string | undefined;
    try {
      flash = countVisit(window.localStorage);
    } catch (error) {
      if (!(error instanceof DOMException)) throw error;
    }
    pageLoad = { flash };
  }
  return pageLoad.flash;
}
