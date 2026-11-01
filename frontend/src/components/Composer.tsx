/**
 * The bottom bar of the chat: the attach button (opens the camera or photo picker), the text
 * draft, and the send button. Send is enabled for any non-empty draft.
 */
import { useRef, useState } from "react";
import { texts } from "../texts";
import { Icon } from "./Icon";

export function Composer({ onSend, onPhotos }: {
  onSend: (text: string) => void;
  onPhotos: (files: File[]) => void;
}) {
  const [draft, setDraft] = useState("");
  const picker = useRef<HTMLInputElement>(null);
  const sendable = draft.trim() !== "";

  function submit() {
    if (!sendable) return;
    onSend(draft.trim());
    setDraft("");
  }

  return (
    <form className="composer" onSubmit={(e) => { e.preventDefault(); submit(); }}>
      <button type="button" className="glyph" aria-label={texts.attach}
              onClick={() => picker.current!.click()}>
        <Icon name="camera" />
      </button>
      <input ref={picker} type="file" hidden data-testid="photo-input" accept="image/*"
             multiple
             onChange={(e) => {
               onPhotos([...e.target.files!]);
               e.target.value = "";
             }} />
      <input className="draft" value={draft} placeholder={texts.composerPlaceholder}
             onChange={(e) => setDraft(e.target.value)} />
      <button type="submit" className="send" disabled={!sendable}>{texts.send}</button>
    </form>
  );
}
