// Entry point: resolves the session before mounting — a signed-in user gets the app, a signed-out
// visitor the landing page, and a sign-in that did not complete its reason with a way to retry.

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createApi } from "./api";
import { AuthError, claims, ensureSignedIn, redirectToLogin, signOut, watchSession } from "./auth";
import { getConfig } from "./config";
import { App } from "./components/App";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Landing } from "./components/Landing";
import { SignInError } from "./components/SignInError";
import "./style.css";

const cfg = getConfig();
const root = createRoot(document.getElementById("root")!);

try {
  const tokens = await ensureSignedIn(cfg);

  if (tokens === null) {
    root.render(
      <StrictMode>
        <ErrorBoundary>
          <Landing onSignIn={() => redirectToLogin(cfg)} />
        </ErrorBoundary>
      </StrictMode>,
    );
  } else {
    watchSession(cfg, tokens);
    root.render(
      <StrictMode>
        <ErrorBoundary>
          <QueryClientProvider client={new QueryClient()}>
            <App email={claims(tokens.id_token).email} api={createApi(cfg, tokens)}
                 onSignOut={() => signOut(cfg)} />
          </QueryClientProvider>
        </ErrorBoundary>
      </StrictMode>,
    );
  }
} catch (err) {
  if (!(err instanceof AuthError)) throw err;
  root.render(
    <SignInError reason={err.message} adminEmail={cfg.adminEmail}
                 onSignIn={() => redirectToLogin(cfg)} />,
  );
}
