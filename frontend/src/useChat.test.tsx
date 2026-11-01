import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { ApiError } from "./api";
import { card, fakeApi, message, snapshot, wrapper } from "./testChat";
import { useChat } from "./useChat";
import type { Mock } from "vitest";

describe("useChat", () => {
  it("loads the history and the snapshot", async () => {
    const api = fakeApi({ messages: [message("user", "שלום")], snapshot: snapshot(card(), 1) });
    const { result } = renderHook(() => useChat(api), { wrapper: wrapper().Wrapper });

    await waitFor(() => expect(result.current.messages).toHaveLength(1));
    expect(result.current.snapshot).toEqual(snapshot(card(), 1));
  });

  it("writes the user text, the replies and the snapshot into the query cache", async () => {
    const api = fakeApi({ messages: [], snapshot: snapshot(null) });
    (api.postTurn as Mock).mockResolvedValue({
      replies: [{ text: "הבנתי", card: card(), image_key: null }],
      snapshot: snapshot(card(), 3),
      image_key: null,
    });
    const w = wrapper();
    const { result } = renderHook(() => useChat(api), { wrapper: w.Wrapper });
    await waitFor(() => expect(result.current.snapshot).not.toBeUndefined());
    (api.getChat as Mock).mockResolvedValue({
      messages: [message("user", "היי"), message("assistant", "הבנתי")],
      snapshot: snapshot(card(), 3),
    });

    await act(async () => result.current.sendText("היי"));

    await waitFor(() => expect(result.current.messages).toHaveLength(2));
    expect(result.current.messages.map((m) => [m.role, m.text])).toEqual([
      ["user", "היי"],
      ["assistant", "הבנתי"],
    ]);
    expect(result.current.snapshot?.waiting).toBe(3);
    expect(w.client.getQueryData(["chat"])).toBeDefined();
  });

  it("keeps the messages and takes the snapshot of a 409", async () => {
    const api = fakeApi({ messages: [message("user", "שלום")], snapshot: snapshot(card()) });
    (api.postTurn as Mock).mockRejectedValue(
      new ApiError(409, "409", "already_handled", snapshot(null)),
    );
    (api.getChat as Mock).mockResolvedValue({
      messages: [message("user", "שלום")],
      snapshot: snapshot(null),
    });
    const { result } = renderHook(() => useChat(api), { wrapper: wrapper().Wrapper });
    await waitFor(() => expect(result.current.snapshot?.card).not.toBeNull());

    await act(async () => result.current.choose(card(), card().options[0]));

    await waitFor(() => expect(result.current.snapshot?.card).toBeNull());
    expect(result.current.messages).toHaveLength(1);
    expect(result.current.failed).toBe(false);
  });

  it("fails loudly on a 409 without a snapshot", async () => {
    const api = fakeApi({ messages: [], snapshot: snapshot(card()) });
    (api.postTurn as Mock).mockRejectedValue(new ApiError(409, "409", "already_handled"));
    const { result } = renderHook(() => useChat(api), { wrapper: wrapper().Wrapper });
    await waitFor(() => expect(result.current.snapshot).toBeDefined());

    await act(async () => result.current.choose(card(), card().options[0]));

    await waitFor(() => expect(result.current.failed).toBe(true));
    expect(result.current.snapshot?.card).not.toBeNull();
  });

  it("appends the chosen label as a user message at once", async () => {
    const api = fakeApi({ messages: [], snapshot: snapshot(card()) });
    (api.postTurn as Mock).mockReturnValue(new Promise(() => {}));
    const { result } = renderHook(() => useChat(api), { wrapper: wrapper().Wrapper });
    await waitFor(() => expect(result.current.snapshot).toBeDefined());

    await act(async () => result.current.choose(card(), card().options[0]));

    await waitFor(() => expect(result.current.messages.map((m) => [m.role, m.text]))
      .toEqual([["user", "אישור"]]));
  });

  it("settles to the server history when a refetch lands mid-turn", async () => {
    const base = [message("user", "ישן")];
    const reply = { text: "הבנתי", card: null, image_key: null };
    const settled = [...base, message("user", "היי"), message("assistant", "הבנתי")];
    let done = false;
    const api = fakeApi({ messages: base, snapshot: snapshot(null) });
    (api.getChat as Mock).mockImplementation(async () =>
      done ? { messages: settled, snapshot: snapshot(null) }
           : { messages: base, snapshot: snapshot(null) });
    let finish!: () => void;
    (api.postTurn as Mock).mockReturnValue(new Promise((r) => {
      finish = () => {
        done = true;
        r({ replies: [reply], snapshot: snapshot(null), image_key: null });
      };
    }));
    const w = wrapper();
    const { result } = renderHook(() => useChat(api), { wrapper: w.Wrapper });
    await waitFor(() => expect(result.current.messages).toHaveLength(1));

    await act(async () => result.current.sendText("היי"));
    await act(async () => { await w.client.refetchQueries({ queryKey: ["chat"] }); });
    await act(async () => finish());

    await waitFor(() => expect(result.current.messages.map((m) => m.text))
      .toEqual(["ישן", "היי", "הבנתי"]));
  });
});

