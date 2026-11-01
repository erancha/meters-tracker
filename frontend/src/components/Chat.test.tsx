import { afterEach, describe, expect, it, vi, type Mock } from "vitest";
import type { Api } from "../api";
import type { TurnResult } from "../types";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApiError } from "../api";
import { answer, card, fakeApi, installResizeObserver, message, snapshot, wrapper } from "../testChat";
import { texts } from "../texts";
import { Chat } from "./Chat";

function setup(chat: Parameters<typeof fakeApi>[0]) {
  const api = fakeApi(chat);
  const w = wrapper();
  const onOpen = vi.fn();
  render(<Chat api={api} onOpen={onOpen} />, { wrapper: w.Wrapper });
  return { api, client: w.client, onOpen };
}

const history = [message("user", "שלום"), message("assistant", "היי, מה נעשה?")];

const cardMessage = (c = card()) => ({
  ...message("assistant", [c.title, ...c.lines].join("\n")), card: c,
});

const jpeg = (name: string) => new File(["x"], `${name}.jpg`, { type: "image/jpeg" });

function stubPhotoPipeline() {
  vi.stubGlobal("createImageBitmap", vi.fn().mockResolvedValue({ width: 1, height: 1 }));
  vi.spyOn(HTMLCanvasElement.prototype, "getContext")
    .mockReturnValue({ drawImage: vi.fn() } as unknown as CanvasRenderingContext2D);
  vi.spyOn(HTMLCanvasElement.prototype, "toBlob")
    .mockImplementation((cb) => cb(new Blob(["j"], { type: "image/jpeg" })));
  vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:1");
}

