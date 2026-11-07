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
final step fails, or a first sign-in by an address not on the deployment's allowed list, shows the
Hebrew reason in place of the chat; a refusal's reason names the deployment's admin address to ask
for access. Beside it, one button starts sign-in again with another Google account, and a second
copies the admin's address to the clipboard, with a note below saying it was copied. The reason
leaves the address bar once shown, so a reload lands on the landing page.

## Chat

- The thread shows the history, oldest first: the user's texts and photos on one side, the
  app's replies on the other. Each of the user's texts and photos shows its date and time to the
  minute, in Israel time. The thread opens at its bottom and keeps its bottom in view as it
  grows, so the latest reply and the thinking line sit just above the composer.
- A new account's thread opens with a welcome that says to start with a meter photo. A
  delegate's first visit names the owner's address, says the delegate works in that account,
  and that period reports go to that address.
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
- The count of waiting items appears in the panel beside the chat, or in the strip under the
  header on a phone, not in the chat itself.

## Cards

- A reply that needs an answer is a card: a title, its lines, and buttons. The card's expected
  answer, confirm where it offers one, is the primary button; the other options are chips
  beside it.
- A reading card for a two-number display has only the two numbers as buttons: the marked one,
  the upper at first, is primary, and pressing either saves the reading with that number.
- Each question card highlights the answer it expects: closing the period on a reading near the
  period's end, closing again on a reading just after a close, and "misread" on a reading lower
  than the one before. A card whose options are even, such as the period start, highlights none.
- A reading far above what the meter's history makes plausible gets a card naming the daily
  consumption and the meter's usual figure before it is saved; confirm saves it, a typed value
  corrects it. A value typed that is the meter's own serial is answered as the serial, and the
  card asks for the reading.
- The new-meter questions offer each existing property and apartment as a button. A typed name
  that is a prefix of an existing one, or two letters or fewer from it, gets a card asking which
  was meant, with the existing name highlighted and a button for a new one by the typed name.
- The new-meter summary shows the reading's date once when it is the period's start. A reading
  dated after the end of the period it would open gets no confirm button: the card asks for its
  date. One dated inside a closed period says that a later reading inside it adds the meter to
  that period.
- The close card offers only confirm. "The apartment is empty", typed, closes without a next
  period. A close chosen on a reading's card that waited for another meter's photo closes once
  that photo is confirmed, with no card.
- Only the card the server is waiting on has working buttons. It has an accent border and the
  tag `ממתין לתשובה`, so the next message's target is plain without counting. The latest card of
  each item queued behind it is dashed and faded, tagged `בתור`, with no buttons. An answered card
  shows the answer in place, as a done mark for the primary option or the marked chip for another.
- A waiting card about a meter photo, the live one or a queued one, shows a small thumbnail of
  that photo beside its tag, which opens the full-screen viewer. Several photos sent at once each
  get a reply that shows its own photo, including a reply whose serial could not be read. An
  answered card shows none, so a photo appears once, on its latest waiting card.
- A pressed option shows on its card at once, with no bubble of its own. While that answer is
  on its way, the waiting card's buttons are disabled; photo uploads do not disable them.
- When several photos wait for confirmation, only the oldest card has working buttons. When an
  answer finishes one card and the queue moves on to a card shown earlier, that card is shown
  again at the end of the chat, and its copy above loses its buttons.

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
  about, and nothing is saved. A request the app does not offer, such as a reminder on a date,
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

## Delegates

- The owner adds a delegate in a typed sentence ("הוסף את dana@example.com כמורשה"). A card
  titled `הוספת מורשה` names the address and what it will be able to do; "אישור" writes it, and
  the reply says that the address's next Google sign-in lands in this account. An address
  signing in for the first time must also be on the allowed list. An address typed
  on the card replaces the one shown. A request with no address ("הוסף מורשה") shows a card
  asking for it, and the address typed next fills it.
- An address the account cannot take is refused in the reply, with no card: the owner's own
  address, an address already a delegate here, or one that holds data or delegates for another
  owner. The refusal does not say what the other account holds.
- "מי המורשים?" lists the delegates' addresses, or says there are none.
- Removing a delegate shows a card titled `הסרת מורשה` saying the address loses access; "אישור"
  deletes its row, and the reply says its next sign-in opens an empty account of its own.
- A delegate who asks to add, list or remove a delegate is told that only the owner can.

