/**
 * The signed-out screen, laid out like every Google sign-in landing page of this author's apps
 * (the shared styles/landing.css): one card holding the tab's icon, the app name, a tagline and
 * the running build, a short pitch and three numbered steps, a "יותר" toggle that adds the
 * details, and the Google sign-in button with a toggle under it that explains why signing in with
 * Google is safe; under the card, links to the demo, a WhatsApp invite and the source.
 */
import { useEffect, useRef, useState } from "react";
import { whatsAppInviteUrl } from "../invite";
import { texts } from "../texts";

// The animated walkthrough, published beside the app by deploy.sh --frontend.
const DEMO_PATH = "demo.html";
const SOURCE_URL = "https://github.com/erancha/meters-tracker";
// Google's help page "How Sign in with Google helps you share data safely". It carries no
// language parameter, so Google answers in the visitor's own language.
const GOOGLE_HELP_URL = "https://support.google.com/accounts/answer/12921417";

/** Renders a text whose **phrases** are bold. */
function Rich({ text }: { text: string }) {
  return <>{text.split("**").map((part, i) => (i % 2 ? <strong key={i}>{part}</strong> : part))}</>;
}

/** An emoji before a link's words, kept out of the link's accessible name and its underline. */
function LinkMark({ mark }: { mark: string }) {
  return <span className="link-mark" aria-hidden="true">{mark}</span>;
}

/**
 * The toggle under the sign-in button and the text it opens in place, ending with a link to
 * Google's own account of signing in. A close button at the end of the toggle's line closes it
 * too, and the link marks that it opens a new tab, so it is not taken for the way out.
 */
function SignInReasons({ open, onOpenChange }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const toggle = useRef<HTMLButtonElement>(null);
  const text = useRef<HTMLDivElement>(null);
  // Opening scrolls down to the explanation's last line, which on a phone opens below the fold.
  useEffect(() => {
    if (open) text.current!.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [open]);
  return (
    <>
      <div className="sign-in-why-row">
        <button ref={toggle} type="button" className="sign-in-why" aria-expanded={open}
                onClick={() => onOpenChange(!open)}>
          {texts.signInWhy}
        </button>
        {open && (
          <button type="button" className="sign-in-why-close" aria-label={texts.signInWhyClose}
                  onClick={() => {
                    onOpenChange(false);
                    // The close button leaves with the text, so focus returns to what reopens it.
                    toggle.current?.focus();
                  }}>
            ✕
          </button>
        )}
      </div>
      {open && (
        <div ref={text} className="sign-in-why-text">
          <p><Rich text={texts.signInWhyLead} /></p>
          <ul className="landing-details">
            {texts.signInWhyPoints.map((point) => <li key={point}><Rich text={point} /></li>)}
          </ul>
          <a href={GOOGLE_HELP_URL} target="_blank" rel="noreferrer"
             aria-label={`${texts.signInWhyLink} ${texts.newTab}`}>
            {texts.signInWhyLink} <span aria-hidden="true">↗</span>
          </a>
        </div>
      )}
    </>
  );
}

export function Landing({ onSignIn }: { onSignIn: () => void }) {
  const [expanded, setExpanded] = useState(false);
  // Opening the sign-in explanation folds the details back, so one subject is open at a time.
  const foldOnExplain = (event: { target: EventTarget }) => {
    const toggle = (event.target as Element).closest(".sign-in-why");
    if (toggle?.getAttribute("aria-expanded") === "false") setExpanded(false);
  };
  const [explained, setExplained] = useState(false);
  return (
    <main className="landing">
      <div className="landing-card" onClick={foldOnExplain}>
        <h1 className="app-heading">
          {/* The same file as the tab's icon, so the two cannot drift apart; decorative. */}
          <img className="app-icon" src="favicon.svg" alt="" /> {texts.appTitle}
        </h1>
        {/* Siblings of the heading, so the heading's accessible name stays the bare title. */}
        <p className="app-tagline">{texts.appTagline}</p>
        <p className="app-version">{texts.appVersion(__APP_VERSION__, __APP_COMMIT_DATE__)}</p>
        {texts.landingPitch.map((line) => (
          <p key={line} className="landing-pitch"><Rich text={line} /></p>
        ))}
        <ol className="landing-steps">
          {texts.landingSteps.map((step) => (
            <li key={step.title}>
              <span><strong>{step.title}</strong>: {step.text}</span>
            </li>
          ))}
        </ol>
        {expanded && (
          <ul className="landing-details">
            {texts.landingDetails.map((detail) => <li key={detail}><Rich text={detail} /></li>)}
          </ul>
        )}
        <button type="button" className="landing-more" aria-expanded={expanded}
                onClick={() => setExpanded(!expanded)}>
          {expanded ? texts.less : texts.more}
        </button>
        {/* While the explanation is open, the sign-in area is framed as one panel, so the text
            about Google reads apart from the pitch about the app above it. */}
        <div className={explained ? "sign-in sign-in-open" : "sign-in"}>
          <button type="button" className="sign-in-button" onClick={onSignIn}>
            <span className="sign-in-mark" aria-hidden="true">G</span>{texts.signIn}
          </button>
          <SignInReasons open={explained} onOpenChange={setExplained} />
        </div>
      </div>
      <footer className="landing-links">
        {/* The demo is this site's own page: its tab keeps the tie to this one. */}
        <a href={DEMO_PATH} target="_blank" rel="opener" title={texts.demoHint}>
          <LinkMark mark={texts.demoMark} />{texts.demo}
        </a>
        <a href={whatsAppInviteUrl()} target="_blank" rel="noreferrer">
          <LinkMark mark={texts.inviteMark} />{texts.invite}
        </a>
        <a href={SOURCE_URL} target="_blank" rel="noreferrer">{texts.source}</a>
      </footer>
    </main>
  );
}
