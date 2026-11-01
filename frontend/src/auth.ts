// Google sign-in and sign-out through the Cognito Hosted UI using the authorization-code + PKCE
// flow. No client secret exists in the browser; the verifier lives in sessionStorage only for the
// duration of the redirect round-trip.

import type { AppConfig } from "./config";
import { texts } from "./texts";

export interface Tokens {
  id_token: string;
  expires_at: number;
}

/** Sign-in did not complete — rejected by the Hosted UI, or a failed code exchange. User-facing. */
export class AuthError extends Error {}

const b64url = (buf: ArrayBuffer | Uint8Array) =>
  btoa(String.fromCharCode(...new Uint8Array(buf)))
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

const navigateTo = (url: string) => { location.href = url; };

// Returns the app to its canonical address, dropping the Hosted UI's one-time code: left in the
// address bar, a reload would re-submit a code Cognito has already spent.
const dropAuthCode = (cfg: AppConfig) => history.replaceState(null, "", cfg.redirectUri);

/**
 * Whether a token is worth sending. The minute of margin absorbs clock skew against Cognito, so a
 * token the API is about to reject counts as expired here rather than being sent and bounced.
 */
export const isUnexpired = (tokens: Tokens): boolean => tokens.expires_at > Date.now() + 60_000;

export function claims(idToken: string): { email: string } {
  const payload = idToken.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
  return JSON.parse(atob(payload));
}

/**
 * Resolves the current session: stored tokens, a fresh code exchange when returning from the
 * Hosted UI, or null when no sign-in is underway — the caller then shows the landing page and
 * starts the flow via redirectToLogin only on an explicit user action.
 */
export async function ensureSignedIn(cfg: AppConfig): Promise<Tokens | null> {
  const stored: Tokens | null = JSON.parse(sessionStorage.getItem("tokens") || "null");
  if (stored && isUnexpired(stored)) return stored;
  const params = new URLSearchParams(location.search);
  const code = params.get("code");
  if (code) return exchangeCode(cfg, code);
  // A rejected sign-in comes back as an OAuth error redirect; surface it verbatim for diagnosis
  // instead of treating it as signed-out, which would silently offer another doomed sign-in.
  const error = params.get("error");
  if (error) throw new AuthError(params.get("error_description") ?? error);
  return null;
}

export function logoutUrl(cfg: AppConfig): string {
  const params = new URLSearchParams({ client_id: cfg.clientId, logout_uri: cfg.redirectUri });
  return `${cfg.cognitoDomain}/logout?${params}`;
}

// Clearing local tokens alone is not enough: the Hosted UI session cookie would silently sign the
// same Google account back in on the next visit, so sign-out must also hit Cognito's /logout.
export function signOut(cfg: AppConfig, navigate: (url: string) => void = navigateTo): void {
  sessionStorage.removeItem("tokens");
  navigate(logoutUrl(cfg));
}

// The sign-in redirect this page load has already started, if any. A second tap on the sign-in
// button before the navigation commits would mint a second verifier, leaving that one stored while
// the committed navigation carries the first tap's challenge, a PKCE mismatch that fails the
// exchange. One redirect per page load keeps the pair consistent.
let redirect: Promise<void> | null = null;

export function redirectToLogin(cfg: AppConfig, navigate: (url: string) => void = navigateTo): Promise<void> {
  return (redirect ??= startLogin(cfg, navigate));
}

// Returns to the landing page instead of starting the Hosted UI round-trip here: Google's account
// chooser goes stale minutes after it renders and rejects a late pick with a 400, so the round-trip
// must start on the user's tap, not on a tab waking in the background.
export function endSession(cfg: AppConfig, navigate: (url: string) => void = navigateTo): void {
  sessionStorage.removeItem("tokens");
  navigate(cfg.redirectUri);
}

/**
 * Ends the session of a tab that returns to the foreground holding a token which expired while it
 * was away. Requests already carry their own 401 recovery; this closes the window before one is
 * made, so a user coming back to the app is not looking at a page whose every action is about to
 * bounce.
 */
export function watchSession(
  cfg: AppConfig, tokens: Tokens, navigate: (url: string) => void = navigateTo,
): void {
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && !isUnexpired(tokens)) endSession(cfg, navigate);
  });
}

async function startLogin(cfg: AppConfig, navigate: (url: string) => void): Promise<void> {
  const verifier = b64url(crypto.getRandomValues(new Uint8Array(32)));
  sessionStorage.setItem("pkce_verifier", verifier);
  const challenge = b64url(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier)));
  const params = new URLSearchParams({
    client_id: cfg.clientId,
    response_type: "code",
    scope: "openid email profile",
    redirect_uri: cfg.redirectUri,
    identity_provider: "Google",
    code_challenge_method: "S256",
    code_challenge: challenge,
  });
  navigate(`${cfg.cognitoDomain}/oauth2/authorize?${params}`);
}

async function exchangeCode(cfg: AppConfig, code: string): Promise<Tokens> {
  const response = await fetch(`${cfg.cognitoDomain}/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      client_id: cfg.clientId,
      code,
      redirect_uri: cfg.redirectUri,
      code_verifier: sessionStorage.getItem("pkce_verifier")!,
    }),
  });
  // The code is spent either way, so the address bar is cleaned before the outcome is reported.
  // A rejected exchange surfaces as an AuthError so the app renders the reason and the reload it
  // asks for lands on the landing page, rather than crashing the entry point into a blank screen
  // that re-submits the same dead code on every refresh.
  dropAuthCode(cfg);
  if (!response.ok) {
    throw new AuthError(
      `${texts.signInIncomplete} (token exchange failed: ${response.status})`,
    );
  }
  const data = await response.json();
  const tokens: Tokens = { id_token: data.id_token, expires_at: Date.now() + data.expires_in * 1000 };
  sessionStorage.setItem("tokens", JSON.stringify(tokens));
  return tokens;
}
