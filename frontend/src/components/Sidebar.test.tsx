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

function setup(messages: Message[], tariffsMissing = false) {
  const onFocus = vi.fn();
  render(<Sidebar messages={messages} tariffsMissing={tariffsMissing} onFocus={onFocus}
                  day="2026-11-01" />);
  return onFocus;
}

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

  it("writes a saved line as time, property / apartment and meter type, newest first", () => {
    setup([saved(AT), saved(LATER)]);

    expect(screen.getAllByRole("button", { hidden: true }).map((b) => b.textContent)).toEqual([
      "10:05 · הרצל 5 / דירה 3 · חשמל",
      "09:47 · הרצל 5 / דירה 3 · חשמל",
    ]);
  });

  it("writes a held line as time and state only", () => {
    setup([
      photo(AT, { state: "awaiting", ...place }),
      photo(LATER, undefined, true),
    ]);

    expect(screen.getAllByRole("button", { hidden: true }).map((b) => b.textContent)).toEqual([
      `10:05 · ${texts.held.failed}`,
      `09:47 · ${texts.held.awaiting}`,
    ]);
  });

  it("calls onFocus with the stamp of the tapped line's message", async () => {
    const onFocus = setup([saved(AT)]);

    await userEvent.click(screen.getByText(texts.savedCount(1)));
    await userEvent.click(screen.getByRole("button"));

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
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("says no meters were read when both lists are empty", () => {
    setup([message("assistant", "שלום")]);

    expect(screen.getByText(texts.noReadingsToday)).toBeInTheDocument();
  });
});
