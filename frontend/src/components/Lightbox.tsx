/**
 * A photo shown full screen over the chat. The close button and Escape both call onClose.
 */
import { useEffect } from "react";
import { texts } from "../texts";

export function Lightbox({ src, onClose }: { src: string; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="lightbox">
      <button type="button" className="lightbox-close" aria-label={texts.close}
              onClick={onClose}>✕</button>
      <img src={src} alt={texts.photo} />
    </div>
  );
}
