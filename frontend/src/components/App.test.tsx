import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
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

    expect(await screen.findByRole("complementary", { name: texts.dayTitle }))
      .toBeInTheDocument();
    expect(screen.getByRole("separator", { name: texts.resizePanel })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: texts.dayTitle })).toBeNull();
  });

  it("shows no sidebar below the configured width", async () => {
    stubScreen(900, 899);
    setup();

    await screen.findByRole("button", { name: texts.accountMenu });
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledWith("/app.json"));
    expect(screen.queryByRole("complementary")).toBeNull();
    expect(screen.queryByRole("separator")).toBeNull();
  });

  it("shows the day strip below the configured width", async () => {
    stubScreen(900, 899);
    setup();

    expect(await screen.findByRole("region", { name: texts.dayTitle })).toBeInTheDocument();
  });

  it("shows neither the sidebar nor the strip until the config has loaded", async () => {
    stubScreen(900, 899);
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
    setup();

    await screen.findByRole("button", { name: texts.accountMenu });
    expect(screen.queryByRole("region", { name: texts.dayTitle })).toBeNull();
    expect(screen.queryByRole("complementary")).toBeNull();
  });
});