## Admin

- The deployment's admin address signs in to its own chat, but manages no meters: the message
  box, the camera button and the send button are disabled.

## Day panel

The chat shows one day, today when it opens. On a window at least `sidebar_min_width_px` wide
(900 px in `config/app.json`), a panel beside it, titled with the day's name and date (`היום · יום
ראשון 01/11/2026` for today, `יום שישי 30/10/2026` for an earlier day), shows that day's meter
photos, dated by when they were uploaded, in up to two collapsed sections, each only when it has
lines:

- `קריאות ממתינות · 2`: photos that still need something. A line is the time and the state:
  `לאישור` (a card awaits an answer), `לשליחה` (failed, awaiting a retry) or `בקריאה` (the model has
  not answered yet). Today's section also lists every photo still failed from an earlier day, as
  its date, time and `לשליחה` (`30/10 19:02 · לשליחה`), after today's own lines.
- `קריאות שבוצעו · 7`: readings saved. A line is the time, the property / apartment and the meter
  type.

While a request for tariffs is open, the panel also shows `חסרים תעריפים` above the sections, even
when another card is shown first, and a tap scrolls to the card asking for them.

A tap on a line scrolls the chat to that photo and highlights it for a moment; a line from an
earlier day first moves the chat to that day, where the photo keeps its retry button. The sections
follow the chat with no reload. With neither section, the panel says `לא נקראו מונים היום`, or
`לא נקראו מונים ביום זה` on an earlier day.

### Moving between days

Arrows on either side of the day's title move the chat and the panel one day back or forward, and
a tap on the title opens the browser's date picker for any earlier day. No day after today can be
shown, so the forward arrow is disabled on today. While today's thread opens a new account, the
title shows alone, without arrows or date picker. While an earlier day is shown, its title takes
the warning color, and a `היום` button beside it returns to today.

The owner may type or send a photo while an earlier day is shown, for example to correct a
reading saved that day. The composer then says `מוצג 30/10 · שינויים נרשמים היום`: what is sent is
recorded under today, and today's thread shows it too. The earlier day's thread keeps showing the
exchange below its own messages until the owner moves to another day. An earlier day with no
reading has nothing to correct, so its composer is disabled and carries no such note.

The panel opens 18 rem wide. Dragging the border between it and the chat sets its width, and so do
the arrow keys while the border has focus. The panel stays at least 192 px wide and the chat at
least 360 px, and the browser brings the chosen width back on the next visit. The page stops at
72 rem and stays centered in a wider window.

On a narrower window the same day shows as a strip under the header, which stays while the chat
scrolls: the arrows around the day as `היום 01/11` (`30/10` and the `היום` button on an earlier
day), then `חסרים תעריפים` while a request for tariffs is open (tapping as above), then a button
per section, `ממתינות 2` and `בוצעו 7`, each only when it has lines. A tap drops that section's
lines over the chat; a tap on the same button, or outside the lines, closes them with the chat
where it was, and a tap on a line closes them as it scrolls. The section buttons show only their
counts on a window 340 px wide or less, and on an earlier day up to 420 px, so the strip stays one
row on common phone widths.

## Report email

- Once a period is closed and both its meters are read and priced, the owner gets one email with
  a PDF of the apartment's period attached. When the tariff is missing at the close, saving it
  sends the email. Readings during the period send nothing.
- The chat says what happened to the report in the same turn, in a line naming the period as
  `<property> / <apartment>` and its start date: that it was emailed and to which address, what
  the bill still lacks (a meter, a closing reading, a price), or that sending failed. A failure
  shows in the owner's chat too when a delegate's answer caused it, and the report goes out on
  the first chat load of a later day. Writing that the address is now verified, or asking for the
  report again, sends every failed report in that turn; the chat names the address instead when
  it is still unverified, and says so when no report is waiting.
- Every report of one apartment has the subject `[<property> / <apartment>] קריאות מונים`, so
  Gmail groups them into one conversation; the date and the total are in the body.
- The PDF shows the property, the apartment, the period and its closing date, then a table
  with a column per meter: opening reading, closing reading, units, price and cost, and a total.
- After a re-close, the body and the top of the PDF name the date of the report to ignore.

## Account menu

The header shows the app's name and a menu button. The menu holds the signed-in address and a
sign-out item; a press outside the menu or Escape closes it.
