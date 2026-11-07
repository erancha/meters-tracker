/**
 * The chat screen of one day: the thread, the failure notices, and the composer, driven by
 * useChat. On an earlier day the composer notes that what is sent now is recorded today; a closed
 * day, an earlier one with no reading to correct, disables the composer and drops the note.
 */
import type { Api } from "../api";
import { jerusalemDay, shownDayMonth } from "../messageTime";
import type { Focus } from "../types";
import { texts } from "../texts";
import { useChat } from "../useChat";
import { Composer } from "./Composer";
import { MessageList } from "./MessageList";

export function Chat({ api, day, onOpen, focus = null, closed = false }: {
  api: Api;
  /** The day shown, as a stamp's date part. */
  day: string;
  onOpen: (src: string) => void;
  focus?: Focus | null;
  closed?: boolean;
}) {
  const {
    messages, snapshot, sendText, sendPhotos, choose, choosing, pending, failed, rejected,
    retry, loadFailed, locked,
  } = useChat(api, day);
  if (loadFailed) return <div className="alert" role="alert">{texts.loadFailed}</div>;
  if (snapshot === undefined) return null;
  return (
    <div className="chat">
      <MessageList api={api} messages={messages} snapshot={snapshot} onChoose={choose}
                   choosing={choosing} thinking={pending} onOpen={onOpen} onRetry={retry}
                   focus={focus} />
      {failed && <div className="alert" role="alert">{texts.turnFailed}</div>}
      {rejected && <div className="alert" role="alert">{texts.imageRejected}</div>}
      <Composer onSend={sendText} onPhotos={sendPhotos} locked={locked || closed}
                notice={closed || day === jerusalemDay()
                  ? null : texts.earlierDayNotice(shownDayMonth(day))} />
    </div>
  );
}
