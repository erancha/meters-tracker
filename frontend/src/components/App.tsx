/**
 * Signed-in shell: the header above the chat, with the day's sidebar beside the chat once the
 * window is at least the configured width, and a draggable border between them. No sidebar renders
 * until the config has loaded. A line tapped in the sidebar focuses its message in the chat. The
 * full-screen lightbox renders here.
 */
import { useState } from "react";
import type { Api } from "../api";
import { useAppConfig, useMinWidth } from "../useAppConfig";
import { useChatQuery } from "../useChat";
import { Chat } from "./Chat";
import { Header } from "./Header";
import { Lightbox } from "./Lightbox";
import { PanelResizer, usePanelWidth } from "./PanelResizer";
import { Sidebar } from "./Sidebar";
import type { Focus } from "../types";
import { jerusalemDay } from "../messageTime";

export function App({ email, api, onSignOut }: { email: string; api: Api; onSignOut: () => void }) {
  const config = useAppConfig();
  const wide = useMinWidth(config.data?.sidebar_min_width_px);
  const history = useChatQuery(api);
  const [opened, setOpened] = useState<string | null>(null);
  const [focus, setFocus] = useState<Focus | null>(null);
  const panel = usePanelWidth();
  return (
    <>
      <Header email={email} onSignOut={onSignOut} />
      <div className="columns" style={panel.style}>
        <Chat api={api} onOpen={setOpened} focus={focus} />
        {wide && (
          <>
            <PanelResizer width={panel.width} resize={panel.resize} />
            <Sidebar messages={history.data?.messages ?? []}
                     tariffsMissing={history.data?.snapshot.tariffs_missing ?? false}
                     day={jerusalemDay()}
                     onFocus={(at) => setFocus((f) => ({ at, n: (f?.n ?? 0) + 1 }))} />
          </>
        )}
      </div>
      {opened !== null && <Lightbox src={opened} onClose={() => setOpened(null)} />}
    </>
  );
}
