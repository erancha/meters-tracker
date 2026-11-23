import { texts } from "./texts";

/** The wa.me composer with the invite filled in, leaving only the pick of a contact or group.
 * An empty line separates the paragraphs. The page's own address closes the message right under
 * the line that leads into it, so the invite names whichever deployment sent it. */
export function whatsAppInviteUrl(): string {
  const message = `${texts.inviteMessage.join("\n\n")}\n${window.location.origin}`;
  return `https://wa.me/?text=${encodeURIComponent(message)}`;
}