describe("useChat lost turns", () => {
  it.each([
    ["text", "a network error", new TypeError("Failed to fetch")],
    ["text", "a 504", new ApiError(504, "504")],
    ["choice", "a network error", new TypeError("Failed to fetch")],
    ["choice", "a 503", new ApiError(503, "503")],
  ])("refetches without a failure notice after a %s turn lost to %s", async (kind, _n, error) => {
    const api = fakeApi({ messages: [], snapshot: snapshot(card()) });
    (api.postTurn as Mock).mockRejectedValue(error);
    const { result } = renderHook(() => useChat(api), { wrapper: wrapper().Wrapper });
    await waitFor(() => expect(result.current.snapshot).toBeDefined());
    (api.getChat as Mock).mockResolvedValue({ messages: [], snapshot: snapshot(null) });

    await act(async () => {
      if (kind === "text") result.current.sendText("היי");
      else result.current.choose(card(), card().options[0]);
    });

    await waitFor(() => expect(result.current.snapshot?.card).toBeNull());
    expect(result.current.failed).toBe(false);
  });
});

describe("useChat polling", () => {
  beforeEach(() => { vi.useFakeTimers({ shouldAdvanceTime: true }); });
  afterEach(() => { vi.useRealTimers(); });

  it("polls the chat while items wait, until the card arrives", async () => {
    const api = fakeApi({ messages: [], snapshot: snapshot(null, 1) });
    const { result } = renderHook(() => useChat(api), { wrapper: wrapper().Wrapper });
    await waitFor(() => expect(result.current.snapshot?.waiting).toBe(1));
    (api.getChat as Mock).mockResolvedValue({ messages: [], snapshot: snapshot(card()) });

    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });

    await waitFor(() => expect(result.current.snapshot?.card).not.toBeNull());
    const calls = (api.getChat as Mock).mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(15000); });
    expect(api.getChat).toHaveBeenCalledTimes(calls);
  });

  it("does not poll while a card is live, though more items wait behind it", async () => {
    const api = fakeApi({ messages: [], snapshot: snapshot(card(), 2) });
    const { result } = renderHook(() => useChat(api), { wrapper: wrapper().Wrapper });
    await waitFor(() => expect(result.current.snapshot?.waiting).toBe(2));

    await act(async () => { await vi.advanceTimersByTimeAsync(15000); });

    expect(api.getChat).toHaveBeenCalledTimes(1);
  });

  it("does not poll while a turn is in flight", async () => {
    const api = fakeApi({ messages: [], snapshot: snapshot(null, 1) });
    (api.postTurn as Mock).mockReturnValue(new Promise(() => {}));
    const { result } = renderHook(() => useChat(api), { wrapper: wrapper().Wrapper });
    await waitFor(() => expect(result.current.snapshot?.waiting).toBe(1));

    await act(async () => result.current.sendText("היי"));
    await act(async () => { await vi.advanceTimersByTimeAsync(15000); });

    expect(api.getChat).toHaveBeenCalledTimes(1);
  });
});

