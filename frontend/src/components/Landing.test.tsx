import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { texts } from "../texts";
import { Landing } from "./Landing";

const toggle = (name: string) => userEvent.click(screen.getByRole("button", { name }));

describe("Landing", () => {
  it("opens on the tab's icon, the product name, the tagline and the build", () => {
    const { container } = render(<Landing onSignIn={() => {}} />);

    const heading = screen.getByRole("heading", { name: "מונים AI" });
    expect(heading).toHaveClass("app-heading");
    expect(heading.querySelector("img.app-icon")).toHaveAttribute("src", "favicon.svg");
    expect(container.querySelector(".app-tagline"))
      .toHaveTextContent("חיוב דירות לפי מוני מים וחשמל");
    expect(container.querySelector(".app-version"))
      .toHaveTextContent(/^גירסה \d+\.\d+\.\d+, \d{2}\/\d{2}\/\d{4}$/);
  });

  it("pitches the photo and the chat, then three numbered steps, the marks in bold", () => {
    const { container } = render(<Landing onSignIn={() => {}} />);

    const pitch = [...container.querySelectorAll(".landing-pitch")];
    expect(pitch).toHaveLength(2);
    expect(pitch[0].querySelector("strong")).toHaveTextContent(/^מצלמים את המונה/);
    const steps = [...container.querySelectorAll(".landing-steps > li")];
    expect(steps.map((step) => step.querySelector("strong")?.textContent)).toEqual([
      "מצלמים את המונה בצ'אט",
      "רואים את הצריכה והחיוב",
      "סוגרים את תקופת החיוב",
    ]);
    expect(container.textContent).not.toContain("**");
  });

  it("adds the details before the toggle from the more toggle, and folds them back", async () => {
    const { container } = render(<Landing onSignIn={() => {}} />);
    expect(screen.getByRole("button", { name: texts.more }))
      .toHaveAttribute("aria-expanded", "false");
    expect(container.querySelector(".landing-card > .landing-details")).not.toBeInTheDocument();

    await toggle(texts.more);

    const details = container.querySelector(".landing-card > .landing-details")!;
    expect(details.querySelectorAll("li")).toHaveLength(texts.landingDetails.length);
    const less = screen.getByRole("button", { name: texts.less });
    expect(less).toHaveAttribute("aria-expanded", "true");
    expect(details.compareDocumentPosition(less) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    await toggle(texts.less);

    expect(container.querySelector(".landing-card > .landing-details")).not.toBeInTheDocument();
  });

  it("opening the sign-in explanation folds the details back", async () => {
    render(<Landing onSignIn={() => {}} />);
    const more = screen.getByRole("button", { name: texts.more });
    fireEvent.click(more);
    fireEvent.click(document.querySelector(".sign-in-why") as HTMLElement);
    expect(document.querySelector(".landing-card > .landing-details")).toBeNull();
    expect(more).toHaveAttribute("aria-expanded", "false");
  });

  it("links to the demo, the WhatsApp invite and the source under the card", () => {
    const { container } = render(<Landing onSignIn={() => {}} />);

    const footer = container.querySelector("main.landing > footer.landing-links")!;
    const links = [...footer.querySelectorAll("a")];
    expect(links.map((link) => link.textContent)).toEqual([
      "▶️הדגמה", "💬הזמנת חברים ב-WhatsApp", "קוד המקור ב-GitHub",
    ]);
    const [demo, invite, source] = links;
    expect(demo).toHaveAttribute("href", "demo.html");
    expect(demo).toHaveAttribute("rel", "opener");
    // The hint is the demo link's tooltip, so the line holds only the link names.
    expect(demo).toHaveAttribute("title", texts.demoHint);
    expect(demo.querySelector(".link-mark")).toHaveAttribute("aria-hidden", "true");
    expect(invite).toHaveAttribute("rel", "noreferrer");
    expect(source).toHaveAttribute("href", "https://github.com/erancha/meters-tracker");
    expect(source).toHaveAttribute("rel", "noreferrer");
    for (const link of links) expect(link).toHaveAttribute("target", "_blank");
  });

  it("prefills the invite as a received recommendation, in paragraphs, closing on the address",
     () => {
    render(<Landing onSignIn={() => {}} />);

    const href = screen.getByRole("link", { name: texts.invite }).getAttribute("href")!;
    expect(href.startsWith("https://wa.me/?text=")).toBe(true);
    const message = decodeURIComponent(href.slice("https://wa.me/?text=".length));
    const paragraphs = message.split("\n\n");
    expect(paragraphs).toHaveLength(texts.inviteMessage.length);
    expect(paragraphs[0]).toContain("קיבלתי המלצה");
    expect(paragraphs.at(-1)).toBe(`${texts.inviteMessage.at(-1)}\n${window.location.origin}`);
  });

  it("signs in through the handler it is given, from our own Google button", async () => {
    const onSignIn = vi.fn();
    render(<Landing onSignIn={onSignIn} />);

    const button = screen.getByRole("button", { name: texts.signIn });
    expect(button).toHaveClass("sign-in-button");
    expect(button.querySelector(".sign-in-mark")).toHaveTextContent("G");
    await userEvent.click(button);

    expect(onSignIn).toHaveBeenCalledOnce();
  });
});

describe("Landing's explanation of signing in with Google", () => {
  const why = "למה להתחבר עם Google?";
  const closeWhy = "סגירת ההסבר";

  it("stays closed until asked for", () => {
    render(<Landing onSignIn={() => {}} />);

    expect(screen.getByRole("button", { name: why })).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText(/בטוחה ונוחה יותר/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: closeWhy })).not.toBeInTheDocument();
  });

  it("opens under the sign-in button with four points and the link to Google", async () => {
    const { container } = render(<Landing onSignIn={() => {}} />);
    const whyToggle = screen.getByRole("button", { name: why });
    const signIn = screen.getByRole("button", { name: texts.signIn });
    expect(signIn.compareDocumentPosition(whyToggle) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    await userEvent.click(whyToggle);

    expect(whyToggle).toHaveAttribute("aria-expanded", "true");
    expect(container.querySelector(".sign-in")).toHaveClass("sign-in-open");
    expect(screen.getByText("בטוחה ונוחה יותר").tagName).toBe("STRONG");
    const points = [...container.querySelectorAll(".sign-in-why-text .landing-details li")];
    expect(points).toHaveLength(4);
    expect(points[0]).toHaveTextContent(
      "ההתחברות מאושרת על ידי Google.\u200F Google מוסרת לאפליקציה רק אימות לגבי"
        + " הזהות שלכם, ללא גישה לנתונים שלכם (מיילים, אנשי קשר ועוד).",
    );
    expect(points[1]).toHaveTextContent("אפשר לנתק את האפליקציה בכל רגע מהגדרות חשבון Google.");
    expect(points[2]).toHaveTextContent("האפליקציה לא שומרת סיסמאות בכלל.");
    expect(points.map((point) => point.querySelector("strong"))).not.toContain(null);
    expect(container.textContent).not.toContain("**");
    // The link says it opens a new tab, so nobody takes it for the way to close the explanation.
    const link = screen.getByRole("link", { name: "ההסבר המלא של Google (נפתח בכרטיסייה חדשה)" });
    expect(link).toHaveAttribute("href", "https://support.google.com/accounts/answer/12921417");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noreferrer");
  });

  it("closes from its close button, returning focus to the toggle", async () => {
    const { container } = render(<Landing onSignIn={() => {}} />);
    const scroll = vi.spyOn(Element.prototype, "scrollIntoView");
    const whyToggle = screen.getByRole("button", { name: why });

    await userEvent.click(whyToggle);
    // Opening scrolls down to the explanation's last line.
    expect(scroll).toHaveBeenLastCalledWith({ behavior: "smooth", block: "end" });
    expect(scroll.mock.instances).toContain(container.querySelector(".sign-in-why-text"));
    await userEvent.click(screen.getByRole("button", { name: closeWhy }));

    expect(whyToggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText(/בטוחה ונוחה יותר/)).not.toBeInTheDocument();
    expect(container.querySelector(".sign-in")).not.toHaveClass("sign-in-open");
    expect(whyToggle).toHaveFocus();
  });
});
