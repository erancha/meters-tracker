import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthError, ensureSignedIn, logoutUrl, redirectToLogin, signOut, watchSession } from "./auth";
import type { AppConfig } from "./config";
import { texts } from "./texts";

const cfg: AppConfig = {
  cognitoDomain: "https://auth.example.com",
  clientId: "client123",
  apiUrl: "https://api.example.com",
  redirectUri: "https://app.example.com/",
};

afterEach(() => vi.unstubAllGlobals());

describe("logoutUrl", () => {
  it("points at the Cognito logout endpoint with the client id and encoded return address", () => {
    expect(logoutUrl(cfg)).toBe(
      "https://auth.example.com/logout?client_id=client123&logout_uri=https%3A%2F%2Fapp.example.com%2F",
    );
  });
});

describe("ensureSignedIn", () => {
  async function rejectionFor(search: string): Promise<AuthError> {
    sessionStorage.removeItem("tokens");
    history.replaceState(null, "", search);

    const err = await ensureSignedIn(cfg).then(
      () => { throw new Error("resolved instead of rejecting"); },
      (e: unknown) => e,
    );
    expect(err).toBeInstanceOf(AuthError);
    return err as AuthError;
  }

  it("raises AuthError with the raw description for errors it does not recognize", async () => {
    const err = await rejectionFor("/?error=server_error&error_description=Something+went+wrong");

    expect(err.message).toBe("Something went wrong");
  });

  it("shows a refusal from the sign-up Lambda as its own message, without Cognito's wrapper", async () => {
    const reason = "הכתובת a@x.com אינה ברשימת הכתובות המורשות להירשם. לקבלת גישה פנו למנהל האתר.";
    const description = encodeURIComponent(`PreSignUp failed with error ${reason}.`);
    const err = await rejectionFor(`/?error=invalid_request&error_description=${description}`);

    expect(err.message).toBe(reason);
  });

  it("unwraps the refusal when Cognito ends it with a trailing space", async () => {
    const reason = "הכתובת a@x.com אינה ברשימת הכתובות המורשות להירשם.";
    const description = encodeURIComponent(`PreSignUp failed with error ${reason}. `);
    const err = await rejectionFor(`/?error=invalid_request&error_description=${description}`);

    expect(err.message).toBe(reason);
  });

  it("resolves to null when there is no session, no auth code, and no error redirect", async () => {
    sessionStorage.removeItem("tokens");
    history.replaceState(null, "", "/");

    await expect(ensureSignedIn(cfg)).resolves.toBeNull();
  });

  it("raises AuthError and drops the spent code from the address bar when the exchange fails", async () => {
    sessionStorage.removeItem("tokens");
    sessionStorage.setItem("pkce_verifier", "verifier");
    history.replaceState(null, "", "/?code=abc123");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("", { status: 400 })));

    const err = await ensureSignedIn({ ...cfg, redirectUri: `${location.origin}/` }).then(
      () => { throw new Error("resolved instead of rejecting"); },
      (e: unknown) => e,
    );

    expect(err).toBeInstanceOf(AuthError);
    expect((err as AuthError).message).toBe(
      `${texts.signInIncomplete} (token exchange failed: 400)`,
    );
    expect(location.search).toBe("");
  });
});

describe("redirectToLogin", () => {
  it("navigates once for concurrent expiries, with the challenge bound to the stored verifier", async () => {
    const navigate = vi.fn();

    await Promise.all([redirectToLogin(cfg, navigate), redirectToLogin(cfg, navigate)]);

    expect(navigate).toHaveBeenCalledOnce();
    const digest = await crypto.subtle.digest(
      "SHA-256", new TextEncoder().encode(sessionStorage.getItem("pkce_verifier")!),
    );
    const expected = btoa(String.fromCharCode(...new Uint8Array(digest)))
      .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    expect(new URL(navigate.mock.calls[0][0]).searchParams.get("code_challenge")).toBe(expected);
  });
});

describe("signOut", () => {
  it("drops the stored tokens and navigates to the Cognito logout endpoint", () => {
    sessionStorage.setItem("tokens", JSON.stringify({ id_token: "t", expires_at: 1 }));
    const navigate = vi.fn();

    signOut(cfg, navigate);

    expect(sessionStorage.getItem("tokens")).toBeNull();
    expect(navigate).toHaveBeenCalledWith(logoutUrl(cfg));
  });
});

describe("watchSession", () => {
  const expired = { id_token: "t", expires_at: Date.now() - 1_000 };
  const live = { id_token: "t", expires_at: Date.now() + 3_600_000 };

  beforeEach(() => sessionStorage.setItem("tokens", JSON.stringify(live)));
  afterEach(() => { delete (document as { visibilityState?: unknown }).visibilityState; });

  const hide = () =>
    Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });

  it("returns a tab that comes back to the foreground holding an expired token to the landing page", () => {
    const navigate = vi.fn();
    watchSession(cfg, expired, navigate);

    document.dispatchEvent(new Event("visibilitychange"));

    expect(navigate).toHaveBeenCalledExactlyOnceWith(cfg.redirectUri);
    expect(sessionStorage.getItem("tokens")).toBeNull();
  });

  it("leaves a token still within its lifetime alone", () => {
    const navigate = vi.fn();
    watchSession(cfg, live, navigate);

    document.dispatchEvent(new Event("visibilitychange"));

    expect(navigate).not.toHaveBeenCalled();
  });

  it("ignores the transition that hides the tab", () => {
    const navigate = vi.fn();
    watchSession(cfg, expired, navigate);
    hide();

    document.dispatchEvent(new Event("visibilitychange"));

    expect(navigate).not.toHaveBeenCalled();
  });
});
