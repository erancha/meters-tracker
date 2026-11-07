/**
 * Signed-in shell: the header above the chat, with the day's sidebar beside the chat once the
 * window is at least the configured width and a draggable border between them, or the day strip
 * above the chat on a narrower window. Neither renders until the config has loaded. Both move the
 * chat to another day, and a line tapped in either focuses its message in the chat, moving to the
 * message's day first. The full-screen lightbox renders here.
 */
import { useState } from "react";
import type { Api } from "../api";
import { hasLines, opensNewAccount } from "../activity";
import { useAppConfig, useMinWidth } from "../useAppConfig";
import { useChatQuery } from "../useChat";
import { Chat } from "./Chat";
import { DayStrip } from "./DayStrip";
import { Header } from "./Header";
import { Lightbox } from "./Lightbox";
import { PanelResizer, usePanelWidth } from "./PanelResizer";
import { Sidebar, type DayView } from "./Sidebar";
import type { Focus } from "../types";
import { jerusalemDay } from "../messageTime";

export function App({ email, api, onSignOut }: { email: string; api: Api; onSignOut: () => void }) {
  const config = useAppConfig();
  const wide = useMinWidth(config.data?.sidebar_min_width_px);
  const today = jerusalemDay();
  // The earlier day the owner moved to; null shows today, which turns over at midnight.
  const [earlier, setEarlier] = useState<string | null>(null);
  const day = earlier ?? today;
  const history = useChatQuery(api, day);
  const todayHistory = useChatQuery(api, today);
  const [opened, setOpened] = useState<string | null>(null);
  const [focus, setFocus] = useState<Focus | null>(null);
  const panel = usePanelWidth();
  const show = (next: string) => {
    setEarlier(next === today ? null : next);
    setFocus(null);
  };
  const dayView: DayView = {
    messages: history.data?.messages ?? [],
    earlierFailed: day === today ? todayHistory.data?.earlier_failed ?? [] : [],
    tariffsMissing: history.data?.snapshot.tariffs_missing ?? false,
    day,
    today,
    newAccount: todayHistory.data !== undefined && opensNewAccount(todayHistory.data.messages),
    onDay: show,
    onFocus: (at: string) => {
      const target = at.slice(0, 10);
      if (target !== day) setEarlier(target === today ? null : target);
      setFocus((f) => ({ at, n: (f?.n ?? 0) + 1 }));
    },
  };
  return (
    <>
      <Header email={email} onSignOut={onSignOut} />
      {wide === false && <DayStrip {...dayView} />}
      <div className="columns" style={panel.style}>
        <Chat api={api} day={day} onOpen={setOpened} focus={focus}
              closed={day !== today && !hasLines(dayView.messages, dayView.earlierFailed)} />
        {wide && (
          <>
            <PanelResizer width={panel.width} resize={panel.resize} />
            <Sidebar {...dayView} />
          </>
        )}
      </div>
      {opened !== null && <Lightbox src={opened} onClose={() => setOpened(null)} />}
    </>
  );
}
