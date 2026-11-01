import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { fakeApi, message, snapshot, wrapper } from "../testChat";
import { texts } from "../texts";
import type { Message } from "../types";
import { Sidebar } from "./Sidebar";

const photo = (key: string, at: string): Message => ({
  ...message("user", ""), text: null, image_key: key, at,
});

function setup(messages: Message[]) {
  const api = fakeApi({ messages, snapshot: snapshot(null) });
  (api.fileUrl as ReturnType<typeof vi.fn>)
    .mockImplementation(async (key: string) => `https://files/${key}`);
  const onOpen = vi.fn();
  render(<Sidebar messages={messages} api={api} onOpen={onOpen} />, { wrapper: wrapper().Wrapper });
  return onOpen;
}

describe("Sidebar", () => {
  it("lists only messages with a photo, newest first", async () => {
    setup([
      photo("old", "2026-10-01T10:00:00Z"),
      message("user", "שלום"),
      photo("new", "2026-10-02T10:00:00Z"),
    ]);

    await waitFor(() => expect(screen.getAllByRole("img")).toHaveLength(2));
    expect(screen.getAllByRole("img").map((i) => i.getAttribute("src")))
      .toEqual(["https://files/new", "https://files/old"]);
  });

  it("opens a thumbnail's photo through onOpen", async () => {
    const onOpen = setup([photo("a", "2026-10-01T10:00:00Z")]);

    await userEvent.click(await screen.findByRole("button", { name: texts.photo }));

    expect(onOpen).toHaveBeenCalledWith("https://files/a");
  });
});
