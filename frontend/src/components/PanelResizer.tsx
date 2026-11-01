/**
 * The day panel's width and the draggable border that sets it, between the panel, on the page's
 * left, and the chat. usePanelWidth keeps the width in local storage and hands it to the
 * stylesheet; PanelResizer is the border, which also moves in steps with the arrow keys.
 */
import { useState } from "react";
import type { CSSProperties, KeyboardEvent, PointerEvent } from "react";
import { texts } from "../texts";

/** 18rem at the browser's default font size. */
export const DEFAULT_PANEL_WIDTH = 288;
export const MIN_PANEL_WIDTH = 192;
export const MIN_CHAT_WIDTH = 360;
const STEP = 20;
const STORAGE_KEY = "panel-width";

function stored(): number {
  const value = Number(localStorage.getItem(STORAGE_KEY));
  return Number.isFinite(value) && value >= MIN_PANEL_WIDTH ? value : DEFAULT_PANEL_WIDTH;
}

export function usePanelWidth() {
  const [width, setWidth] = useState(stored);
  const resize = (next: number, pageWidth: number) => {
    const clamped = Math.round(Math.min(
      Math.max(next, MIN_PANEL_WIDTH),
      Math.max(pageWidth - MIN_CHAT_WIDTH, MIN_PANEL_WIDTH),
    ));
    setWidth(clamped);
    localStorage.setItem(STORAGE_KEY, String(clamped));
  };
  // The stylesheet also caps the panel by the chat's minimum, since a stored width may come from
  // a wider window than this one.
  const style = {
    "--panel-width": `${width}px`, "--chat-min-width": `${MIN_CHAT_WIDTH}px`,
  } as CSSProperties;
  return { width, resize, style };
}

export function PanelResizer({ width, resize }: {
  width: number;
  resize: (next: number, pageWidth: number) => void;
}) {
  const page = (element: Element) => element.parentElement!.getBoundingClientRect();

  function onPointerMove(event: PointerEvent<HTMLDivElement>) {
    if (!event.currentTarget.hasPointerCapture(event.pointerId)) return;
    const bounds = page(event.currentTarget);
    resize(event.clientX - bounds.left, bounds.width);
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const delta = { ArrowRight: STEP, ArrowLeft: -STEP }[event.key];
    if (delta === undefined) return;
    event.preventDefault();
    resize(width + delta, page(event.currentTarget).width);
  }

  return (
    <div className="panel-resizer" role="separator" aria-orientation="vertical"
         aria-label={texts.resizePanel} aria-valuenow={width} aria-valuemin={MIN_PANEL_WIDTH}
         tabIndex={0}
         onPointerDown={(event) => event.currentTarget.setPointerCapture(event.pointerId)}
         onPointerMove={onPointerMove} onKeyDown={onKeyDown} />
  );
}
