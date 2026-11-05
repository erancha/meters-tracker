// Typed client for the authenticated backend API, bound to the signed-in user's tokens.

import { endSession, isUnexpired, type Tokens } from "./auth";
import type { AppConfig } from "./config";
import type { ChatData, Snapshot, TurnBody, TurnResult } from "./types";

/**
 * Backend request rejected; the message keeps the method, path, status, and body for diagnosis,
 * serverError the body's own `error` text or null when the body states none, and snapshot the
 * current state the body carries (a stale choice's 409) or null.
 */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    detail: string,
    readonly serverError: string | null = null,
    readonly snapshot: Snapshot | null = null,
  ) {
    super(detail);
  }
}

// The parsed JSON object of an error body; the gateway's own rejections and non-JSON bodies
// state nothing, so they read as an empty object.
function errorBody(body: string): { error?: unknown; snapshot?: Snapshot } {
  let parsed: unknown;
  try {
    parsed = JSON.parse(body);
  } catch {
    return {};
  }
  return typeof parsed === "object" && parsed !== null ? parsed : {};
}

export interface Api {
  /** The chat of day, a stamp's date part. */
  getChat(day: string): Promise<ChatData>;
  postTurn(body: TurnBody): Promise<TurnResult>;
  fileUrl(key: string): Promise<string>;
}

export function createApi(
  cfg: AppConfig,
  tokens: Tokens,
  onExpired: () => void = () => endSession(cfg),
): Api {
  async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const response = await fetch(cfg.apiUrl + path, {
      method,
      headers: {
        Authorization: `Bearer ${tokens.id_token}`,
        ...(body !== undefined && { "Content-Type": "application/json" }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    // The captured token has outlived its validity (the tab stayed open past expiry), so the
    // session ends and the landing page offers a fresh sign-in. The promise never settles: the
    // page is navigating away, and rejecting would flash an error alert during the redirect.
    //
    // A 401 on a token still inside its lifetime is a different fault: the API rejects a token
    // this client considers good, as when the stack is redeployed under a new user pool while an
    // older config.js is still cached. Signing in again would only produce another rejected
    // token, so that case falls through to the error below instead of cycling through the Hosted
    // UI forever.
    if (response.status === 401 && !isUnexpired(tokens)) {
      onExpired();
      return new Promise<T>(() => {});
    }
    if (!response.ok) {
      const text = await response.text();
      const parsed = errorBody(text);
      throw new ApiError(
        response.status,
        `${method} ${path} → ${response.status}: ${text}`,
        typeof parsed.error === "string" ? parsed.error : null,
        parsed.snapshot ?? null,
      );
    }
    return response.json();
  }
  return {
    getChat: (day) => request("GET", `/chat?day=${day}`),
    postTurn: (body) => request("POST", "/turn", body),
    // The key contains slashes, so it travels percent-encoded as one path segment. The link is
    // good for minutes only, which is why it is asked for at display time.
    // API Gateway reads an encoded slash as a path separator, so only each segment is encoded.
    fileUrl: async (key) => {
      const path = key.split("/").map(encodeURIComponent).join("/");
      return (await request<{ url: string }>("GET", `/files/${path}`)).url;
    },
  };
}
