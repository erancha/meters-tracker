# Features

This document details, surface by surface, what a user of the browser app sees and does: the
product the [README](../README.md) overview lists. How the app is built is in
[Architecture](architecture.md#the-browser-app); the reading and period rules behind each reply
are in [Domain rules](domain-rules.md). Every screen is in Hebrew, right to left, and follows
the app inside the phone of the [demo](demo/demo.html).

## Landing page

A signed-out visitor sees one card: the app's name with the build's version and commit date under
it, a three-paragraph pitch, a "more" toggle and the "sign in with Google" button. The toggle swaps
the pitch for seven bullets condensing the README's overview, and back. Below the card, one link
opens the animated demo in a new tab, a second opens WhatsApp with an invite to the app prefilled
as a received recommendation, and a third opens the source repository on GitHub. A sign-in whose
final step fails shows a banner asking to reload and sign in again.

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

## Corrections

- Anything already saved is corrected by typing it in a sentence or a number, with or without a
  card on screen: "הקריאה הקודמת היא 843", "מספר המונה היה 2721058, לא 2721056", "דירה 3 נמצאת
  בלאונר 18". No field picker appears.
- A correction shows a card titled `תיקון נתונים שמורים`, ahead of any waiting card. Its lines name
  each changed field (`... · הקריאה: 843 במקום 820`), each record it removes or reopens, warnings
  such as a reading lower than the one before it, and every period whose consumption or charge
  changes, with the new figures beside the old. "אישור" saves it; "בטל" leaves everything as
  it was. Another value typed instead ("לא, 845") replaces the card. A correction the app refuses
  shows no card, only the reason and what to do instead.
- A correction that leaves out its new value ("הקריאה הקודמת לא נכונה") shows a question card
  naming the value on record and how to write the new one; the typed answer fills that field and
  shows the correction card. A typed correction on a card that lacks its value asks the same
  question there, and the next typed value fills that field.
- A question about how to do something gets one sentence on how, with an offer to do it here. A
  message that fits several records lists them. A number typed with no card waiting is asked
  about, and nothing is saved. A request the app does not offer yet, such as adding a delegate,
  says so. Any other message gets what the assistant can do, naming the account's apartments; the
  reply is never "לא הבנתי".

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
- A statement whose prices exclude VAT asks for the prices typed.
- Closing a period with no prices on record asks for them; once saved, the reply shows what each
  such period costs.

## Day panel

The chat shows one day, today. On a window at least `sidebar_min_width_px` wide (900 px in
`config/app.json`), a panel beside it, titled with the day's name and date (`היום · יום ראשון
01/11/2026`), shows that day's meter photos, dated by when they were uploaded, in up to two
collapsed sections, each only when it has lines:

- `קריאות ממתינות · 2`: photos that still need something. A line is the time and the state:
  `לאישור` (a card awaits an answer), `לשליחה` (failed, awaiting a retry) or `בקריאה` (the model has
  not answered yet).
- `קריאות שבוצעו · 7`: readings saved. A line is the time, the property / apartment and the meter
  type.

While a request for tariffs is open, the panel also shows `חסרים תעריפים` above the sections, even
when another card is shown first, and a tap scrolls to the card asking for them.

A tap on a line scrolls the chat to that photo and highlights it for a moment. The sections follow
the chat with no reload. With neither section, the panel says `לא נקראו מונים היום`.

The panel opens 18 rem wide. Dragging the border between it and the chat sets its width, and so do
the arrow keys while the border has focus. The panel stays at least 192 px wide and the chat at
least 360 px, and the browser brings the chosen width back on the next visit. The page stops at
72 rem and stays centered in a wider window.

On a narrower window the same day shows as a strip under the header, which stays while the chat
scrolls: the day as `היום 01/11` (or `חסרים תעריפים` while a request for tariffs is open, tapping
as above), then a button per section, `ממתינות 2` and `בוצעו 7`, each only when it has lines. A
tap drops that section's lines over the chat; a tap on the same button, or outside the lines,
closes them with the chat where it was, and a tap on a line closes them as it scrolls. A day with
no lines and no missing tariffs shows no strip.

## Report email

- After a confirmed reading that has a previous one, and after a close, the owner gets one email
  with a PDF of the apartment's period attached. A meter's first reading sends nothing.
- Every report of one apartment has the subject `[<property> / <apartment>] קריאות מונים`, so
  Gmail groups them into one conversation; the date and the total are in the body.
- The PDF shows the property, the apartment, the period and the date of the latest reading, then
  a table with a column per meter: opening reading, closing or latest reading, units, price
  and cost. A total closes it, marked as final or as an outlook. A meter with no reading in
  the period is named as such, and a total that cannot be calculated says so.
- After a re-close, the body and the top of the PDF name the date of the report to ignore.

## Account menu

The header shows the app's name and a menu button. The menu holds the signed-in address and a
sign-out item; a press outside the menu or Escape closes it.
