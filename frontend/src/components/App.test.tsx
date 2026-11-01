import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { fakeApi, message, snapshot, wrapper } from "../testChat";
import { texts } from "../texts";
import { App } from "./App";

function stubScreen(minWidth: number, width: number) {
  vi.stubGlobal("matchMedia", (query: string) => {
    const n = Number(/min-width:\s*(\d+)px/.exec(query)![1]);
    return {
      matches: width >= n, media: query,
      addEventListener: () => {}, removeEventListener: () => {},
    };
  });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: true, json: async () => ({ sidebar_min_width_px: minWidth }),
  }));
}

function setup() {
  const api = fakeApi({
    messages: [{ ...message("user", ""), text: null, image_key: "k1" }],
    snapshot: snapshot(null),
  });
  (api.fileUrl as ReturnType<typeof vi.fn>).mockResolvedValue("https://files/k1");
  render(<App email="a@b.com" api={api} onSignOut={() => {}} />, { wrapper: wrapper().Wrapper });
}

afterEach(() => vi.unstubAllGlobals());

describe("App", () => {
  it("shows the sidebar when the window is at least the configured width", async () => {
    stubScreen(900, 900);
    setup();

    expect(await screen.findByRole("complementary", { name: texts.photosTitle }))
      .toBeInTheDocument();
  });

  it("shows no sidebar below the configured width", async () => {
    stubScreen(900, 899);
    setup();

    await screen.findByRole("button", { name: texts.accountMenu });
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledWith("/app.json"));
    expect(screen.queryByRole("complementary")).toBeNull();
  });

  it("opens a sidebar photo in one lightbox outside the sidebar's stacking context", async () => {
    stubScreen(900, 900);
    setup();
    const sidebar = await screen.findByRole("complementary", { name: texts.photosTitle });

    await userEvent.click(await within(sidebar).findByRole("button", { name: texts.photo }));

    const close = screen.getByRole("button", { name: texts.close });
    expect(sidebar).not.toContainElement(close);
    expect(document.querySelectorAll(".lightbox")).toHaveLength(1);
    await userEvent.click(close);
    expect(document.querySelector(".lightbox")).toBeNull();
  });
});
