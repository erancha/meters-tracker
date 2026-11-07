/**
 * Chat state over the `["chat", day]` queries: the shown day's history and the snapshot from the
 * backend, and the turns that extend them. The backend flags the admin's chat; here that becomes
 * locked, and the composer takes no input.
 *
 * The owner may write while an earlier day is shown, so a correction is made beside the records
 * that day shows, but the server records every message under today. From the first turn sent on
 * an earlier day, the thread therefore also shows today's messages that came after the last one
 * today held at that moment; today's history is kept loaded for this.
 *
 * Every turn writes its outcome into the query cache: the user's own message (the typed text,
 * or the chosen option's label and its card, as the backend records it) is appended before the
 * request, the replies and the new snapshot on success, then the chat is refetched to reconcile.
 * A stale choice (409) only replaces the snapshot; any other failure refetches the chat, which
 * drops the local message unless the server stored it.
 *
 * A text or photo whose handling failed comes back from the server marked failed, and a retry
 * clears the mark in the cache at once, then posts the message's stamp, with no new message. One
 * whose request never reached the server is kept here, with a resend, for as long as the page
 * stays open.
 *
 * A turn lost to the network or a gateway timeout may still have been handled, so it only
 * refetches, with no failure notice; a photo's card then arrives from the server. A photo is
 * one turn each, all in flight together. Its local message carries an object URL; once the
 * server names the photo's stored key, that URL becomes the stored photo's link for the page's
 * life, and a photo that was never stored has its URL revoked. The refetch runs once the last
 * turn in flight settles, so it cannot drop the local messages of the others.
 *
 * A photo the model is still reading when the gateway gives up arrives later as a card, which
 * no response carries; so while no card is shown, items wait and no turn is in flight, the chat
 * is polled. With a card shown, the queued items include cards behind it, which only change after
 * the user answers.
 */
