/**
 * Signed-in shell: the header above the chat, with the photo sidebar beside the chat once the
 * window is at least the configured width. No sidebar renders until the config has loaded.
 *
 * The full-screen lightbox is shared by the chat and the sidebar and renders here, outside both:
 * the sticky sidebar starts its own stacking context, which would keep a lightbox inside it
 * beneath the sticky header and composer.
 */
import { useState } from "react";
import type { Api } from "../api";
import { useAppConfig, useMinWidth } from "../useAppConfig";
import { useChatQuery } from "../useChat";
import { Chat } from "./Chat";
import { Header } from "./Header";
import { Lightbox } from "./Lightbox";
import { Sidebar } from "./Sidebar";

export function App({ email, api, onSignOut }: { email: string; api: Api; onSignOut: () => void }) {
  const config = useAppConfig();
  const wide = useMinWidth(config.data?.sidebar_min_width_px);
  const history = useChatQuery(api);
  const [opened, setOpened] = useState<string | null>(null);
  return (
    <>
      <Header email={email} onSignOut={onSignOut} />
      <div className="columns">
        <Chat api={api} onOpen={setOpened} />
        {wide && <Sidebar messages={history.data?.messages ?? []} api={api} onOpen={setOpened} />}
      </div>
      {opened !== null && <Lightbox src={opened} onClose={() => setOpened(null)} />}
    </>
  );
}
