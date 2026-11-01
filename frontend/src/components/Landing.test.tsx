import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { texts } from "../texts";
import { Landing } from "./Landing";

const toggle = (name: string) => userEvent.click(screen.getByRole("button", { name }));

describe("Landing", () => {
  it("opens condensed: the name, the pitch paragraphs, then the more and sign-in buttons", () => {
    const { container } = render(<Landing onSignIn={() => {}} />);

    expect(screen.getByRole("heading", { name: texts.appTitle })).toBeInTheDocument();
    expect(container.querySelectorAll(".landing-condensed"))
      .toHaveLength(texts.landingCondensed.length);
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
    const more = screen.getByRole("button", { name: texts.more });
    const signIn = screen.getByRole("button", { name: texts.signIn });
    expect(more.compareDocumentPosition(signIn) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("sets the marked phrases in bold, without the markers", () => {
    const { container } = render(<Landing onSignIn={() => {}} />);

    expect(container.querySelector(".landing-condensed strong")).toHaveTextContent("מוני מים וחשמל");
    expect(container.textContent).not.toContain("**");
  });

  it("swaps in the full summary when expanded, and folds back", async () => {
    const { container } = render(<Landing onSignIn={() => {}} />);

    await toggle(texts.more);

    expect(container.querySelector(".landing-condensed")).not.toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(texts.landingSummary.length);

    await toggle(texts.less);

    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });

  it("links to the demo in both modes, with its hint beside the link", async () => {
    render(<Landing onSignIn={() => {}} />);

    const demo = () => screen.getByRole("link", { name: texts.demo });
    expect(demo()).toHaveAttribute("href", "demo.html");
    expect(demo().parentElement).toHaveTextContent(texts.demoHint);

    await toggle(texts.more);

    expect(demo()).toHaveAttribute("href", "demo.html");
  });

  it("names the build's version and commit date under the name, in both modes", async () => {
    render(<Landing onSignIn={() => {}} />);

    const version = () => screen.getByText(/^גירסה \d+\.\d+\.\d+, \d{2}\/\d{2}\/\d{4}$/);
    expect(screen.getByRole("heading").nextElementSibling).toBe(version());

    await toggle(texts.more);

    expect(version()).toBeInTheDocument();
  });

  it("follows the demo link with the invite, then the source link, in both modes", async () => {
    render(<Landing onSignIn={() => {}} />);

    const links = () => screen.getAllByRole("link").map((link) => link.textContent);
    expect(links()).toEqual([texts.demo, texts.invite, texts.source]);

    await toggle(texts.more);

    expect(links()).toEqual([texts.demo, texts.invite, texts.source]);
  });

  it("links to the source repository", () => {
    render(<Landing onSignIn={() => {}} />);

    expect(screen.getByRole("link", { name: texts.source }))
      .toHaveAttribute("href", "https://github.com/erancha/meters-tracker");
  });

  it("prefills the invite as a received recommendation closing on the app's address", () => {
    render(<Landing onSignIn={() => {}} />);

    const href = screen.getByRole("link", { name: texts.invite }).getAttribute("href")!;
    expect(href.startsWith("https://wa.me/?text=")).toBe(true);
    const message = decodeURIComponent(href.slice("https://wa.me/?text=".length));
    expect(message).toContain("קיבלתי המלצה");
    expect(message.split("\n").at(-1)).toBe(window.location.origin);
  });

  it("invokes onSignIn in both modes", async () => {
    const onSignIn = vi.fn();
    render(<Landing onSignIn={onSignIn} />);

    await toggle(texts.signIn);
    await toggle(texts.more);
    await toggle(texts.signIn);

    expect(onSignIn).toHaveBeenCalledTimes(2);
  });
});
