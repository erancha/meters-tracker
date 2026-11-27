import { beforeEach, describe, expect, it } from "vitest";
import { countVisit, demoFlashClass } from "./demoFlash";

describe("countVisit", () => {
  beforeEach(() => localStorage.clear());

  it("flashes long on the first visit, short on the second, then not at all", () => {
    const flashes = [1, 2, 3].map(() => countVisit(localStorage));

    expect(flashes).toEqual(["demo-flash-first", "demo-flash-second", undefined]);
  });
});

describe("demoFlashClass", () => {
  it("counts one visit per page load, however often the landing page renders", () => {
    localStorage.clear();

    expect([demoFlashClass(), demoFlashClass()]).toEqual(["demo-flash-first", "demo-flash-first"]);
    expect(localStorage.getItem("landing-visits")).toBe("1");
  });
});
