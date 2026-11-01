import { describe, expect, it } from "vitest";
import { answer, card, message } from "./testChat";
import { foldChoices } from "./foldChoices";
import type { Message } from "./types";

const cardMsg = (c = card()): Message => ({
  ...message("assistant", [c.title, ...c.lines].join("\n")), card: c,
});

describe("foldChoices", () => {
  it("keeps plain messages unanswered", () => {
    const msgs = [message("user", "שלום"), message("assistant", "היי")];

    expect(foldChoices(msgs)).toEqual(msgs.map((m) => ({ message: m, answer: null })));
  });

  it("folds a choice into the card it answered", () => {
    const c = cardMsg();
    const entries = foldChoices([c, answer(card(), "אישור"), message("assistant", "נשמר")]);

    expect(entries.map((e) => [e.message.text, e.answer])).toEqual([
      [c.text, "אישור"],
      ["נשמר", null],
    ]);
  });

  it("never folds typed text, even when it matches an offered label", () => {
    const entries = foldChoices([cardMsg(), message("user", "אישור")]);

    expect(entries).toHaveLength(2);
    expect(entries[0].answer).toBeNull();
  });

  it("folds each of several same-label choices into its own card, oldest answered first", () => {
    const cards = ["a", "b", "c"].map((p) => card({ pending: p }));
    const answered = (n: number) => foldChoices([
      ...cards.map((c) => cardMsg(c)),
      ...cards.slice(0, n).map((c) => answer(c, "אישור")),
    ]).map((e) => [e.message.card!.pending, e.answer]);

    expect(answered(1)).toEqual([["a", "אישור"], ["b", null], ["c", null]]);
    expect(answered(2)).toEqual([["a", "אישור"], ["b", "אישור"], ["c", null]]);
    expect(answered(3)).toEqual([["a", "אישור"], ["b", "אישור"], ["c", "אישור"]]);
  });

  it("matches the card's version, not only its pending item", () => {
    const first = card({ version: 1 });
    const second = card({ version: 2 });

    const entries = foldChoices([cardMsg(first), cardMsg(second), answer(first, "אישור")]);

    expect(entries.map((e) => [e.message.card!.version, e.answer]))
      .toEqual([[1, "אישור"], [2, null]]);
  });

  it("folds the answer of an older card arriving after a newer card", () => {
    const older = card({ pending: "a", title: "ישן", options: [{ id: "x", label: "א" }] });
    const newer = card({ pending: "b", title: "חדש", options: [{ id: "y", label: "ב" }] });

    const entries = foldChoices([cardMsg(older), cardMsg(newer), answer(older, "א")]);

    expect(entries.map((e) => [e.message.card!.pending, e.answer]))
      .toEqual([["a", "א"], ["b", null]]);
  });
});
