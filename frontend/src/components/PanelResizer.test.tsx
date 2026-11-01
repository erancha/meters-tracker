import { beforeEach, describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { texts } from "../texts";
import { DEFAULT_PANEL_WIDTH, MIN_CHAT_WIDTH, MIN_PANEL_WIDTH, PanelResizer, usePanelWidth }
  from "./PanelResizer";

const PAGE_LEFT = 100;

function Page() {
  const panel = usePanelWidth();
  return (
    <div data-testid="page" style={panel.style}>
      <PanelResizer width={panel.width} resize={panel.resize} />
    </div>
  );
}

function setup(pageWidth: number) {
  const { unmount } = render(<Page />);
  const page = screen.getByTestId("page");
  page.getBoundingClientRect = () => ({ left: PAGE_LEFT, width: pageWidth }) as DOMRect;
  const shown = () => page.style.getPropertyValue("--panel-width");
  return { unmount, shown, border: screen.getByRole("separator", { name: texts.resizePanel }) };
}

function grab(border: HTMLElement) {
  // jsdom implements no pointer capture.
  let held = false;
  border.setPointerCapture = () => { held = true; };
  border.hasPointerCapture = () => held;
  fireEvent.pointerDown(border, { pointerId: 1 });
}

beforeEach(() => localStorage.clear());

describe("PanelResizer", () => {
  it("gives a new visitor an 18rem panel", () => {
    const { shown } = setup(1152);

    expect(shown()).toBe("288px");
  });

  it("follows a drag, measured from the page's left edge", () => {
    const { border, shown } = setup(1152);

    grab(border);
    fireEvent.pointerMove(border, { pointerId: 1, clientX: PAGE_LEFT + 400 });

    expect(shown()).toBe("400px");
  });

  it("ignores a pointer passing over the border with no drag", () => {
    const { border, shown } = setup(1152);
    border.hasPointerCapture = () => false;

    fireEvent.pointerMove(border, { pointerId: 1, clientX: PAGE_LEFT + 400 });

    expect(shown()).toBe(`${DEFAULT_PANEL_WIDTH}px`);
  });

  it("moves the border with the arrow keys", () => {
    const { border, shown } = setup(1152);

    fireEvent.keyDown(border, { key: "ArrowRight" });
    expect(shown()).toBe(`${DEFAULT_PANEL_WIDTH + 20}px`);
    fireEvent.keyDown(border, { key: "ArrowLeft" });
    fireEvent.keyDown(border, { key: "ArrowLeft" });
    expect(shown()).toBe(`${DEFAULT_PANEL_WIDTH - 20}px`);
  });

  it("never narrows the panel below its minimum", () => {
    const { border, shown } = setup(1152);

    grab(border);
    fireEvent.pointerMove(border, { pointerId: 1, clientX: PAGE_LEFT });

    expect(shown()).toBe(`${MIN_PANEL_WIDTH}px`);
  });

  it("keeps the chat's minimum width", () => {
    const { border, shown } = setup(1152);

    grab(border);
    fireEvent.pointerMove(border, { pointerId: 1, clientX: PAGE_LEFT + 1152 });

    expect(shown()).toBe(`${1152 - MIN_CHAT_WIDTH}px`);
  });

  it("brings the chosen width back on the next visit", () => {
    const first = setup(1152);
    fireEvent.keyDown(first.border, { key: "ArrowRight" });
    first.unmount();

    expect(setup(1152).shown()).toBe(`${DEFAULT_PANEL_WIDTH + 20}px`);
  });

  it.each(["", "wide", "NaN", "Infinity", "-5", String(MIN_PANEL_WIDTH - 1)])(
    "falls back to the default on the stored value %j",
    (value) => {
      localStorage.setItem("panel-width", value);

      expect(setup(1152).shown()).toBe(`${DEFAULT_PANEL_WIDTH}px`);
    },
  );

  it("hands the chat's minimum to the stylesheet", () => {
    setup(1152);

    expect(screen.getByTestId("page").style.getPropertyValue("--chat-min-width"))
      .toBe(`${MIN_CHAT_WIDTH}px`);
  });
});
