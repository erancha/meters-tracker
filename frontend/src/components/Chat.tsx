/**
 * The chat screen: the thread, the failure notices, and the composer, driven by useChat.
 */
import type { Api } from "../api";
import type { Focus } from "../types";
import { texts } from "../texts";
import { useChat } from "../useChat";
import { Composer } from "./Composer";
import { MessageList } from "./MessageList";

export function Chat({ api, onOpen, focus = null }: {
  api: Api;
  onOpen: (src: string) => void;
  focus?: Focus | null;
}) {
  const {
    messages, snapshot, sendText, sendPhotos, choose, choosing, pending, failed, rejected,
    retry, loadFailed,
  } = useChat(api);
  if (loadFailed) return <div className="alert" role="alert">{texts.loadFailed}</div>;
  if (snapshot === undefined) return null;
  return (
    <div className="chat">
      <MessageList api={api} messages={messages} snapshot={snapshot} onChoose={choose}
                   choosing={choosing} thinking={pending} onOpen={onOpen} onRetry={retry}
                   focus={focus} />
      {failed && <div className="alert" role="alert">{texts.turnFailed}</div>}
      {rejected && <div className="alert" role="alert">{texts.imageRejected}</div>}
      <Composer onSend={sendText} onPhotos={sendPhotos} />
    </div>
  );
}
