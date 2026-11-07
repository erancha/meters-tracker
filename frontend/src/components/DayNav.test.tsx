import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { texts } from "../texts";
import { DayNav } from "./DayNav";

const TODAY = "2026-11-05";

function setup(day: string, newAccount = false) {
  const onDay = vi.fn();
  render(<DayNav day={day} today={TODAY} newAccount={newAccount} label="label" onDay={onDay} />);
  return onDay;
}

describe("DayNav", () => {
  it("steps to the previous day across a month's start", async () => {
    const onDay = setup("2026-11-01");

    await userEvent.click(screen.getByRole("button", { name: texts.previousDay }));

    expect(onDay).toHaveBeenCalledWith("2026-10-31");
  });

  it("offers no next day and no way back to today while today is shown", () => {
    setup(TODAY);

    expect(screen.getByRole("button", { name: texts.nextDay })).toBeDisabled();
    expect(screen.queryByRole("button", { name: texts.backToToday })).toBeNull();
  });

  it("steps to the next day and back to today from an earlier day", async () => {
    const onDay = setup("2026-11-03");

    await userEvent.click(screen.getByRole("button", { name: texts.nextDay }));
    await userEvent.click(screen.getByRole("button", { name: texts.backToToday }));

    expect(onDay.mock.calls).toEqual([["2026-11-04"], [TODAY]]);
  });

  it("shows a picked day, and today for a typed day after it", () => {
    const onDay = setup(TODAY);
    const picker = screen.getByLabelText(texts.pickDay);

    fireEvent.change(picker, { target: { value: "2026-10-12" } });
    fireEvent.change(picker, { target: { value: "2026-12-01" } });

    expect(onDay.mock.calls).toEqual([["2026-10-12"], [TODAY]]);
  });

  it("shows only the label on a new account", () => {
    setup(TODAY, true);

    expect(screen.getByText("label")).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByLabelText(texts.pickDay)).toBeNull();
  });
});
