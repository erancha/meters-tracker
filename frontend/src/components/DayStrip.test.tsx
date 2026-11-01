import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { card, message } from "../testChat";
import { texts } from "../texts";
import type { Message, Reading } from "../types";
import { DayStrip } from "./DayStrip";

const photo = (at: string, reading: Reading): Message => ({
  ...message("user", ""), text: null, image_key: `k-${at}`, at, reading,
});

const place = { meter_type: "electricity", apartment: "דירה 3", property: "הרצל 5" } as const;
const AT = "2026-11-01T09:47:00.000000_aaaa";
const LATER = "2026-11-01T10:05:00.000000_bbbb";
const saved = photo(AT, { state: "saved", ...place });
const held = photo(LATER, { state: "awaiting", ...place });
const SAVED_LINE = "09:47 · הרצל 5 / דירה 3 · חשמל";
const HELD_LINE = `10:05 · ${texts.held.awaiting}`;

const ask: Message = {
  ...message("assistant", "חסרים תעריפים"), at: AT,
  card: { ...card(), kind: "tariffs_ask", options: [] },
};

function strip(messages: Message[], onFocus: (at: string) => void, tariffsMissing: boolean) {
  return (
    <DayStrip messages={messages} tariffsMissing={tariffsMissing} onFocus={onFocus}
              day="2026-11-01" />
  );
}

function setup(messages: Message[], tariffsMissing = false) {
  const onFocus = vi.fn();
  const view = render(strip(messages, onFocus, tariffsMissing));
  return { onFocus, update: (next: Message[]) => view.rerender(strip(next, onFocus, tariffsMissing)) };
}

const tap = (name: string) => userEvent.click(screen.getByRole("button", { name }));

describe("DayStrip", () => {
  it("names the day and offers a button per list with its count", () => {
    setup([saved, held]);

    expect(screen.getByText(`${texts.dayTitle} 01/11`)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: texts.heldShort(1) })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: texts.savedShort(1) })).toBeInTheDocument();
  });

  it("offers no button for a list with a count of zero", () => {
    setup([saved]);

    expect(screen.queryByRole("button", { name: texts.heldShort(0) })).toBeNull();
  });

  it("opens only the tapped list", async () => {
    setup([saved, held]);

    await tap(texts.heldShort(1));

    expect(screen.getByRole("button", { name: HELD_LINE })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: SAVED_LINE })).toBeNull();
  });

  it("switches to the other list when its button is tapped", async () => {
    setup([saved, held]);

    await tap(texts.heldShort(1));
    await tap(texts.savedShort(1));

    expect(screen.getByRole("button", { name: SAVED_LINE })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: HELD_LINE })).toBeNull();
  });

  it("closes the list when its button is tapped again", async () => {
    setup([saved]);

    await tap(texts.savedShort(1));
    await tap(texts.savedShort(1));

    expect(screen.queryByRole("button", { name: SAVED_LINE })).toBeNull();
  });

  it("closes the list on a tap outside it, focusing nothing", async () => {
    const { onFocus } = setup([saved]);

    await tap(texts.savedShort(1));
    await tap(texts.closeList);

    expect(screen.queryByRole("button", { name: SAVED_LINE })).toBeNull();
    expect(onFocus).not.toHaveBeenCalled();
  });

  it("closes the list and focuses the message of the tapped line", async () => {
    const { onFocus } = setup([saved]);

    await tap(texts.savedShort(1));
    await tap(SAVED_LINE);

    expect(onFocus).toHaveBeenCalledWith(AT);
    expect(screen.queryByRole("button", { name: SAVED_LINE })).toBeNull();
  });

  it("closes the open list when its last line leaves it", async () => {
    const { update } = setup([held]);

    await tap(texts.heldShort(1));
    update([]);

    expect(screen.queryByRole("button", { name: texts.closeList })).toBeNull();
  });

  it("puts missing tariffs in the day's place, focusing the card asking for them", async () => {
    const { onFocus } = setup([ask], true);

    await tap(texts.tariffsMissing);

    expect(onFocus).toHaveBeenCalledWith(AT);
    expect(screen.queryByText(`${texts.dayTitle} 01/11`)).toBeNull();
  });

  it("names missing tariffs as plain text when the card asking for them is on an earlier day", () => {
    setup([], true);

    expect(screen.getByText(texts.tariffsMissing)).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("shows nothing when both lists are empty and no tariffs are missing", () => {
    setup([message("assistant", "שלום")]);

    expect(screen.queryByRole("region")).toBeNull();
  });

  it("appears once the first line of the day arrives", () => {
    const { update } = setup([]);

    update([saved]);

    expect(screen.getByRole("region", { name: texts.dayTitle })).toBeInTheDocument();
  });

  it("names only missing tariffs when both lists are empty", () => {
    setup([], true);

    expect(screen.getByRole("region", { name: texts.dayTitle })).toBeInTheDocument();
    expect(screen.queryByText(texts.noReadingsToday)).toBeNull();
  });
});
