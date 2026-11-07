import { afterEach, describe, expect, it, vi, type Mock } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { jerusalemDay, shiftDay } from "../messageTime";
import { fakeApi, installResizeObserver, message, snapshot, wrapper } from "../testChat";
import { texts } from "../texts";
import type { Message } from "../types";
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

async function setup(earlierFailed: Message[] = []) {
  const api = fakeApi({
    messages: [{ ...message("user", ""), text: null, image_key: "k1" }],
    snapshot: snapshot(null),
  });
  const served = await api.getChat(jerusalemDay());
  (api.getChat as Mock).mockClear().mockImplementation(async (day: string) => ({
    ...served, earlier_failed: day === jerusalemDay() ? earlierFailed : [],
  }));
  (api.fileUrl as ReturnType<typeof vi.fn>).mockResolvedValue("https://files/k1");
  render(<App email="a@b.com" api={api} onSignOut={() => {}} />, { wrapper: wrapper().Wrapper });
  return api;
}

afterEach(() => vi.unstubAllGlobals());

describe("App", () => {
  it("shows the sidebar when the window is at least the configured width", async () => {
    stubScreen(900, 900);
    await setup();

    expect(await screen.findByRole("complementary", { name: texts.dayTitle }))
      .toBeInTheDocument();
    expect(screen.getByRole("separator", { name: texts.resizePanel })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: texts.dayTitle })).toBeNull();
  });

  it("shows no sidebar below the configured width", async () => {
    stubScreen(900, 899);
    await setup();

    await screen.findByRole("button", { name: texts.accountMenu });
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledWith("/app.json"));
    expect(screen.queryByRole("complementary")).toBeNull();
    expect(screen.queryByRole("separator")).toBeNull();
  });

  it("shows the day strip below the configured width", async () => {
    stubScreen(900, 899);
    await setup();

    expect(await screen.findByRole("region", { name: texts.dayTitle })).toBeInTheDocument();
  });

  it("shows neither the sidebar nor the strip until the config has loaded", async () => {
    stubScreen(900, 899);
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
    await setup();

    await screen.findByRole("button", { name: texts.accountMenu });
    expect(screen.queryByRole("region", { name: texts.dayTitle })).toBeNull();
    expect(screen.queryByRole("complementary")).toBeNull();
  });

  it("loads the previous day's chat and shows that the composer records today", async () => {
    stubScreen(900, 899);
    installResizeObserver();
    const api = await setup();
    const yesterday = shiftDay(jerusalemDay(), -1);

    await userEvent.click(await screen.findByRole("button", { name: texts.previousDay }));

    await vi.waitFor(() => expect(api.getChat).toHaveBeenCalledWith(yesterday));
    expect(await screen.findByRole("status")).toHaveTextContent(texts.earlierDayNotice(
      `${yesterday.slice(8, 10)}/${yesterday.slice(5, 7)}`,
    ));
  });

  it("closes the composer on an earlier day with no reading", async () => {
    stubScreen(900, 899);
    installResizeObserver();
    const api = await setup();
    const served = await api.getChat(jerusalemDay());
    (api.getChat as Mock).mockResolvedValue({ ...served, messages: [] });

    await userEvent.click(await screen.findByRole("button", { name: texts.previousDay }));

    await vi.waitFor(() => expect(screen.getByRole("button", { name: texts.attach }))
      .toBeDisabled());
    expect(screen.getByPlaceholderText(texts.composerPlaceholder)).toBeDisabled();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("moves to the day of an earlier failed photo tapped in today's list", async () => {
    stubScreen(900, 900);
    installResizeObserver();
    const at = "2026-10-28T08:15:00.000000_cccc";
    const api = await setup([{ ...message("user", ""), text: null, image_key: "k0", at, failed: true }]);

    await userEvent.click(await screen.findByRole("button", { name: `28/10 08:15 · ${texts.held.failed}` }));

    await vi.waitFor(() => expect(api.getChat).toHaveBeenCalledWith("2026-10-28"));
  });
});
