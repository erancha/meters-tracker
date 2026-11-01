/**
 * The app header: the brand, and an account menu holding the signed-in email and sign-out. The
 * open menu floats over the page, so a press outside it and Escape are heard on the document.
 */
import { useEffect, useRef, useState } from "react";
import { texts } from "../texts";
import { Icon } from "./Icon";

export function Header({ email, onSignOut }: { email: string; onSignOut: () => void }) {
  const [open, setOpen] = useState(false);
  const account = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!open) return;
    const dismiss = (e: MouseEvent) => {
      if (!account.current!.contains(e.target as Node)) setOpen(false);
    };
    const onEscape = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", dismiss);
    document.addEventListener("keydown", onEscape);
    return () => {
      document.removeEventListener("mousedown", dismiss);
      document.removeEventListener("keydown", onEscape);
    };
  }, [open]);

  return (
    <header className="app-head">
      <span className="brand"><Icon name="gauge" />{texts.appTitle}</span>
      <span className="account" ref={account}>
        <button type="button" className="glyph" aria-haspopup="menu" aria-expanded={open}
                aria-label={texts.accountMenu} onClick={() => setOpen((o) => !o)}>
          <Icon name={open ? "close" : "menu"} />
        </button>
        {open && (
          <span className="account-menu">
            <span className="account-email">{email}</span>
            <span role="menu">
              <button type="button" role="menuitem" className="menu-item"
                      onClick={() => { setOpen(false); onSignOut(); }}>
                {texts.signOut}
              </button>
            </span>
          </span>
        )}
      </span>
    </header>
  );
}
