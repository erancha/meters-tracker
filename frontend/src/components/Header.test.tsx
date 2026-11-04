import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { texts } from "../texts";
import { Header } from "./Header";

const openMenu = () => userEvent.click(screen.getByRole("button", { name: texts.accountMenu }));

describe("Header", () => {
  it("shows the app title and keeps the email in the closed menu out of the page", () => {
    render(<Header email="a@b.com" onSignOut={() => {}} />);

    expect(screen.getByText(texts.appTitle)).toBeInTheDocument();
    expect(screen.queryByText("a@b.com")).toBeNull();
  });

  it("opens the menu with the email and a sign-out item that calls onSignOut", async () => {
    const onSignOut = vi.fn();
    render(<Header email="a@b.com" onSignOut={onSignOut} />);

    await openMenu();
    expect(screen.getByText("a@b.com")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("menuitem", { name: texts.signOut }));

    expect(onSignOut).toHaveBeenCalledOnce();
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("closes on a click outside", async () => {
    render(<Header email="a@b.com" onSignOut={() => {}} />);
    await openMenu();

    await userEvent.click(document.body);

    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("closes on Escape", async () => {
    render(<Header email="a@b.com" onSignOut={() => {}} />);
    await openMenu();

    await userEvent.keyboard("{Escape}");

    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("closes when the trigger is pressed again", async () => {
    render(<Header email="a@b.com" onSignOut={() => {}} />);
    await openMenu();

    await openMenu();

    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("names the app in the header and the browser tab alike", () => {
    expect(texts.appTitle).toBe("מונים AI");
    expect(readFileSync("index.html", "utf8")).toContain(`<title>${texts.appTitle}</title>`);
  });

  // The day strip and the sidebar stick at --head-height, so the header must be exactly that tall
  // or the page scrolls visibly through the gap between them.
  it("takes its height from the offset the bars below it stick at", () => {
    const rule = readFileSync("src/style.css", "utf8").match(/#root \.app-head \{([^}]*)\}/)?.[1];

    expect(rule).toMatch(/height: var\(--head-height\)/);
    expect(rule).toMatch(/box-sizing: border-box/);
  });
});
