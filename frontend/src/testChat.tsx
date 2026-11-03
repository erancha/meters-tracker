// Test fixtures shared by the chat tests: card and snapshot builders, a fake Api, a query wrapper.

import { vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import type { Api } from "./api";
import type { Card, Message, Snapshot } from "./types";

export const card = (over: Partial<Card> = {}): Card => ({
  kind: "reading",
  pending: "p1",
  version: 1,
  title: "קריאה חדשה",
  lines: ["חשמל 123.4"],
  options: [{ id: "confirm", label: "אישור" }, { id: "other", label: "דירה אחרת" }],
  default: "confirm",
  ...over,
});

export const message = (role: Message["role"], text: string): Message => ({
  at: "2026-10-29T10:00:00Z", role, text, card: null, image_key: null, pdf_key: null,
  choice: null,
});

/** The user message the backend records for a choice of the given option on card c. */
export const answer = (c: Card, label: string): Message => ({
  ...message("user", label), choice: { pending: c.pending, version: c.version },
});

export const snapshot = (c: Card | null, waiting = 0, tariffsMissing = false): Snapshot => ({
  card: c, waiting, tariffs_missing: tariffsMissing,
});

/** The server's chat; a test that expects the post-turn refetch to differ sets getChat itself. */
export function fakeApi(chat: { messages: Message[]; snapshot: Snapshot }): Api {
  return {
    getChat: vi.fn().mockResolvedValue(chat),
    postTurn: vi.fn(),
    fileUrl: vi.fn(),
  };
}

export function wrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return {
    client,
    Wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  };
}

/**
 * Stands in for the browser's ResizeObserver, which jsdom lacks: grow() reports a size change to
 * every observer made since the install.
 */
export function installResizeObserver(): { grow: () => void } {
  const callbacks: ResizeObserverCallback[] = [];
  vi.stubGlobal("ResizeObserver", class {
    constructor(cb: ResizeObserverCallback) { callbacks.push(cb); }
    observe() {}
    unobserve() {}
    disconnect() {}
  });
  return { grow: () => callbacks.forEach((cb) => cb([], {} as ResizeObserver)) };
}
