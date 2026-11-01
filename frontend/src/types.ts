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
}

/** The card a choice answered: its pending item and the version it showed. */
export interface CardRef {
  pending: string;
  version: number;
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
}

export interface Reply {
  text: string | null;
  card: Card | null;
  image_key: string | null;
}

/** What the browser renders: the card awaiting an answer, and how many other queued items wait. */
export interface Snapshot {
  card: Card | null;
  waiting: number;
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

/** A photo the browser has sent but the server's history does not hold yet; localUrl shows it. */
export interface LocalMessage extends Message {
  localUrl?: string;
  /** Sends again a message that never reached the server; set only on such a message. */
  resend?: () => void;
}
