/**
 * Signed-out screen: one card holding the app name, a condensed pitch, a "יותר" toggle that swaps
 * in the full summary, and the Google sign-in button; the demo, WhatsApp invite and source links
 * sit on the page below it.
 */
import { useState } from "react";
import { texts } from "../texts";
import { Icon } from "./Icon";

// The animated walkthrough, published beside the app by deploy.sh --frontend.
const DEMO_PATH = "demo.html";
const SOURCE_URL = "https://github.com/erancha/meters-tracker";

/** Renders a text whose **phrases** are bold. */
function Rich({ text }: { text: string }) {
  return <>{text.split("**").map((part, i) => (i % 2 ? <strong key={i}>{part}</strong> : part))}</>;
}

export function Landing({ onSignIn }: { onSignIn: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const invite = encodeURIComponent(texts.inviteMessage(window.location.origin));
  return (
    <main className={expanded ? "landing" : "landing landing-brief"}>
      <div className="landing-card">
        {/* Outside the heading, so the heading's accessible name stays the app's name. */}
        <div className="landing-heading">
          <h1 className="brand"><Icon name="gauge" />{texts.appTitle}</h1>
          <p className="landing-version">
            {texts.appVersion(__APP_VERSION__, __APP_COMMIT_DATE__)}
          </p>
        </div>
        {expanded ? (
          <>
            <p className="landing-intro">{texts.landingIntro}</p>
            <ul className="landing-summary">
              {texts.landingSummary.map((item) => <li key={item}>{item}</li>)}
            </ul>
          </>
        ) : (
          texts.landingCondensed.map((text) => (
            <p key={text} className="landing-condensed"><Rich text={text} /></p>
          ))
        )}
        <button type="button" className="disclosure more-toggle" aria-expanded={expanded}
                onClick={() => setExpanded(!expanded)}>
          {expanded ? texts.less : texts.more}
        </button>
        <button type="button" className="sign-in" onClick={onSignIn}>
          <span className="sign-in-mark" aria-hidden="true">G</span>{texts.signIn}
        </button>
      </div>
      {/* The hint sits outside the anchor, so the link's accessible name stays the page's own. */}
      <p className="landing-demo">
        <a href={DEMO_PATH} target="_blank" rel="noreferrer">{texts.demo}</a>{" "}
        <span className="landing-demo-hint">{texts.demoHint}</span>
      </p>
      <p className="landing-link">
        <a href={`https://wa.me/?text=${invite}`} target="_blank" rel="noreferrer">
          {texts.invite}
        </a>
      </p>
      <p className="landing-link">
        <a href={SOURCE_URL} target="_blank" rel="noreferrer">{texts.source}</a>
      </p>
    </main>
  );
}
