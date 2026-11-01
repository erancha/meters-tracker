/**
 * The photo strip beside the chat on wide windows: the history's stored photos, newest first,
 * each a thumbnail that opens the lightbox.
 */
import type { Api } from "../api";
import { texts } from "../texts";
import type { Message } from "../types";
import { Thumbnail } from "./Thumbnail";

export function Sidebar({ messages, api, onOpen }: {
  messages: Message[];
  api: Api;
  onOpen: (src: string) => void;
}) {
  const photos = messages
    .filter((m) => m.image_key !== null)
    .sort((a, b) => b.at.localeCompare(a.at));
  return (
    <aside className="sidebar" aria-label={texts.photosTitle}>
      <h2>{texts.photosTitle}</h2>
      <ul>
        {photos.map((m) => (
          <li key={m.image_key}>
            <Thumbnail imageKey={m.image_key!} api={api} onOpen={onOpen} />
          </li>
        ))}
      </ul>
    </aside>
  );
}
