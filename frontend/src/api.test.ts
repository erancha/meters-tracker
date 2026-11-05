import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, createApi } from "./api";
import type { Tokens } from "./auth";
import type { AppConfig } from "./config";

const cfg: AppConfig = {
  cognitoDomain: "https://auth.example.com",
  clientId: "client123",
  apiUrl: "https://api.example.com",
  redirectUri: "https://app.example.com/",
};

const live: Tokens = { id_token: "token", expires_at: Date.now() + 3_600_000 };
const expired: Tokens = { id_token: "token", expires_at: 0 };
const snapshot = { card: null, waiting: 0 };

const stubFetch = (response: Response) => {
  const fetchMock = vi.fn().mockResolvedValue(response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
};

afterEach(() => vi.unstubAllGlobals());

describe("createApi", () => {
  it("sends the id token as a Bearer header on a GET /chat", async () => {
    const fetchMock = stubFetch(new Response(JSON.stringify({ messages: [], snapshot })));

    const chat = await createApi(cfg, live).getChat("2026-11-05");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://api.example.com/chat?day=2026-11-05");
    expect(init.method).toBe("GET");
    expect(init.headers.Authorization).toBe("Bearer token");
    expect(chat).toEqual({ messages: [], snapshot });
  });

  it("posts a turn body as JSON to /turn", async () => {
    const fetchMock = stubFetch(new Response(JSON.stringify({ replies: [], snapshot })));

    const result = await createApi(cfg, live).postTurn({ text: "שלום" });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://api.example.com/turn");
    expect(init.method).toBe("POST");
    expect(init.headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(init.body)).toEqual({ text: "שלום" });
    expect(result).toEqual({ replies: [], snapshot });
  });

  it("requests a file link under the key, each path segment percent-encoded, and returns its url",
     async () => {
    const fetchMock = stubFetch(new Response('{"url": "https://files.example.com/x"}'));

    const url = await createApi(cfg, live).fileUrl("a@x.com/images/2026-10-29T17:58:29_1.jpg");

    // API Gateway reads an encoded slash as a path separator, so the slashes stay literal.
    expect(fetchMock.mock.calls[0][0])
      .toBe("https://api.example.com/files/a%40x.com/images/2026-10-29T17%3A58%3A29_1.jpg");
    expect(url).toBe("https://files.example.com/x");
  });

  it("carries the snapshot of a 409 body on the ApiError", async () => {
    const stale = { card: { kind: "confirm", pending: "p", version: 2, title: "t", lines: [],
                            options: [], default: null }, waiting: 1 };
    stubFetch(new Response(JSON.stringify({ error: "already_handled", snapshot: stale }),
                           { status: 409 }));

    const error = await createApi(cfg, live)
      .postTurn({ choice: { pending: "p", version: 1, option: "confirm" } })
      .then(() => null, (e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(409);
    expect((error as ApiError).serverError).toBe("already_handled");
    expect((error as ApiError).snapshot).toEqual(stale);
  });

  it("leaves snapshot null on an error body without one", async () => {
    stubFetch(new Response('{"error": "invalid"}', { status: 400 }));

    const error = await createApi(cfg, live).postTurn({ text: "" }).then(() => null, (e) => e);

    expect((error as ApiError).snapshot).toBeNull();
    expect((error as ApiError).serverError).toBe("invalid");
  });

  it("triggers re-authentication and never settles on a 401 under an expired token", async () => {
    stubFetch(new Response("Unauthorized", { status: 401 }));
    const onExpired = vi.fn();

    const outcome = await Promise.race([
      createApi(cfg, expired, onExpired).getChat("2026-11-05").then(() => "settled", () => "settled"),
      new Promise((resolve) => setTimeout(() => resolve("pending"), 10)),
    ]);

    expect(outcome).toBe("pending");
    expect(onExpired).toHaveBeenCalledOnce();
  });

  it("surfaces a 401 under a token still within its lifetime instead of re-authenticating", async () => {
    stubFetch(new Response("Unauthorized", { status: 401 }));
    const onExpired = vi.fn();

    const error = await createApi(cfg, live, onExpired).getChat("2026-11-05").then(() => null, (e) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(401);
    expect(onExpired).not.toHaveBeenCalled();
  });
});