describe("useChat photos", () => {
  const files = ["a", "b", "c"].map((n) => new File([n], `${n}.jpg`, { type: "image/jpeg" }));
  const urls: string[] = [];

  beforeEach(() => {
    vi.stubGlobal("createImageBitmap", vi.fn().mockResolvedValue({ width: 10, height: 10 }));
    vi.spyOn(HTMLCanvasElement.prototype, "getContext")
      .mockReturnValue({ drawImage: vi.fn() } as unknown as CanvasRenderingContext2D);
    vi.spyOn(HTMLCanvasElement.prototype, "toBlob")
      .mockImplementation((cb) => cb(new Blob(["j"], { type: "image/jpeg" })));
    urls.length = 0;
    vi.spyOn(URL, "createObjectURL").mockImplementation(() => {
      urls.push(`blob:${urls.length}`);
      return urls.at(-1)!;
    });
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
  });
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

  const photoReply = (n: number) => ({
    replies: [{ text: `תמונה ${n}`, card: null, image_key: null }],
    snapshot: snapshot(card({ pending: `p${n}` }), 3 - n),
    image_key: `k${n}`,
  });

  async function ready(api: ReturnType<typeof fakeApi>) {
    const hook = renderHook(() => useChat(api), { wrapper: wrapper().Wrapper });
    await waitFor(() => expect(hook.result.current.snapshot).toBeDefined());
    return hook;
  }

  it("posts one turn per photo and shows local photo messages at once", async () => {
    const api = fakeApi({ messages: [], snapshot: snapshot(null) });
    (api.postTurn as Mock).mockReturnValue(new Promise(() => {}));
    const { result } = await ready(api);

    await act(async () => result.current.sendPhotos(files));

    await waitFor(() => expect(api.postTurn).toHaveBeenCalledTimes(3));
    expect((api.postTurn as Mock).mock.calls[0][0])
      .toEqual({ image: btoa("j"), media_type: "image/jpeg" });
    expect(result.current.messages.map((m) => m.localUrl)).toEqual(["blob:0", "blob:1", "blob:2"]);
    expect(result.current.choosing).toBe(false);
  });

  it("keeps the last snapshot received, and the sent photos' urls, after the refetch", async () => {
    const api = fakeApi({ messages: [], snapshot: snapshot(null) });
    let n = 0;
    (api.postTurn as Mock).mockImplementation(async () => photoReply(++n));
    const { result } = await ready(api);
    (api.getChat as Mock).mockResolvedValue({
      messages: [message("assistant", "תמונה 3")], snapshot: snapshot(card({ pending: "p3" })),
    });

    await act(async () => result.current.sendPhotos(files));

    await waitFor(() => expect(result.current.messages.map((m) => m.text)).toEqual(["תמונה 3"]));
    expect(result.current.snapshot?.card?.pending).toBe("p3");
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();
  });

  it("shows a sent photo's stored copy from the page's own copy, with no download", async () => {
    const api = fakeApi({ messages: [], snapshot: snapshot(null) });
    (api.postTurn as Mock).mockResolvedValue({ ...photoReply(1), image_key: "k1" });
    const w = wrapper();
    const { result } = renderHook(() => useChat(api), { wrapper: w.Wrapper });
    await waitFor(() => expect(result.current.snapshot).toBeDefined());
    (api.getChat as Mock).mockResolvedValue({
      messages: [{ ...message("user", ""), text: null, image_key: "k1" }],
      snapshot: snapshot(card({ pending: "p1" })),
    });

    await act(async () => result.current.sendPhotos([files[0]]));

    await waitFor(() => expect(result.current.messages.map((m) => m.image_key)).toEqual(["k1"]));
    expect(w.client.getQueryData(["file", "k1"])).toBe("blob:0");
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();
    expect(api.fileUrl).not.toHaveBeenCalled();
  });

  it.each([
    ["a network error", new TypeError("Failed to fetch")],
    ["a 502", new ApiError(502, "502")],
    ["a 504", new ApiError(504, "504")],
  ])("refetches without a failure notice after %s", async (_name, error) => {
    const api = fakeApi({ messages: [], snapshot: snapshot(null) });
    (api.postTurn as Mock).mockRejectedValue(error);
    const { result } = await ready(api);
    (api.getChat as Mock).mockResolvedValue({ messages: [], snapshot: snapshot(card()) });

    await act(async () => result.current.sendPhotos([files[0]]));

    await waitFor(() => expect(result.current.snapshot?.card).not.toBeNull());
    expect(result.current.failed).toBe(false);
    expect(result.current.rejected).toBe(false);
  });

  it.each(["image_too_large", "unsupported_image_type"])("flags a 400 %s as rejected", async (e) => {
    const api = fakeApi({ messages: [], snapshot: snapshot(null) });
    (api.postTurn as Mock).mockRejectedValue(new ApiError(400, "400", e));
    const { result } = await ready(api);

    await act(async () => result.current.sendPhotos([files[0]]));

    await waitFor(() => expect(result.current.rejected).toBe(true));
    expect(result.current.failed).toBe(false);
  });

  it("revokes the url of a photo the server never stored", async () => {
    const api = fakeApi({ messages: [], snapshot: snapshot(null) });
    (api.postTurn as Mock).mockRejectedValue(new ApiError(400, "400", "image_too_large"));
    const { result } = await ready(api);

    await act(async () => result.current.sendPhotos([files[0]]));

    await waitFor(() => expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:0"));
  });

  it("reports a failed downscale as failed, without posting", async () => {
    vi.stubGlobal("createImageBitmap", vi.fn().mockRejectedValue(new TypeError("bad image")));
    const api = fakeApi({ messages: [], snapshot: snapshot(null) });
    const { result } = await ready(api);

    await act(async () => result.current.sendPhotos([files[0]]));

    await waitFor(() => expect(result.current.failed).toBe(true));
    expect(api.postTurn).not.toHaveBeenCalled();
  });

  it("decodes photos one at a time", async () => {
    let active = 0;
    let peak = 0;
    vi.stubGlobal("createImageBitmap", vi.fn(async () => {
      peak = Math.max(peak, ++active);
      await new Promise((r) => setTimeout(r, 5));
      active--;
      return { width: 10, height: 10 };
    }));
    const api = fakeApi({ messages: [], snapshot: snapshot(null) });
    (api.postTurn as Mock).mockReturnValue(new Promise(() => {}));
    const { result } = await ready(api);

    await act(async () => result.current.sendPhotos(files));

    await waitFor(() => expect(api.postTurn).toHaveBeenCalledTimes(3));
    expect(peak).toBe(1);
  });

  it("still reports other photo failures as failed", async () => {
    const api = fakeApi({ messages: [], snapshot: snapshot(null) });
    (api.postTurn as Mock).mockRejectedValue(new ApiError(500, "500"));
    const { result } = await ready(api);

    await act(async () => result.current.sendPhotos([files[0]]));

    await waitFor(() => expect(result.current.failed).toBe(true));
  });
});
