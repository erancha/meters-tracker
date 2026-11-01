/**
 * Chat state over the `["chat"]` query: the history and the snapshot from the backend, and the
 * turns that extend them.
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
 * is polled. With a card shown, the waiting count includes cards queued behind it, which only
 * change after the user answers.
 */
import { useEffect, useRef, useState } from "react";
import { useIsMutating, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { downscale } from "./images";
import { fileKey } from "./fileQuery";
import { ApiError, type Api } from "./api";
import { jerusalemStamp } from "./messageTime";
import type {
  Card, CardOption, CardRef, LocalMessage, Snapshot, TurnBody, TurnResult,
} from "./types";

const KEY = ["chat"];

interface Chat {
  messages: LocalMessage[];
  snapshot: Snapshot;
}

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
  local: LocalMessage;
  /** The turn retries the stored message local stands for, so it adds no message. */
  retrying?: boolean;
}

/** The chat query itself, for views that only read the history; only useChat polls it. */
export function useChatQuery(api: Api, poll = false) {
  return useQuery<Chat>({
    queryKey: KEY,
    queryFn: () => api.getChat(),
    // The interval is first asked for before the first load, while there is no data.
    refetchInterval: ({ state: { data } }) =>
      poll && data !== undefined && data.snapshot.card === null && data.snapshot.waiting > 0
        ? POLL_MS : false,
  });
}

export function useChat(api: Api) {
  const client = useQueryClient();
  const pending = useIsMutating() > 0;
  const choosing = useIsMutating({
    predicate: (m) => (m.state.variables as TurnVars).local.choice !== null,
  }) > 0;
  const chat = useChatQuery(api, !pending);

  const [problem, setProblem] = useState<Problem>(null);
  const [unsent, setUnsent] = useState<TurnVars[]>([]);
  const objectUrls = useRef(new Set<string>());
  // Decodes run one at a time so several full-size photos are never in memory together.
  const decoding = useRef<Promise<unknown>>(Promise.resolve());

  const reconcile = async () => {
    // The settling turn still counts as in flight here.
    if (client.isMutating() <= 1) await client.invalidateQueries({ queryKey: KEY });
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
    onMutate: async ({ local, retrying }) => {
      setProblem(null);
      await client.cancelQueries({ queryKey: KEY });
      client.setQueryData<Chat>(KEY, (old) => ({
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
      await client.cancelQueries({ queryKey: KEY });
      client.setQueryData<Chat>(KEY, (old) => ({
        messages: [...old!.messages, ...replyMessages(result)],
        snapshot: result.snapshot,
      }));
      await reconcile();
    },
    onError: (err, vars) => {
      if (err instanceof ApiError && err.status === 409) {
        client.setQueryData<Chat>(KEY, (old) => ({ ...old!, snapshot: err.snapshot! }));
      } else if (err instanceof ApiError && err.serverError === "message_failed") {
        // Checked before lostInTransit: unlike a gateway's own 503, this one names its error.
        // The refetch below brings the stored message with its failed mark.
      } else if (err instanceof LostRequest && vars.local.choice === null && !vars.retrying) {
        client.setQueryData<Chat>(KEY, (old) => ({
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
  const messages = [
    ...(chat.data?.messages ?? []),
    ...unsent.map((vars) => ({ ...vars.local, resend: () => resend(vars) })),
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
  }, [chat.data, unsent]);

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
      local: { ...userMessage(null), localUrl: url },
    });
  }

  return {
    pending,
    loadFailed: chat.isError,
    messages,
    snapshot: chat.data?.snapshot,
    sendText: (text: string) =>
      turn.mutate({ body: async () => ({ text }), local: userMessage(text) }),
    sendPhotos: (files: File[]) => files.forEach(sendPhoto),
    choose: (card: Card, option: CardOption) => {
      const answered = { pending: card.pending, version: card.version };
      turn.mutate({
        body: async () => ({ choice: { ...answered, option: option.id } }),
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
        local: { ...message, failed: false, retried_at: jerusalemStamp() },
        retrying: true,
      });
    },
    choosing,
    failed: problem === "failed",
    rejected: problem === "rejected",
  };
}

