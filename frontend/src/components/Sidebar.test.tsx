import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { card, message } from "../testChat";
import { texts } from "../texts";
import type { Message, Reading } from "../types";
import { Sidebar } from "./Sidebar";

const photo = (at: string, reading?: Reading, failed?: boolean): Message => ({
  ...message("user", ""), text: null, image_key: `k-${at}`, at,
  ...(reading && { reading }), ...(failed && { failed }),
});

const place = { meter_type: "electricity", apartment: "דירה 3", property: "הרצל 5" } as const;
const saved = (at: string) => photo(at, { state: "saved", ...place });
const AT = "2026-11-01T09:47:00.000000_aaaa";
const LATER = "2026-11-01T10:05:00.000000_bbbb";

const ask: Message = {
  ...message("assistant", "חסרים תעריפים"), at: AT,
  card: { ...card(), kind: "tariffs_ask", options: [] },
};

function setup(
  messages: Message[], tariffsMissing = false, earlierFailed: Message[] = [], day = "2026-11-01",
) {
  const onFocus = vi.fn();
  render(<Sidebar messages={messages} tariffsMissing={tariffsMissing} onFocus={onFocus}
                  day={day} today="2026-11-01" earlierFailed={earlierFailed} onDay={vi.fn()} />);
  return onFocus;
}

// The line buttons alone, without the day controls in the heading.
const lineTexts = () =>
  [...document.querySelectorAll(".day-lines button")].map((b) => b.textContent);

describe("Sidebar", () => {
  it("titles the panel with today's weekday and date", () => {
    setup([]);

    expect(screen.getByRole("heading")).toHaveTextContent("היום · יום ראשון 01/11/2026");
  });

  it("lists the waiting section before the saved one", () => {
    setup([saved(AT), photo(LATER, { state: "awaiting", ...place })]);

    const summaries = screen.getAllByText(/קריאות/).map((e) => e.textContent);
    expect(summaries).toEqual([texts.heldCount(1), texts.savedCount(1)]);
  });

  it("shows a section only for a count above zero", () => {
    setup([saved(AT)]);

    expect(screen.getByText(texts.savedCount(1))).toBeInTheDocument();
    expect(screen.queryByText(texts.heldCount(0))).toBeNull();
  });

  it("keeps a section collapsed until its header is tapped", async () => {
    setup([saved(AT)]);
    const summary = screen.getByText(texts.savedCount(1));

    expect(summary.closest("details")).not.toHaveAttribute("open");
    await userEvent.click(summary);
    expect(summary.closest("details")).toHaveAttribute("open");
  });

  it("opens the waiting section from the start", () => {
    setup([photo(AT, { state: "awaiting", ...place })]);

    expect(screen.getByText(texts.heldCount(1)).closest("details")).toHaveAttribute("open");
  });

  it("marks the waiting and saved sections for their colors", () => {
    setup([saved(AT), photo(LATER, { state: "awaiting", ...place })]);

    expect(screen.getByText(texts.heldCount(1)).closest("details")).toHaveClass("held");
    expect(screen.getByText(texts.savedCount(1)).closest("details")).toHaveClass("saved");
  });

  it("writes a saved line as time, property / apartment and meter type, newest first", () => {
    setup([saved(AT), saved(LATER)]);

    expect(lineTexts()).toEqual([
      "10:05 · הרצל 5 / דירה 3 · חשמל",
      "09:47 · הרצל 5 / דירה 3 · חשמל",
    ]);
  });

  it("writes a held line as time and state only", () => {
    setup([
      photo(AT, { state: "awaiting", ...place }),
      photo(LATER, undefined, true),
    ]);

    expect(lineTexts()).toEqual([
      `10:05 · ${texts.held.failed}`,
      `09:47 · ${texts.held.awaiting}`,
    ]);
  });

  it("calls onFocus with the stamp of the tapped line's message", async () => {
    const onFocus = setup([saved(AT)]);

    await userEvent.click(screen.getByText(texts.savedCount(1)));
    await userEvent.click(screen.getByRole("button", { name: "09:47 · הרצל 5 / דירה 3 · חשמל" }));

    expect(onFocus).toHaveBeenCalledWith(AT);
  });

  it("shows missing tariffs as a line that focuses the card asking for them", async () => {
    const onFocus = setup([ask], true);

    await userEvent.click(screen.getByRole("button", { name: texts.tariffsMissing }));

    expect(onFocus).toHaveBeenCalledWith(AT);
  });

  it("shows no tariffs line once none are missing", () => {
    setup([ask]);

    expect(screen.queryByText(texts.tariffsMissing)).toBeNull();
  });

  it("still names missing tariffs when the card asking for them was on an earlier day", () => {
    setup([], true);

    expect(screen.getByText(texts.tariffsMissing)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: texts.tariffsMissing })).toBeNull();
  });

  it("says no meters were read when both lists are empty", () => {
    setup([message("assistant", "שלום")]);

    expect(screen.getByText(texts.noReadingsToday)).toBeInTheDocument();
  });

  it("lists photos still failed from earlier days after today's, each with its date", () => {
    const earlier = [photo("2026-10-28T08:15:00.000000_cccc", undefined, true),
                     photo("2026-10-30T19:02:00.000000_dddd", undefined, true)];
    setup([photo(AT, { state: "awaiting", ...place })], false, earlier);

    expect(screen.getByText(texts.heldCount(3))).toBeInTheDocument();
    expect(lineTexts()).toEqual([
      `09:47 · ${texts.held.awaiting}`,
      `30/10 19:02 · ${texts.held.failed}`,
      `28/10 08:15 · ${texts.held.failed}`,
    ]);
  });

  it("titles an earlier day by its weekday and date alone", () => {
    setup([], false, [], "2026-10-30");

    expect(screen.getByRole("heading")).toHaveTextContent("יום שישי 30/10/2026");
    expect(screen.getByRole("heading")).not.toHaveTextContent(`${texts.dayTitle} ·`);
    expect(screen.getByText(texts.noReadingsThatDay)).toBeInTheDocument();
  });

  // The day's title reads as the panel's header in the band the narrow day strip shows it in, so
  // it stands apart from the collapsible sections under it.
  it("shows the day's title in the day strip's band", () => {
    const rule = readFileSync("src/style.css", "utf8")
      .match(/#root \.day-strip-row, #root \.sidebar h2 \{([^}]*)\}/)?.[1];

    expect(rule).toMatch(/background: var\(--accent-soft\)/);
    expect(rule).toMatch(/border-bottom: 1px solid var\(--accent-border\)/);
  });
});
