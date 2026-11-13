# Sign-in

Owners and their delegates sign in to meters-tracker with a Google account, through a Cognito
user pool that the `meters-tracker-cognito` stack deploys; there is no other way in. This page
explains how sign-in works, how to set up the Google side for a new deployment and who may sign
in, behind [Architecture](architecture.md#the-entry-points). How a signed-in address maps to an
account is under Sign-in and accounts in
[Architecture's cross-cutting mechanisms](architecture.md#cross-cutting-mechanisms).

```mermaid
sequenceDiagram
    participant B as Browser app
    participant C as Cognito domain
    participant G as Google
    participant P as presignup Lambda
    participant A as HTTP API
    B->>C: /oauth2/authorize, identity_provider=Google, PKCE challenge
    C->>G: redirect to Google's account chooser
    G->>C: /oauth2/idpresponse with Google's code
    C->>G: redeem the code with the Google client secret
    C->>P: first sign-in only: may this address sign up?
    P-->>C: allowed, or an error that refuses the account
    C->>B: redirect to the app's address with Cognito's code
    B->>C: /oauth2/token with the code and the PKCE verifier
    C-->>B: ID token
    B->>A: request with the ID token as bearer
    A->>A: JWT authorizer checks issuer and audience
```

## How it works

1. **The app sends the browser to Cognito.** The sign-in button stores a random PKCE verifier
   in the tab's `sessionStorage` and redirects to `<cognitoDomain>/oauth2/authorize` with
   `identity_provider=Google`, so Cognito skips its own page and goes straight to Google. The
   redirect URI is the app's own origin with a trailing slash (`frontend/src/auth.ts`).
2. **Google answers Cognito, not the app.** Google returns the browser to Cognito's
   `/oauth2/idpresponse`, and Cognito redeems Google's code with the client secret the stack was
   deployed with. The secret never reaches the browser.
3. **The first sign-in creates the account.** Before Cognito creates a user, it calls the
   presignup Lambda, which refuses an address that does not match the allowed list (below). The
   refusal names the admin address to ask for access; Cognito returns it to the app as an error
   redirect, and the app shows it on the signed-out page.
4. **The app redeems Cognito's code for tokens.** Back on its own address with a one-time code,
   the app posts the code and the PKCE verifier to `/oauth2/token`, keeps the ID token in
   `sessionStorage` and drops the code from the address bar.
5. **Every API request carries the ID token.** The HTTP API's JWT authorizer accepts a token
   only when Cognito's pool issued it for this app's client. The api Lambda answers 403
   `email_unverified` unless Google marked the address verified, then maps the token's `email`
   to an account. The app keeps no refresh token: an expired token returns it to its signed-out
   landing page.
6. **Sign-out goes through Cognito.** It clears the stored token and opens
   `<cognitoDomain>/logout`, since Cognito's own session cookie would otherwise sign the same
   Google account straight back in.

The pool accepts no sign-up by password, and its app client allows no password flow, so Google
is the only way to hold a token. The Cognito domain, the app client id and the API address reach
the browser in `frontend/public/config.js`, which every deploy writes from the stacks' outputs
(`scripts/lib/app-config.sh`).

## Setting up Google

Do this once, before the first deploy, since the Cognito stack is deployed with the client's id and
secret. The steps start from no Google Cloud project.

1. **Create a project.** In the [Google Cloud console](https://console.cloud.google.com/), choose
   **New project** in the project picker, name it `meters-tracker` and select it.
2. **Set up the consent screen.** Open **Google Auth Platform** and click **Get started**. Give an
   app name and your email, choose **External**, and finish with **Create**.
3. **Add the test users.** Under **Audience → Test users**, add every Google account that will sign
   in: the admin, each owner and each delegate.
4. **Print the redirect URI.** Run `./scripts/aws-helper.sh --redirect-uri`. It needs only
   `scripts/lib/aws-config.sh` filled in.
5. **Create the client.** Under **Clients → Create client**, choose **Web application** and add the
   URI of step 4 under **Authorized redirect URIs**.
6. **Store the id and secret.** Copy them into `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in
   `scripts/lib/secrets.sh`.

A feature branch deploys its own Cognito domain: run step 4 on that branch and add its URI to the
same client. Google may take a few minutes to apply a new URI; until then, sign-in ends on a
`redirect_uri_mismatch` page.

Cognito alone talks to Google, so the client needs no other address. The app's own addresses,
`http://localhost:8000/` and the CloudFront address, are callback URLs of the Cognito app client,
which the deploy registers. To reuse a client you already have, add the URI of step 4 to it.

## Who may sign in

Two lists apply, and an address must pass both.

- **Google's audience.** While the app is in **Testing**, Google signs in only the accounts
  listed under **Audience → Test users**, up to 100, and shows any other account an "access
  blocked" page. **Publish app** under **Audience** lifts that list; with no scopes beyond
  `openid email profile`, Google publishes it without a verification review, and the allowed
  list below still decides who gets an account.
- **The allowed list.** `ALLOWED_EMAILS` in `scripts/lib/params.sh` is a regex a new account's
  address must match in full, ignoring case: join addresses with `|` and escape their dots
  (`a\.b@host\.com|c@host\.com`), or write `.*` to admit everyone. It must include the admin
  (`ADMIN_EMAIL`) and every delegate an owner adds in the chat. The presignup Lambda reads it on
  each new account, so it gates sign-up, not sign-in: an account already in the pool stays when
  the regex changes. A change takes a backend deploy, before the new address first signs in.
