# The conversation engine

This document details the conversation engine that
[Architecture](architecture.md#the-conversation-engine) summarizes: what each flow does with a
meter photo, a typed edit, a correction of a saved record and a message it cannot act on. The
checks, values and lifecycles those flows apply are in [Domain rules](domain-rules.md).

**Contents:** [A meter photo](#a-meter-photo) · [Typed edits on a card](#typed-edits-on-a-card) ·
[Corrections of saved records](#corrections-of-saved-records) ·
[Replies that ask back](#replies-that-ask-back)

## A meter photo

Each photo is its own turn. The model returns the meter type, the serial with whether any of its
digits is uncertain, every counter value it sees with its position on the display (`top` and
`bottom` on a two-number electricity display, in whole kWh without the tenths digit the display
ends in, `single` on a water counter), whether any digit is unreadable, and the text handwritten
beside the meter as written, read in the direction it is written (often sideways), or none when it
cannot be read; owners write the apartment's name there, and on an electricity meter that label is
the serial the photo goes by, so a photo of one with no label is unreadable, and its card asks for
the label to be typed or photographed. A note sent with the photo that names a date sets the
reading date; otherwise the reading is dated the day the photo was sent, which a retry on a later
day keeps.

- **Statement.** A photo with no known meter type and no serial is read again as a previous
  statement; a statement becomes a tariffs card (see [Tariffs](domain-rules.md#tariffs)), dated
  like a reading.
- **Not a meter.** Any other photo of an unknown type and an unknown serial is answered as
  "no meter found" and nothing is kept.
- **Unreadable.** A missing or unreadable serial asks for a retake. A value the model could not
  read at all asks for a retake or a typed value. A value read with an uncertain digit is
  proposed on the reading card, and on a new meter's summary, with a line naming the doubt; a
  typed value settles it. A value, read or typed, that equals the meter's own serial is no
  reading: the reply says so and the card asks for the reading. A new photo replaces an earlier
  unreadable card that had no serial or had the same serial, when it was sent after that card was
  shown: photos sent together are separate meters, never retakes of each other.
- **Retired meter.** A photo of a retired meter is refused, naming the live meter that replaced
  it, when its apartment has one of its type.
- **Two-number display.** With no saved choice, the card offers both numbers as its only
  buttons, the top one marked as the default, and pressing either saves the reading with that
  number. A display showing the same number twice gets a one-number card with confirm, and its
  reading leaves the choice unsaved. The position of the first saved reading chosen from two
  numbers becomes the meter's display choice, and later photos propose that position's number
  directly.
- **Near-miss serial.** A read water serial that names no meter but differs in one character
  from the serial of exactly one live water meter is taken as that meter's, with a reply naming
  both serials; a misread digit is the model's usual serial error. The saved reading counts the
  photo as misread, as a typed serial correction does. Only the model's read is matched, never a
  typed serial, and never an electricity label: labels a character apart name different
  apartments.
- **Unknown serial.** A link flow (`src/common/flows/link_meter.py`) asks for the property and then
  the apartment, existing or new by name. A serial read with an uncertain digit is named as
  uncertain on the property question, and a typed serial settles the doubt. An answer naming
  both ("לאונר 18, דירה קדמית") skips the apartment question; a property or apartment name typed
  on a later question corrects the one given, forgets the period answered for it, and the flow
  resumes from the corrected name. An apartment with no open period also asks for the period's
  start (buttons for today and for the first of next month, which is left out on the first of a
  month; a typed date also answers) and length (default `default_period_months`, or the
  apartment's own length when it exists). A new period that starts before the photo's date dates
  the opening reading at the start, since the photo then stands for the meter on that day; a
  later start keeps the photo's date, and a date equal to the start shows once, on the opening
  line. The questions offer the existing names as buttons; the apartment question also offers
  the handwritten label as a new apartment's name, highlighted, unless it already names one of
  the property's apartments. A typed name that is not an existing one but is a prefix of one, or
  within two single-letter edits of one, asks whether that name was meant; a name chosen as new, by
  that question or by the label's button, is not asked about. A reading dated after the last day of
  the period it would open is refused on the summary, which asks for its date. A reading dated
  inside a closed period, before its close, stands for the meter on the next period until a later
  reading of the meter inside the closed period arrives; that reading's save flags the meter's
  readings from the period dates as the close would have, so the closed period gets the meter's
  opening and closing readings and is marked for its report. The final card shows everything that
  will be created; on confirmation the property, apartment and period are created as needed, the
  meter is saved, and its reading becomes the period's opening reading. A live meter of the same
  type in that apartment is retired, dated the new reading's date; each keeps its own consumption.
  When another photo of the same serial was linked while this one waited, it shows next as an
  ordinary reading card for that meter.
- **Known serial.** The card proposes the reading; the user confirms, picks the other number,
  or types a corrected value, a date, or a cancel. Cards carry no reject button: corrections and
  cancels are typed.
- **Corrected serial.** A typed serial, whole or as a few corrected digits, replaces the one the
  model read on any card of the photo. The photo then goes to that serial's meter: a reading
  card for a known meter, the link questions for a new one.
- **Failed photos.** A photo counts as failed when its card was unreadable or stalled and a later
  photo replaced it, or when it is the saved photo and the confirmed value is not among the readable
  values the model returned for it or its serial was corrected. Choosing the other number of a
  two-number display is not a failure. A failed card with a serial hands its photos to the next
  photo of that serial. A failed card with no serial (unreadable, or stalled before the model
  answered) cannot name its meter, so it hands them to the next photo whatever its meter; the check
  that scores another model on a reading's failed photos ([Tests](development.md#backend-tests))
  may then mark such a photo "serial NO" for every model. The reading row keeps the failed photos'
  keys, in the order they were sent, and the `LLM_MODEL` value of the model that failed on them. A
  replacing duplicate reading whose new photo was read right removes the keys and the model from
  the replaced row.

## Typed edits on a card

A waiting card is a form, and a typed answer to a question card is an edit of its empty field. An
edit may change any field of the card at any step, and the card the flow then arrives at is shown.

- **Unreadable card.** The value, date, serial and meter type. A bare input fills the serial
  while the serial is what is unreadable, else the value.
- **Reading card and its checks.** The value, date and serial. A bare input fills the value.
- **Link questions and summary.** The value, date, serial, meter type, property and apartment,
  plus the period's start and length while the link creates the period. A bare input fills the
  asked field, and the value on the summary. On the period question a date is the period's start.
- **Period change card.** The start and the length of a period opening on a vacant apartment. A
  bare input fills the start.
- **Tariffs card.** Both prices and the effective date. A bare number fills nothing, since it
  could be either price.
- **Close card.** It lists saved readings, each under its record's id, so a change to one is a
  correction of that record. Once it is confirmed, the close card shows the current readings.
- **Current records.** A waiting card is built again from the saved records each time it is about
  to be shown, after any turn and on each load of the chat, so a correction by anyone shows on it
  at once. A card that now reads differently is shown anew; one whose records are gone is dropped,
  saying why and what to do next, and the card after it is built again in turn. An answer to a
  card that changed since it was shown is not acted on: the card is shown again.
- **Looking records up.** The classifier gets no saved records with a typed message; it looks up
  the ones the message names with `find_records`, by apartment, meter type and date. An apartment
  matches when every word of the query is a word of its property and apartment names, ignoring
  quotes, geresh and case, with a slash, hyphen or punctuation mark splitting words as a space
  does; a property matches on its own name too. A match brings its property, its meters of the
  named type with their latest `correction_recent_readings` readings and every reading of the
  named date, and its periods; no name brings every property, apartment and meter without
  readings; every answer carries the reading saved last, which an undo names; tariffs and
  delegates come when asked. A name that matches nothing answers with every apartment's name,
  so the model can look again. A record from any day can be corrected from today's chat.
- **A card behind the first.** The classifier gets every waiting card with its fields, so an edit
  may name a card further back in the queue. That card takes the edit and is shown; the first card
  stays in front.
- **A dropped photo.** The classifier also gets the user's latest `correction_dropped_photos`
  photos the app did not keep: cancelled, not a meter, a retired serial, or read as a statement.
  Only a photo of this account that no saved reading (its own or a failed photo), tariff or
  waiting meter card holds is listed, so a photo is never counted twice; an edit naming any other
  is refused with what to do instead. A waiting statement card leaves the queue when its photo is
  reopened as a meter's. An edit naming a listed one reads the stored photo again,
  dated the day it was sent, applies its typed value, date, serial or meter type, and routes it as
  a new photo. A typed kind forces the reading: a statement becomes a tariffs card; a meter is
  never dropped as no meter, so a type the model cannot tell is asked for. A photo the model read
  no meter type in counts once among the failed photos of the reading it becomes.
- **Checks run again.** A value, date or serial changed on a check card
  ([Checks before a reading is saved](domain-rules.md#checks-before-a-reading-is-saved)) returns
  to the reading card, so the checks run again on confirmation, and takes back a reset chosen on
  the earlier reading.
- **Refused edits.** One message may change several fields. Every change is checked first, so an
  edit the card cannot take changes nothing and the card is shown again. A missing or unfitting
  value is answered with the field's question naming its current value, and the card then holds
  that field as asked: the next bare input fills it, not the default field, until the card is shown
  anew or moves to another step. Any other edit the card cannot take is answered with what the
  card takes (its buttons, a typed correction of any detail on it, or a cancel).
- **Edits that change nothing.** An edit that sets each field to the value it already holds,
  such as naming the apartment the card already names, says so and shows the card again as it
  was; the questions already answered stay answered.

## Corrections of saved records

A known meter's type and place and an open period are saved records, so no card edits them. A
change to any saved record (a property, apartment, meter, reading, period, tariff or delegate's
address) is planned on a copy of the account and shown on one correction card, ahead of any
waiting card. The card lists each changed field as its new value in place of the old one, every
record the change removes or reopens, and every meter's figures (consumption and charge) in
every period of its apartment that differ, closed periods included. Nothing is written until
the card is confirmed, and then every row is written in one transaction; a correction touching
more rows than that holds is refused. Confirming plans again from the current records: when the
card would say something else it is shown again and nothing is written. A correction never sends
a report. A new correction replaces one still waiting, so only the latest can be confirmed; a
message giving another value for what the card shows ("לא, 845") is such a new correction.

- **Warn or refuse.** A value out of order with its neighbours is a warning on the card, since
  fixing two wrong readings passes through that state. A refusal shows no confirmation and names
  the reason and what to do instead: two readings of a meter on one day, overlapping periods, a
  closing reading before its opening reading, two live meters of one type in an apartment, two
  tariffs of one date, a repeated name in one property or account, two open periods in an
  apartment, readings no period of a meter's new apartment covers. Whatever the operation,
  the result is also checked as a whole: a reading counted in a period before the change and not
  deleted by it is still counted after; a live meter that opens a closed period also closes it;
  every period flag names a period of the reading's own apartment. Periods overlap when one
  starts before the one before it closed: a period may start on its neighbour's closing day,
  since one closing reading can stand for both.
- **A reading's value, date or reset.** The meter's figures in each period it counts in are
  recomputed. A date that crosses a period moves the reading's flags with it. A reading counted in
  a period after its opening reading may not be dated before the period's start: a date change
  that leaves one so flags the meter's readings again from the period dates, and a result that
  still holds one is refused.
- **A reading on another meter.** It moves with its flags rebuilt against the target meter's
  periods; moving a meter's last reading follows the rule of deleting it.
- **Delete a reading.** An opening flag passes to the meter's next reading in that period. A
  closing flag passes to the previous reading after the opening; with none, the period reopens. A
  row that closes one period and opens the next gives both flags to the previous reading after the
  opening, as a close does; with none, the period reopens. A reset flag passes to the next reading.
- **A meter's last reading.** Deleting it deletes the meter too, which undoes its link. A
  replacement the link caused is undone, so the replaced meter is live again with its readings
  rebuilt against the apartment's periods. A period, apartment or property left empty that was
  created for the link goes too. The card lists each.
- **A meter's serial.** The meter keeps every former serial, so a card holding an old serial still
  finds its meter. A serial another meter holds merges the two: allowed for the same type and
  apartment, the readings join and the other row goes; another type or apartment, or a same-day
  clash, is refused.
- **A meter's apartment.** Its readings' flags are rebuilt from the target apartment's periods by
  date. With no target period covering its first reading, the source's periods are copied to the
  target. A reading that would fall outside every target period is refused, naming its dates.
- **A meter's display.** It changes which number later photos read; the card lists the meter's
  past readings, each correctable by its value. A water meter, with one number, has no display.
- **A meter's retirement.** A date retires the meter by hand; a blank brings a retired meter back,
  with its readings rebuilt against the apartment's periods.
- **Merge.** Apartments merge by moving every meter of the one that goes, then deleting it.
  Properties merge by moving every apartment of the one that goes, then deleting it.
- **Delete a property or apartment.** Refused while it holds apartments or meters, naming them. A
  period has no delete: a close is undone by reopening, a start by moving it.
- **A period's start or length.** The readings that open or close it move with the start. An
  apartment's length is also its open period's, so the next close carries it.
- **Reopen a period.** A blank closing date clears it and every closing flag of the period, and
  the next period merges back: its row goes and its readings count in the reopened period. It is
  refused unless that next period is open or absent. A live meter with no opening in the reopened
  period opens it on its latest reading inside it, or on the day before its start when the
  previous period closed then. Any other closing date is refused, naming the closing reading's
  date as the way to change it.
- **Another closing reading.** The chosen reading takes both flags of its meter's closing reading
  in the period, and the period's closing date and the next period's start follow it. It is
  refused when the meter has no closing reading there, or when the chosen reading already opens or
  closes another period.
- **Vacate.** Adding one to a closed period removes the period after it, refused while that period
  is closed or holds readings. Undoing one on a closed period with no later period opens the next.
- **A tariff.** Its prices, its date and its deletion recompute every charge they reach.
- **A delegate's address.** Owner only. The card says which address loses access and which gains
  it, and that the new address loses a waiting card it holds. An address that holds data or
  delegates for another owner is refused, on the card and again on confirmation, in one neutral
  text. Confirming clears the waiting items of both addresses in the same transaction.
- **A delegate's corrections.** A delegate cannot change a delegate's address or delete an
  apartment or property, and is refused any change whose result removes a property, apartment or
  meter that held more than one reading.
- **Failed photos.** A saved reading whose value or serial changes adds its photo and the model's
  name to its failed photos, once.
- **Day panel.** The acting person's photos have their reading tags brought in step with the
  records: a photo whose reading is gone counts as dropped. Other people's tags are left as they
  were.

## Replies that ask back

No reply only says it did not understand. Each comes from a fixed template in
`src/common/texts.py`, chosen by `ask_back` (`src/common/flows/`) from the intent and the field
table; the model never writes it.

- **A missing or unfitting value** on a saved record or a dropped photo shows a question card
  naming the field's current value, with the field's how-to text. It comes first in the queue,
  ahead of a correction, and its answer fills only that field: a bare value never reaches a card
  behind it. The completed message then goes on as it would have, so a value that still does not
  fit asks again. A cancel drops the question; a new one replaces it, and so does the first card of
  any newer photo, correction or request, so text meant for that card never fills the question.
- **A how-to question** about a field gets that field's how-to text and an offer to make the change
  here; one about no field gets what the assistant can do.
- **A message fitting several records** lists them, so the next message can name one.
- **A bare value with no card waiting** asks what it is, and nothing is written.
- **A request the app does not offer**, such as a reminder on a date, says so, then what it can do.
- **Anything else** gets what the assistant can do, with an example naming one of the account's own
  apartments; an account with no apartment is told to start with a photo. On a card, it gets what
  that card takes, and the card again. A record the account lacks, an unknown field on a record,
  and an unknown field on a dropped photo each name what can be done instead.
- **A dropped card.** A card whose records a correction removed is dropped as soon as it would
  show, with the reason and the next step.
