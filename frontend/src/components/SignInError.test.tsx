import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { texts } from "../texts";
import { SignInError } from "./SignInError";

const reason = "הכתובת אינה מורשית";

describe("SignInError", () => {
  it("shows the reason with a button that starts sign-in again", async () => {
    const onSignIn = vi.fn();
    render(<SignInError reason={reason} adminEmail="admin@x.com" onSignIn={onSignIn} />);

    expect(screen.getByRole("alert")).toHaveTextContent(reason);
    await userEvent.click(screen.getByRole("button", { name: texts.signInAnotherAccount }));

    expect(onSignIn).toHaveBeenCalledOnce();
  });

  it("copies the admin's address to the clipboard and says so, beside the sign-in button", async () => {
    const user = userEvent.setup();
    render(<SignInError reason={reason} adminEmail="admin@x.com" onSignIn={() => {}} />);
    const copy = screen.getByRole("button", { name: texts.copyAdminEmail });
    expect(copy.parentElement)
      .toBe(screen.getByRole("button", { name: texts.signInAnotherAccount }).parentElement);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    await user.click(copy);

    expect(await navigator.clipboard.readText()).toBe("admin@x.com");
    expect(screen.getByRole("status")).toHaveTextContent(texts.adminEmailCopied("admin@x.com"));
  });
});
