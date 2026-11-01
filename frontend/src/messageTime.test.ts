import { describe, expect, it } from "vitest";
import { jerusalemStamp, shownDay, shownTime } from "./messageTime";

describe("shownDay", () => {
  it("names the day of the week in Hebrew before the date", () => {
    expect(shownDay("2026-11-01")).toBe("יום ראשון 01/11/2026");
    expect(shownDay("2026-11-07")).toBe("שבת 07/11/2026");
  });
});

describe("shownTime", () => {
  it("shows a stored stamp's wall clock to the minute", () => {
    expect(shownTime("2026-11-01T03:00:17.123456_ab12")).toBe("01/11/2026 03:00");
  });

  it("refuses a stamp that is not a date and time", () => {
    expect(() => shownTime("yesterday")).toThrow("yesterday");
  });
});

describe("jerusalemStamp", () => {
  it("takes the Jerusalem wall clock in winter and in summer", () => {
    expect(jerusalemStamp(new Date("2026-01-15T22:30:05Z"))).toBe("2026-01-16T00:30:05");
    expect(jerusalemStamp(new Date("2026-07-15T21:30:05Z"))).toBe("2026-07-16T00:30:05");
  });
});
