import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { texts } from "../texts";
import { Lightbox } from "./Lightbox";

describe("Lightbox", () => {
  it("shows the photo full screen", () => {
    render(<Lightbox src="blob:x" onClose={() => {}} />);

    expect(screen.getByRole("img")).toHaveAttribute("src", "blob:x");
  });

  it("closes with the close button", async () => {
    const onClose = vi.fn();
    render(<Lightbox src="blob:x" onClose={onClose} />);

    await userEvent.click(screen.getByRole("button", { name: texts.close }));

    expect(onClose).toHaveBeenCalledOnce();
  });

  it("closes with Escape", async () => {
    const onClose = vi.fn();
    render(<Lightbox src="blob:x" onClose={onClose} />);

    await userEvent.keyboard("{Escape}");

    expect(onClose).toHaveBeenCalledOnce();
  });
});
