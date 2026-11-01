# Features

This document details, surface by surface, what a user of the browser app sees and does: the
product the [README](../README.md) overview lists. How the app is built is in
[Architecture](architecture.md#the-browser-app); the reading and period rules behind each reply
are in [Domain rules](domain-rules.md). Every screen is in Hebrew, right to left, and follows
the app inside the phone of the [demo](demo/demo.html).

## Landing page

A signed-out visitor sees one card: the app's name, a three-paragraph pitch, a "more" toggle and
the "sign in with Google" button. The toggle swaps the pitch for seven bullets condensing the
README's overview, and back. Below the card, a link opens the animated demo in a new
tab. A sign-in whose final step fails shows a banner asking to reload and sign in again.

OPEN — not yet implemented (#2): the build's version, a WhatsApp invite and a source link.

## Chat

- The thread shows the history, oldest first: the user's texts and photos on one side, the
  app's replies on the other. Each of the user's texts and photos shows its date and time to the
  minute, in Israel time. The thread opens at its bottom and keeps its bottom in view as it
  grows, so the latest reply and the thinking line sit just above the composer.
- A new account's thread opens with a welcome that says to start with a meter photo.
- At the bottom, the composer holds the camera button, the draft and the send button. Send is
  disabled only while the draft is empty. A typed reply is sent even while the card awaiting
  an answer offers a confirm button; the server reads it with that card as context.
- A sent text or photo shows at once, before the server answers. A turn the server refused
  shows a notice that the message was not sent; a turn whose answer was lost on the way shows
  no notice, and the chat reloads to show whatever the server did with it.
- A text or photo that could not be handled, for any reason, shows "לא טופל" and a "ניסיון חוזר"
  button under it, which stays after a reload. A retry handles the same message, with no second
  bubble and no new photo, and the message shows the time of its latest retry beside its sent
  time. A message that never reached the server keeps the button only while the page is open.
- While a message or photo is on its way, or a photo is still being read and no card is shown,
  the end of the thread shows "חושב" with moving dots. Then the chat checks for news every five
  seconds, so the card of a photo that took long to read appears without a reload.
- While a card is shown, a line at the end counts the questions queued behind it.

## Cards

- A reply that needs an answer is a card: a title, its lines, and buttons. Confirm is the
  primary button; the other options are chips beside it.
- Only the card the server is waiting on has working buttons. An answered card shows the
  answer in place, as a done mark for confirm or the marked chip for another option.
- A pressed option shows on its card at once, with no bubble of its own. While that answer is
  on its way, the waiting card's buttons are disabled; photo uploads do not disable them.
- When several photos wait for confirmation, only the oldest card has working buttons. After
  an answer, the next card may sit above the latest replies.

## Photos

- The camera button lets the user take a photo or pick existing ones (on a phone, the system
  offers the camera and the gallery); several can be picked at once.
  Each photo is its own turn; several upload together, each shown at once as a local preview.
- A photo the server refuses as too large or of an unsupported type shows a notice asking for
  a smaller JPEG or PNG.
- A photo in the thread opens full screen on a tap; the close button or Escape closes it. A
  photo whose link cannot be fetched shows a placeholder line instead.

## Tariffs

- The owner types the prices of electricity and water in plain words, or photographs a
  previous statement. A card shows the prices, that they include VAT, and the date they apply
  from; "אישור ושמירה" saves them. A typed price or date on the card changes it first.
- A statement's card names the apartment and period it was issued for, and lists the tariff
  lines it does not save. A statement whose prices exclude VAT asks for the prices typed.
- Closing a period with no prices on record asks for them; once saved, the reply shows what each
  such period costs.

## Photo sidebar

On a window at least `sidebar_min_width_px` wide (900 px in `config/app.json`), a sidebar
beside the chat lists every photo in the history, newest first. A tap opens the same
full-screen view as in the thread.

OPEN — not yet implemented (#1): reaching the sidebar on a narrow screen.

## Account menu

The header shows the app's name and a menu button. The menu holds the signed-in address and a
sign-out item; a press outside the menu or Escape closes it.