import { useEffect, useRef, useState } from "react";
import { useIsMutating, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { downscale } from "./images";
import { fileKey } from "./fileQuery";
import { ApiError, type Api } from "./api";
import { jerusalemStamp } from "./messageTime";
import { jerusalemDay } from "./messageTime";
import type {
  Card, CardOption, CardRef, ChatData, LocalMessage, TurnBody, TurnResult,
} from "./types";

// Every day's query starts with this, so one invalidation refetches all of them.
const ROOT = ["chat"];
const chatKey = (day: string) => [...ROOT, day];

type Chat = Omit<ChatData, "messages"> & { messages: LocalMessage[] };

const userMessage = (text: string | null, choice: CardRef | null = null): LocalMessage => ({
  at: jerusalemStamp(), role: "user", text, card: null, image_key: null, pdf_key: null,
  choice,
});

const replyMessages = (result: TurnResult): LocalMessage[] =>
  result.replies.map((reply) => ({
    at: jerusalemStamp(), role: "assistant", pdf_key: null, choice: null, ...reply,
  }));

/** The request itself failed to reach the backend (fetch rejected); only postTurn raises it. */
class LostRequest extends Error {}

// A request that never got an answer (offline, dropped) or a gateway that gave up waiting.
const lostInTransit = (err: unknown) =>
  err instanceof LostRequest || (err instanceof ApiError && GATEWAY_STATUSES.includes(err.status));

const isRejectedImage = (err: unknown) =>
  err instanceof ApiError && err.status === 400 && err.serverError !== null &&
    REJECTED_IMAGE_ERRORS.includes(err.serverError);

const isSameLocal = (a: LocalMessage, b: LocalMessage) =>
  a.at === b.at && a.text === b.text && a.localUrl === b.localUrl;

const GATEWAY_STATUSES = [502, 503, 504];
const REJECTED_IMAGE_ERRORS = ["image_too_large", "unsupported_image_type"];
const POLL_MS = 5000;

type Problem = "failed" | "rejected" | null;

interface TurnVars {
  body: () => Promise<TurnBody>;
  /** The day shown when the turn was sent, whose history holds its local messages. */
  day: string;
  local: LocalMessage;
  /** The turn retries the stored message local stands for, so it adds no message. */
  retrying?: boolean;
}

/** The chat query of day, for views that only read the history; only useChat polls it. */
export function useChatQuery(api: Api, day: string, poll = false) {
  return useQuery<Chat>({
    queryKey: chatKey(day),
    queryFn: () => api.getChat(day),
    // The interval is first asked for before the first load, while there is no data.
    refetchInterval: ({ state: { data } }) =>
      poll && data !== undefined && data.snapshot.card === null && data.snapshot.queued.length > 0
        ? POLL_MS : false,
  });
}

/** The chat of day, a stamp's date part; today unless the owner moved to an earlier day. */
export function useChat(api: Api, day: string) {
  const client = useQueryClient();
  const pending = useIsMutating() > 0;
  const choosing = useIsMutating({
    predicate: (m) => (m.state.variables as TurnVars).local.choice !== null,
  }) > 0;
  const today = jerusalemDay();
  const chat = useChatQuery(api, day, !pending);
  const todayChat = useChatQuery(api, today, !pending && day !== today);
  // The earlier day the owner sent from, and the stamp of today's last message at that moment.
  const [sentFrom, setSentFrom] = useState<{ day: string; after: string } | null>(null);

  const [problem, setProblem] = useState<Problem>(null);
  const [unsent, setUnsent] = useState<TurnVars[]>([]);
  const objectUrls = useRef(new Set<string>());
  // Decodes run one at a time so several full-size photos are never in memory together.
  const decoding = useRef<Promise<unknown>>(Promise.resolve());

  const reconcile = async () => {
    // The settling turn still counts as in flight here.
    if (client.isMutating() <= 1) await client.invalidateQueries({ queryKey: ROOT });
  };

  const turn = useMutation({
    mutationFn: async (vars: TurnVars) => {
      const body = await vars.body();
      try {
        return await api.postTurn(body);
      } catch (err) {
        if (err instanceof TypeError) throw new LostRequest(err.message, { cause: err });
        // A stale choice must carry the current snapshot; without it there is nothing to show.
        if (err instanceof ApiError && err.status === 409 && err.snapshot === null) {
          throw new Error(`409 without a snapshot: ${err.message}`);
        }
        throw err;
      }
    },
    onMutate: async ({ day: sentDay, local, retrying }) => {
      setProblem(null);
      if (sentDay !== today && sentFrom?.day !== sentDay) {
        // An earlier day renders only once today's history has loaded, so it is here.
        const held = todayChat.data!.messages;
        setSentFrom({ day: sentDay, after: held.length === 0 ? "" : held[held.length - 1].at });
      }
      await client.cancelQueries({ queryKey: ROOT });
      client.setQueryData<Chat>(chatKey(sentDay), (old) => ({
        ...old!,
        messages: retrying
          ? old!.messages.map((m) => (m.at === local.at ? local : m))
          : [...old!.messages, local],
      }));
    },
    // A refetch that landed mid-turn is cancelled before the replies are written, then the
    // server's own history is fetched to reconcile the local writes.
    onSuccess: async (result, vars) => {
      const sent = vars.local.localUrl;
      if (sent !== undefined) {
        if (result.image_key === null) throw new Error("a photo turn came back without its key");
        // The stored photo shows this page's own copy, so it needs no download and never blinks.
        client.setQueryData(fileKey(result.image_key), sent);
        objectUrls.current.delete(sent);
      }
      await client.cancelQueries({ queryKey: ROOT });
      client.setQueryData<Chat>(chatKey(vars.day), (old) => ({
        ...old!,
        messages: [...old!.messages, ...replyMessages(result)],
        snapshot: result.snapshot,
      }));
      await reconcile();
    },
    onError: (err, vars) => {
      if (err instanceof ApiError && err.status === 409) {
        client.setQueryData<Chat>(chatKey(vars.day), (old) => ({ ...old!, snapshot: err.snapshot! }));
      } else if (err instanceof ApiError && err.serverError === "message_failed") {
        // Checked before lostInTransit: unlike a gateway's own 503, this one names its error.
        // The refetch below brings the stored message with its failed mark.
      } else if (err instanceof LostRequest && vars.local.choice === null && !vars.retrying) {
        client.setQueryData<Chat>(chatKey(vars.day), (old) => ({
          ...old!,
          messages: old!.messages.filter((m) => !isSameLocal(m, vars.local)),
        }));
        setUnsent((held) => [...held, vars]);
      } else if (lostInTransit(err)) {
        // Nothing to report: the refetch below shows whatever the server did with the turn.
      } else if (vars.local.localUrl !== undefined && isRejectedImage(err)) {
        setProblem("rejected");
      } else {
        setProblem("failed");
      }
      void reconcile();
    },
  });

  const resend = (vars: TurnVars) => {
    setUnsent((held) => held.filter((v) => v !== vars));
    turn.mutate(vars);
  };
  const sentToday = sentFrom !== null && sentFrom.day === day && day !== today
    ? todayChat.data!.messages.filter((m) => m.at > sentFrom.after)
    : [];
  const messages = [
    ...(chat.data?.messages ?? []),
    ...sentToday,
    ...unsent.filter((vars) => vars.day === day)
      .map((vars) => ({ ...vars.local, resend: () => resend(vars) })),
  ];

  // Local photo messages leave the history when the server's history replaces them.
  useEffect(() => {
    const kept = new Set(messages.map((m) => m.localUrl));
    for (const url of objectUrls.current) {
      if (!kept.has(url)) {
        URL.revokeObjectURL(url);
        objectUrls.current.delete(url);
      }
    }
  }, [chat.data, todayChat.data, unsent]);

  function sendPhoto(file: File) {
    const url = URL.createObjectURL(file);
    objectUrls.current.add(url);
    turn.mutate({
      body: async () => {
        const scaled = decoding.current.then(() => downscale(file));
        decoding.current = scaled.catch(() => {});
        const { base64, mediaType } = await scaled;
        return { image: base64, media_type: mediaType };
      },
      day,
      local: { ...userMessage(null), localUrl: url },
    });
  }

  return {
    pending,
    loadFailed: chat.isError || todayChat.isError,
    messages,
    // Undefined until today's history has loaded too, which a turn on an earlier day reads.
    snapshot: todayChat.data === undefined ? undefined : chat.data?.snapshot,
    locked: chat.data?.admin,
    sendText: (text: string) =>
      turn.mutate({ body: async () => ({ text }), day, local: userMessage(text) }),
    sendPhotos: (files: File[]) => files.forEach(sendPhoto),
    choose: (card: Card, option: CardOption) => {
      const answered = { pending: card.pending, version: card.version };
      turn.mutate({
        body: async () => ({ choice: { ...answered, option: option.id } }),
        day,
        local: userMessage(option.label, answered),
      });
    },
    retry: (message: LocalMessage) => {
      if (message.resend !== undefined) {
        message.resend();
        return;
      }
      turn.mutate({
        body: async () => ({ retry: message.at }),
        day,
        local: { ...message, failed: false, retried_at: jerusalemStamp() },
        retrying: true,
      });
    },
    choosing,
    failed: problem === "failed",
    rejected: problem === "rejected",
  };
}

