// Shapes of the backend API (src/handlers/api.py), shared by the client and the chat views.

export interface CardOption {
  id: string;
  label: string;
}

export interface Card {
  kind: string;
  pending: string;
  version: number;
  title: string;
  lines: string[];
  options: CardOption[];
  /** The option id shown as the expected answer, or null on a card with none. */
  default: string | null;
  /** The stored photo a meter photo's card answers; null on any other card, and absent on a
   * card stored without one. */
  photo?: string | null;
}

/** The card a choice answered: its pending item and the version it showed. */
export interface CardRef {
  pending: string;
  version: number;
}

/** Which meter a photo read and where it stands; the place is null until the chat knows it. */
export interface Reading {
  state: "awaiting" | "saved" | "dropped";
  meter_type: "water" | "electricity" | "unknown" | null;
  apartment: string | null;
  property: string | null;
}

export interface Message {
  at: string;
  role: "user" | "assistant";
  text: string | null;
  card: Card | null;
  image_key: string | null;
  pdf_key: string | null;
  /** Set on the user message that records a choice: the card the choice answered. */
  choice: CardRef | null;
  /** True while handling this text or photo has failed and it awaits a retry. */
  failed?: boolean;
  /** The stamp of the latest retry, on a message that has had one. */
  retried_at?: string;
  /** Set on a photo's message once the model has answered. */
  reading?: Reading;
}

export interface Reply {
  text: string | null;
  card: Card | null;
  image_key: string | null;
}

/** What the browser renders: the card awaiting an answer, and the items queued behind it. */
export interface Snapshot {
  card: Card | null;
  /** The pending seqs of the queued items behind the card; every queued item when none shows. */
  queued: string[];
  /** A request for tariffs is open, even when another card is shown ahead of it. */
  tariffs_missing: boolean;
}

export interface TurnResult {
  replies: Reply[];
  snapshot: Snapshot;
  /** The stored key of the photo the turn sent; null for a text or a choice. */
  image_key: string | null;
}

/** Exactly one of the shapes: typed text, a photo (base64 JPEG or PNG), a card choice, or the
 *  stamp of a failed message to retry. */
export type TurnBody =
  | { text: string }
  | { image: string; media_type: string; note?: string }
  | { choice: { pending: string; version: number; option: string } }
  | { retry: string };

/** GET /chat: one day's history, the snapshot, the admin flag, and on today's history the photos
 *  still failed from earlier days, oldest first. */
export interface ChatData {
  messages: Message[];
  snapshot: Snapshot;
  admin: boolean;
  earlier_failed: Message[];
}

/** A request to show a message in the thread; n grows so a repeated tap scrolls again. */
export interface Focus {
  at: string;
  n: number;
}

/** A photo the browser has sent but the server's history does not hold yet; localUrl shows it. */
export interface LocalMessage extends Message {
  localUrl?: string;
  /** Sends again a message that never reached the server; set only on such a message. */
  resend?: () => void;
}