describe("Chat", () => {
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

  it("scrolls to the message a focus names and highlights it for a moment", async () => {
    const api = fakeApi({ messages: history, snapshot: snapshot(null) });
    render(<Chat api={api} onOpen={vi.fn()} focus={{ at: history[0].at, n: 1 }} />,
           { wrapper: wrapper().Wrapper });

    const bubble = (await screen.findByText("שלום")).closest("li")!;

    await waitFor(() => expect(bubble).toHaveClass("focused"));
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
  });

  it("shows a card message once, not as text and as card", async () => {
    setup({ messages: [...history, cardMessage()], snapshot: snapshot(card()) });

    await screen.findByText("קריאה חדשה");

    expect(screen.getAllByText(/קריאה חדשה/)).toHaveLength(1);
    expect(screen.getByRole("button", { name: "אישור" })).toBeInTheDocument();
  });

  it("keeps the live card in its own place, before later messages", async () => {
    setup({
      messages: [cardMessage(), message("assistant", "הודעה מאוחרת")],
      snapshot: snapshot(card()),
    });

    const title = await screen.findByText("קריאה חדשה");
    const later = screen.getByText("הודעה מאוחרת");

    expect(title.compareDocumentPosition(later) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("shows a reloaded answer in place of the card's buttons, with no bubble", async () => {
    const answered = cardMessage();
    setup({
      messages: [answered, answer(card(), "אישור")],
      snapshot: snapshot(null),
    });

    expect(await screen.findByText("✓ אישור")).toBeInTheDocument();
    expect(document.querySelectorAll(".msg.user")).toHaveLength(0);
  });

  it("answers a live choice in place at once", async () => {
    const { api } = setup({ messages: [cardMessage()], snapshot: snapshot(card()) });
    (api.postTurn as Mock).mockReturnValue(new Promise(() => {}));

    await userEvent.click(await screen.findByRole("button", { name: "אישור" }));

    expect(await screen.findByText("✓ אישור")).toBeInTheDocument();
    expect(document.querySelectorAll(".msg.user")).toHaveLength(0);
  });

  it("shows the thinking line while a turn is in flight, and drops it with the reply", async () => {
    const { api } = setup({ messages: [], snapshot: snapshot(null) });
    let resolve!: (r: TurnResult) => void;
    (api.postTurn as Mock).mockReturnValue(new Promise((r) => { resolve = r; }));

    await userEvent.type(await screen.findByRole("textbox"), "שלום{Enter}");

    expect(await screen.findByText(texts.thinking)).toBeInTheDocument();
    (api.getChat as Mock).mockResolvedValue({
      messages: [message("user", "שלום"), message("assistant", "הבנתי")], snapshot: snapshot(null),
    });
    resolve({
      replies: [{ text: "הבנתי", card: null, image_key: null }], snapshot: snapshot(null),
      image_key: null,
    });
    await waitFor(() => expect(screen.queryByText(texts.thinking)).not.toBeInTheDocument());
  });

  it("shows the thinking line while a photo is still being read, with no card shown", async () => {
    setup({ messages: [], snapshot: snapshot(null, 1) });

    expect(await screen.findByText(texts.thinking)).toBeInTheDocument();
    expect(screen.queryByText(texts.waiting(1))).not.toBeInTheDocument();
  });

  it("keeps line breaks of a multi-line text", async () => {
    setup({ messages: [message("assistant", "א\nב")], snapshot: snapshot(null) });

    const p = await screen.findByText(/א/);

    expect(p).toHaveClass("text");
  });

  it("shows the history in order, the roles styled apart, then the snapshot card", async () => {
    setup({ messages: history, snapshot: snapshot(card(), 2) });

    const user = await screen.findByText("שלום");
    const bot = screen.getByText("היי, מה נעשה?");
    expect(user.closest("li")).toHaveClass("msg", "user");
    expect(bot.closest("li")).toHaveClass("msg", "bot");
    const title = screen.getByText("קריאה חדשה");
    expect(bot.compareDocumentPosition(title) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByRole("button", { name: "אישור" })).toBeInTheDocument();
    expect(screen.getByText(texts.waiting(2))).toBeInTheDocument();
  });

  it("posts the shown card's choice, appends the replies and replaces the card", async () => {
    const { api } = setup({ messages: history, snapshot: snapshot(card()) });
    post(api, {
      replies: [{ text: "נשמר", card: null, image_key: null }],
      snapshot: snapshot(null),
      image_key: null,
    });

    (api.getChat as Mock).mockResolvedValue({
      messages: [...history, answer(card(), "אישור"), message("assistant", "נשמר")],
      snapshot: snapshot(null),
    });
    await userEvent.click(await screen.findByRole("button", { name: "אישור" }));

    expect(await screen.findByText("נשמר")).toBeInTheDocument();
    expect(api.postTurn).toHaveBeenCalledWith({
      choice: { pending: "p1", version: 1, option: "confirm" },
    });
    expect(screen.queryByText("קריאה חדשה")).toBeNull();
  });

  it("takes the snapshot of a 409 without an alert", async () => {
    const { api } = setup({ messages: history, snapshot: snapshot(card()) });
    const fresh = card({ pending: "p2", title: "קריאה אחרת", options: [] });
    (api.postTurn as Mock).mockRejectedValue(
      new ApiError(409, "409", "already_handled", snapshot(fresh)),
    );
    (api.getChat as Mock).mockResolvedValue({ messages: history, snapshot: snapshot(fresh) });

    await userEvent.click(await screen.findByRole("button", { name: "אישור" }));

    expect(await screen.findByText("קריאה אחרת")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByText(texts.turnFailed)).toBeNull();
  });

  it("appends the typed text at once, then the replies, and clears the draft", async () => {
    const { api } = setup({ messages: history, snapshot: snapshot(null) });
    let resolve!: (v: unknown) => void;
    (api.postTurn as Mock).mockReturnValue(new Promise((r) => (resolve = r)));

    (api.getChat as Mock).mockResolvedValue({
      messages: [...history, message("user", "קריאה 100"), message("assistant", "הבנתי")],
      snapshot: snapshot(null),
    });
    const box = await screen.findByPlaceholderText(texts.composerPlaceholder);
    await userEvent.type(box, "קריאה 100{Enter}");

    expect(api.postTurn).toHaveBeenCalledWith({ text: "קריאה 100" });
    expect((await screen.findByText("קריאה 100")).closest("li")).toHaveClass("user");
    expect(box).toHaveValue("");
    resolve({
      replies: [{ text: "הבנתי", card: null, image_key: null }], snapshot: snapshot(null),
      image_key: null,
    });
    expect(await screen.findByText("הבנתי")).toBeInTheDocument();
  });

  it("sends a typed reply while the card has a confirm option", async () => {
    const { api } = setup({ messages: history, snapshot: snapshot(card()) });

    const box = await screen.findByPlaceholderText(texts.composerPlaceholder);
    await userEvent.type(box, "קריאה 105");
    expect(screen.getByRole("button", { name: texts.send })).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: texts.send }));

    expect(api.postTurn).toHaveBeenCalledWith({ text: "קריאה 105" });
  });

  it("sends a typed reply with Enter while the card has a confirm option", async () => {
    const { api } = setup({ messages: history, snapshot: snapshot(card()) });

    const box = await screen.findByPlaceholderText(texts.composerPlaceholder);
    await userEvent.type(box, "קריאה 105{Enter}");

    expect(api.postTurn).toHaveBeenCalledWith({ text: "קריאה 105" });
  });

  it("shows the failure text and refetches after a failed turn", async () => {
    const { api } = setup({ messages: history, snapshot: snapshot(null) });
    (api.postTurn as Mock).mockRejectedValue(new ApiError(500, "500"));

    const box = await screen.findByPlaceholderText(texts.composerPlaceholder);
    await userEvent.type(box, "שלום{Enter}");

    expect(await screen.findByText(texts.turnFailed)).toBeInTheDocument();
    await waitFor(() => expect(api.getChat).toHaveBeenCalledTimes(2));
  });

  it("shows the date and time of user messages only", async () => {
    setup({ messages: history, snapshot: snapshot(null) });

    expect(await screen.findAllByText("29/10/2026 10:00")).toHaveLength(1);
    expect(screen.getByText("29/10/2026 10:00").closest("li")).toHaveTextContent("שלום");
  });

  it("offers a retry under a failed message, with no notice", async () => {
    const { api } = setup({ messages: history, snapshot: snapshot(null) });
    (api.postTurn as Mock).mockRejectedValue(new ApiError(503, "503", "message_failed"));
    (api.getChat as Mock).mockResolvedValue({
      messages: [...history, { ...message("user", "הודעה"), failed: true }],
      snapshot: snapshot(null),
    });

    const box = await screen.findByPlaceholderText(texts.composerPlaceholder);
    await userEvent.type(box, "הודעה{Enter}");

    expect(await screen.findByRole("button", { name: texts.retry })).toBeInTheDocument();
    expect(screen.getByText(texts.notHandled)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("retries a failed message in place and hides its button", async () => {
    const failed = { ...message("user", "הודעה"), failed: true };
    const { api } = setup({ messages: [failed], snapshot: snapshot(null) });
    (api.postTurn as Mock).mockReturnValue(new Promise(() => {}));

    await userEvent.click(await screen.findByRole("button", { name: texts.retry }));

    expect(api.postTurn).toHaveBeenCalledWith({ retry: failed.at });
    expect(screen.queryByRole("button", { name: texts.retry })).toBeNull();
    expect(screen.getAllByText("הודעה")).toHaveLength(1);
  });

  it("shows when a message was last retried beside its sent time", async () => {
    const retried = { ...message("user", "הודעה"), retried_at: "2026-10-30T09:14:02.5_ab12" };
    setup({ messages: [retried], snapshot: snapshot(null) });

    expect(await screen.findByText("29/10/2026 10:00")).toBeInTheDocument();
    expect(screen.getByText(texts.retried("09:14"))).toBeInTheDocument();
  });

  it("keeps a message that never reached the server, with a retry that resends it", async () => {
    const { api } = setup({ messages: history, snapshot: snapshot(null) });
    (api.postTurn as Mock).mockRejectedValueOnce(new TypeError("offline"));

    const box = await screen.findByPlaceholderText(texts.composerPlaceholder);
    await userEvent.type(box, "הודעה{Enter}");
    await waitFor(() => expect(api.getChat).toHaveBeenCalledTimes(2));
    (api.postTurn as Mock).mockReturnValue(new Promise(() => {}));
    await userEvent.click(await screen.findByRole("button", { name: texts.retry }));

    expect(api.postTurn).toHaveBeenLastCalledWith({ text: "הודעה" });
    expect(screen.getAllByText("הודעה")).toHaveLength(1);
  });

  it("shows a placeholder when a photo's link cannot be fetched", async () => {
    const photo = { ...message("user", ""), text: null, image_key: "k/1.jpg" };
    const { api } = setup({ messages: [photo], snapshot: snapshot(null) });
    (api.fileUrl as Mock).mockRejectedValue(new ApiError(500, "500"));

    expect(await screen.findByText(texts.photoUnavailable)).toBeInTheDocument();
  });

  it("opens a history photo's thumbnail through onOpen", async () => {
    const photo = { ...message("user", ""), text: null, image_key: "k/1.jpg" };
    const { api, onOpen } = setup({ messages: [photo], snapshot: snapshot(null) });
    (api.fileUrl as Mock).mockResolvedValue("https://files/1.jpg");

    const thumb = await screen.findByRole("img");
    expect(thumb).toHaveAttribute("src", "https://files/1.jpg");
    expect(api.fileUrl).toHaveBeenCalledWith("k/1.jpg");
    await userEvent.click(thumb);

    expect(onOpen).toHaveBeenCalledWith("https://files/1.jpg");
  });

  it("shows the rejection notice for a refused photo", async () => {
    const { api } = setup({ messages: history, snapshot: snapshot(null) });
    (api.postTurn as Mock).mockRejectedValue(new ApiError(400, "400", "image_too_large"));
    stubPhotoPipeline();

    await screen.findByPlaceholderText(texts.composerPlaceholder);
    await userEvent.upload(screen.getByTestId("photo-input"), jpeg("a"));

    expect(await screen.findByText(texts.imageRejected)).toBeInTheDocument();
  });

  it("shows one live card after three photos each answered with a card", async () => {
    const cards = [1, 2, 3].map((n) => card({ pending: `p${n}`, title: `קריאה ${n}` }));
    const { api } = setup({ messages: [], snapshot: snapshot(null) });
    let n = 0;
    (api.postTurn as Mock).mockImplementation(async () => {
      const c = cards[n++];
      return {
        replies: [{ text: null, card: c, image_key: null }], snapshot: snapshot(c, 3 - n),
        image_key: `k${n}`,
      };
    });
    (api.getChat as Mock).mockResolvedValue({
      messages: cards.map((c) => cardMessage(c)), snapshot: snapshot(cards[2]),
    });
    stubPhotoPipeline();

    await screen.findByPlaceholderText(texts.composerPlaceholder);
    await userEvent.upload(screen.getByTestId("photo-input"), [jpeg("a"), jpeg("b"), jpeg("c")]);

    await waitFor(() => expect(screen.getAllByText(/^קריאה \d$/)).toHaveLength(3));
    expect(screen.getAllByRole("button", { name: "אישור" })).toHaveLength(1);
  });

  it("gives buttons only to the latest copy of a card the backend re-sent", async () => {
    setup({
      messages: [cardMessage(), message("assistant", "לא הבנתי"), cardMessage()],
      snapshot: snapshot(card()),
    });

    const [, latest] = await screen.findAllByText("קריאה חדשה");

    expect(screen.getAllByRole("button", { name: "אישור" })).toHaveLength(1);
    expect(latest.closest("li")).toContainElement(screen.getByRole("button", { name: "אישור" }));
  });

  it("scrolls the page to its very bottom, below the sticky composer, whenever the thread grows", async () => {
    const { grow } = installResizeObserver();
    const scrollTo = vi.spyOn(window, "scrollTo");
    setup({ messages: history, snapshot: snapshot(null) });
    await screen.findByText("היי, מה נעשה?");
    scrollTo.mockClear();

    act(grow);

    expect(scrollTo).toHaveBeenLastCalledWith({ top: document.documentElement.scrollHeight });
  });

  it("places the thread itself on a reload instead of the browser's remembered position", async () => {
    window.history.scrollRestoration = "auto";
    setup({ messages: [], snapshot: snapshot(null) });
    await screen.findByPlaceholderText(texts.composerPlaceholder);

    expect(window.history.scrollRestoration).toBe("manual");
  });

  it("keeps the live card's buttons enabled while a photo uploads", async () => {
    const { api } = setup({ messages: [], snapshot: snapshot(card()) });
    (api.postTurn as Mock).mockReturnValue(new Promise(() => {}));
    stubPhotoPipeline();

    await screen.findByPlaceholderText(texts.composerPlaceholder);
    await userEvent.upload(screen.getByTestId("photo-input"), jpeg("a"));

    await waitFor(() => expect(api.postTurn).toHaveBeenCalled());
    expect(screen.getByRole("button", { name: "אישור" })).toBeEnabled();
  });

  it("disables the live card's buttons while its choice is in flight", async () => {
    const { api } = setup({ messages: [], snapshot: snapshot(card()) });
    (api.postTurn as Mock).mockReturnValue(new Promise(() => {}));

    await userEvent.click(await screen.findByRole("button", { name: "דירה אחרת" }));

    expect(screen.getByRole("button", { name: "אישור" })).toBeDisabled();
  });
});


function post(api: Api, result: TurnResult) {
  (api.postTurn as Mock).mockResolvedValue(result);
}
