import type { ReactElement } from "react";

// Line icons of the demo, drawn in the current text colour.
const PATHS: Record<string, ReactElement> = {
  gauge: <><path d="M4 16a8 8 0 1 1 16 0" /><path d="M12 16l4-5" /><circle cx="12" cy="16" r="1.4" /></>,
  camera: <><path d="M4 8h3l2-3h6l2 3h3v11H4z" /><circle cx="12" cy="13" r="3.5" /></>,
  back: <path d="M9 6l6 6-6 6" />,
  forward: <path d="M15 6l-6 6 6 6" />,
  menu: <path d="M4 7h16M4 12h16M4 17h16" />,
  close: <path d="M6 6l12 12M18 6L6 18" />,
  clip: <path d="M8 12.5l5.5-5.5a3 3 0 1 1 4.2 4.2l-7 7a5 5 0 0 1-7-7l6-6" />,
};

export function Icon({ name }: { name: keyof typeof PATHS }) {
  return <svg className="icon" viewBox="0 0 24 24" aria-hidden="true">{PATHS[name]}</svg>;
}
