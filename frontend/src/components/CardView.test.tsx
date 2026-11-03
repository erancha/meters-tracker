import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { card } from "../testChat";
import { CardView } from "./CardView";

describe("CardView", () => {
  it("shows the title, the lines and one button per option", () => {
    render(<CardView card={card()} onChoose={() => {}} />);

    expect(screen.getByText("קריאה חדשה")).toBeInTheDocument();
    expect(screen.getByText("חשמל 123.4")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "אישור" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "דירה אחרת" })).toBeInTheDocument();
  });

  it("reports the chosen option", async () => {
    const onChoose = vi.fn();
    render(<CardView card={card()} onChoose={onChoose} />);

    await userEvent.click(screen.getByRole("button", { name: "דירה אחרת" }));

    expect(onChoose).toHaveBeenCalledWith({ id: "other", label: "דירה אחרת" });
  });

  it("shows no buttons without a choose handler", () => {
    render(<CardView card={card()} />);

    expect(screen.queryByRole("button")).toBeNull();
  });

  it("shows an answered confirm as a done mark without buttons", () => {
    render(<CardView card={card()} onChoose={() => {}} answer="אישור" />);

    expect(screen.getByText("✓ אישור")).toHaveClass("done");
    expect(screen.queryByRole("button", { name: "אישור" })).toBeNull();
  });

  it("marks the chosen chip and makes the chips inert", () => {
    render(<CardView card={card()} onChoose={() => {}} answer="דירה אחרת" />);

    const chip = screen.getByRole("button", { name: "דירה אחרת" });
    expect(chip).toHaveClass("on");
    expect(chip).toBeDisabled();
  });

  it("renders the default option as the primary button and the rest as chips", () => {
    const twoNumbers = card({
      options: [{ id: "top", label: "המספר העליון" }, { id: "bottom", label: "המספר התחתון" }],
      default: "top",
    });
    render(<CardView card={twoNumbers} onChoose={() => {}} />);

    expect(screen.getByRole("button", { name: "המספר העליון" })).toHaveClass("primary");
    expect(screen.getByRole("button", { name: "המספר התחתון" })).toHaveClass("chip");
    expect(screen.queryByRole("button", { name: "אישור" })).toBeNull();
  });

  it("shows an answered default as a done mark", () => {
    const twoNumbers = card({
      options: [{ id: "top", label: "המספר העליון" }, { id: "bottom", label: "המספר התחתון" }],
      default: "top",
    });
    render(<CardView card={twoNumbers} onChoose={() => {}} answer="המספר העליון" />);

    expect(screen.getByText("✓ המספר העליון")).toHaveClass("done");
  });

  it("shows every option as a chip on a card without a default", () => {
    render(<CardView card={card({ default: null })} onChoose={() => {}} />);

    expect(screen.getByRole("button", { name: "אישור" })).toHaveClass("chip");
  });
});
