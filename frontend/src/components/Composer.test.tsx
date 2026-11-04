import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { texts } from "../texts";
import { Composer } from "./Composer";

const send = () => screen.getByRole("button", { name: texts.send });
const box = () => screen.getByPlaceholderText(texts.composerPlaceholder);

describe("Composer", () => {
  it("offers no way to send when locked", () => {
    render(<Composer onSend={() => {}} onPhotos={() => {}} locked />);

    expect(box()).toBeDisabled();
    expect(screen.getByRole("button", { name: texts.attach })).toBeDisabled();
    expect(send()).toBeDisabled();
  });

  it("disables send for an empty draft", () => {
    render(<Composer onSend={() => {}} onPhotos={() => {}} />);

    expect(send()).toBeDisabled();
  });

  it("disables send for a whitespace-only draft", async () => {
    render(<Composer onSend={() => {}} onPhotos={() => {}} />);

    await userEvent.type(box(), "   ");

    expect(send()).toBeDisabled();
  });

  it("sends the draft with the button and clears it", async () => {
    const onSend = vi.fn();
    render(<Composer onSend={onSend} onPhotos={() => {}} />);

    await userEvent.type(box(), "שלום");
    await userEvent.click(send());

    expect(onSend).toHaveBeenCalledWith("שלום");
    expect(box()).toHaveValue("");
  });

  it("sends with Enter", async () => {
    const onSend = vi.fn();
    render(<Composer onSend={onSend} onPhotos={() => {}} />);

    await userEvent.type(box(), "שלום{Enter}");

    expect(onSend).toHaveBeenCalledWith("שלום");
  });

  it("passes the chosen photos on, from a file input that offers the camera and the gallery", async () => {
    const onPhotos = vi.fn();
    render(<Composer onSend={() => {}} onPhotos={onPhotos} />);
    const input = screen.getByTestId("photo-input");
    const files = [new File(["a"], "a.jpg", { type: "image/jpeg" }),
                   new File(["b"], "b.jpg", { type: "image/jpeg" })];

    await userEvent.upload(input, files);

    // A capture attribute would open the camera directly, with no way to pick an existing photo.
    expect(input).not.toHaveAttribute("capture");
    expect(input).toHaveAttribute("multiple");
    expect(input).toHaveAttribute("accept", "image/*");
    expect(onPhotos).toHaveBeenCalledWith(files);
  });
});
