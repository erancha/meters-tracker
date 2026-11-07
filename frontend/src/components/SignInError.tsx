/**
 * Screen for a sign-in that did not complete: the reason in an alert, and side by side a button
 * that starts sign-in again, so the person can pick another Google account, and a button that
 * copies the admin's address to the clipboard, so a refused person can write to ask for access.
 * It reuses the landing page's card and sign-in button styles.
 */
import { useState } from "react";
import { texts } from "../texts";

export function SignInError({ reason, adminEmail, onSignIn }: {
  reason: string;
  adminEmail: string;
  onSignIn: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const copyAdminEmail = async () => {
    await navigator.clipboard.writeText(adminEmail);
    setCopied(true);
  };
  return (
    <main className="landing">
      <div className="landing-card">
        <div className="alert" role="alert">{reason}</div>
        <div className="sign-in-error-actions">
          <button type="button" className="sign-in" onClick={onSignIn}>
            <span className="sign-in-mark" aria-hidden="true">G</span>{texts.signInAnotherAccount}
          </button>
          <button type="button" className="sign-in" onClick={copyAdminEmail}>
            {texts.copyAdminEmail}
          </button>
        </div>
        {copied && <p className="sign-in-error-copied" role="status">
          {texts.adminEmailCopied(adminEmail)}
        </p>}
      </div>
    </main>
  );
}
