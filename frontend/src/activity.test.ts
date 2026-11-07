import { describe, expect, it } from "vitest";
import { dayActivity, opensNewAccount } from "./activity";
import { texts } from "./texts";
import { message } from "./testChat";
import type { Message, Reading } from "./types";

const reading = (state: Reading["state"]): Reading => ({
  state, meter_type: "water", apartment: "דירה 3", property: "הרצל 5",
});

const photo = (at: string, over: Partial<Message> = {}): Message => ({
  ...message("user", ""), text: null, image_key: `k-${at}`, at, ...over,
});

describe("dayActivity", () => {
  it("sorts saved and held newest first, skipping dropped photos and other messages", () => {
    const { saved, held } = dayActivity([
      photo("1", { reading: reading("saved") }),
      photo("2", { reading: reading("awaiting") }),
      photo("3", { reading: reading("saved") }),
      photo("4", { reading: reading("dropped") }),
      photo("5", { reading: reading("awaiting") }),
      message("user", "שלום"),
    ]);

    expect(saved.map((m) => m.at)).toEqual(["3", "1"]);
    expect(held.map((h) => [h.message.at, h.state]))
      .toEqual([["5", "awaiting"], ["2", "awaiting"]]);
  });

  it("ranks a failed mark above a reading the photo holds", () => {
    const { saved, held } = dayActivity([
      photo("1", { failed: true, reading: reading("saved") }),
    ]);

    expect(saved).toEqual([]);
    expect(held.map((h) => h.state)).toEqual(["failed"]);
  });

  it("treats a stored photo with no reading as still being read", () => {
    expect(dayActivity([photo("1")]).held.map((h) => h.state)).toEqual(["reading"]);
  });

  it("treats a retried photo, its failed mark cleared, as still being read", () => {
    expect(dayActivity([photo("1", { failed: false })]).held.map((h) => h.state))
      .toEqual(["reading"]);
  });

  it("leaves out a photo on its way, which has no stored key yet", () => {
    expect(dayActivity([photo("1", { image_key: null })]).held).toEqual([]);
  });
});

describe("opensNewAccount", () => {
  it("spots the welcome of a new account and nothing else", () => {
    const welcome = message("assistant", `${texts.newAccountOpener}\nאפשר להתחיל`);

    expect(opensNewAccount([welcome, photo("2026-11-07T09:00:00")])).toBe(true);
    expect(opensNewAccount([message("assistant", "שלום"), message("user", texts.newAccountOpener)]))
      .toBe(false);
  });
});
