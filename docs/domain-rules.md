# Domain rules

This document details the conversation engine that [Architecture](architecture.md) summarizes:
the lifecycle of meters, readings and billing periods, and the arithmetic of consumption and
charge, with the exact values the code uses. The arithmetic lives in
`src/common/periods.py`, which does no I/O; the lifecycles live in the flows under
`src/common/flows/`.

## Terms

- An **account** is one building owner's data: properties, apartments, meters, readings,
  periods and tariffs. Its id is the owner's lowercase Google address.
- A **property** is a building or address; an **apartment** belongs to one property and is the
  unit that is billed. Both have free-text names, matched exactly.
- A **meter** is a water or electricity meter, identified by the serial printed on it, and
  belongs to one apartment. A meter is **live** until a replacement retires it.
- A **reading** is a meter's value on a date, saved only after the user confirms it.
- A **period** is an apartment's billing window: a start date and a length in whole months,
  shared by all of the apartment's meters ([ADR 0003](adr/0003-period-belongs-to-apartment.md)).
  At most one period of an apartment is open at a time.
- A **tariff** is a pair of VAT-inclusive prices, per kWh and per cubic meter, in force from its
  effective date.

## Values

| Value | Where | Used for |
|---|---|---|
| `default_period_months`: 2 | `config/app.json` | Length of the period a new apartment gets |
| `reclose_days`: 7 | `config/app.json` | Window after a closing reading that offers a re-close |
| `close_days_before_end`: 7 | `config/app.json` | Days before a period's end that may close it |
| `file_url_minutes`: 10 | `config/app.json` | Lifetime of a photo's presigned URL |
| `sidebar_min_width_px`: 900 | `config/app.json` | Narrowest window with the day panel |
| `correction_recent_readings`: 12 | `config/app.json` | Readings per meter the classifier gets |
| `correction_dropped_photos`: 5 | `config/app.json` | Latest dropped photos the classifier gets |
| 100 | `src/common/store.py` | Most rows one correction writes |
| 4 MiB, JPEG or PNG | `src/handlers/api.py` | Largest accepted photo, after base64 decoding |
| 200 | `src/common/store.py` | Messages `GET /chat` returns |
| Asia/Jerusalem | `src/common/dates.py` | The zone of every date and of "today" |

## A meter photo

Each photo is its own turn. The model returns the meter type, the serial, every counter value it
sees with its position on the display (`top` and `bottom` on a two-number electricity display,
`single` on a water counter), whether any digit is unreadable, and the text handwritten beside
the meter as written, or none when it cannot be read; owners write the apartment's name there. A
note sent with the photo that names a date sets the reading date; otherwise the reading is dated
the day the photo was sent, which a retry on a later day keeps.

- **Statement.** A photo with no known meter type and no serial is read again as a previous
  statement; a statement becomes a tariffs card (see [Tariffs](#tariffs)), dated like a reading.
- **Not a meter.** Any other photo of an unknown type and an unknown serial is answered as
  "no meter found" and nothing is kept.
- **Unreadable.** A missing or unreadable serial asks for a retake. An unreadable value asks for
  a retake or a typed value. A new photo replaces an earlier unreadable card that had no serial
  or had the same serial.
- **Retired meter.** A photo of a retired meter is refused, naming the live meter that replaced
  it, when its apartment has one of its type.
- **Two-number display.** With no saved choice, the card offers both numbers as its only
  buttons, the top one marked as the default, and pressing either saves the reading with that
  number. A display showing the same number twice gets a one-number card with confirm, and its
  reading leaves the choice unsaved. The position of the first saved reading chosen from two
  numbers becomes the meter's display choice, and later photos propose that position's number
  directly.
- **Unknown serial.** A link flow (`src/common/flows/link_meter.py`) asks for the property and then
  the apartment, existing or new by name; an answer naming both ("לאונר 18, דירה קדמית") skips the
  apartment question; a property or apartment name typed on a later question corrects the one given,
  forgets the period answered for it, and the flow resumes from the corrected name. An apartment
  with no open period also asks for the period's start (buttons for today and for the first of next
  month, which is left out on the first of a month; a typed date also answers) and length (default
  `default_period_months`, or the apartment's own length when it exists). A new period that starts
  before the photo's date dates the opening reading at the start, since the photo then stands for
  the meter on that day; a later start keeps the photo's date, and a date equal to the start shows
  once, on the opening line. The questions offer the existing names as buttons; the apartment
  question also offers the handwritten label as a new apartment's name, highlighted, unless it
  already names one of the property's apartments. A typed name that is not an existing one but is
  a prefix of one, or within two single-letter edits of one, asks whether that name was meant; a
  name chosen as new is not asked about again. A reading dated after the last day of the period
  it would open is refused on the summary, which asks for its date. A
  reading dated inside a closed period, before its close, stands for the meter on the next period
  until a later reading of the meter inside the closed period arrives; that reading's save flags
  the meter's readings from the period dates as the close would have, so the closed period gets the
  meter's opening and closing readings and is marked for its report. The final card shows
  everything that will be created; on confirmation the property, apartment and period are created
  as needed, the meter is saved, and its reading becomes the period's opening reading. A live
  meter of the same type in that apartment is retired, dated the new reading's date; each keeps
  its own consumption. When another photo of the same serial was linked while this one waited, it
  shows next as an ordinary reading card for that meter.
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
  that scores another model on a reading's failed photos ([Tests](development.md#tests)) may then
  mark such a photo "serial NO" for every model. The reading row keeps the failed photos' keys, in
  the order they were sent, and the `LLM_MODEL` value of the model that failed on them. A replacing
  duplicate reading whose new photo was read right removes the keys and the model from the replaced
  row.

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
- **Checks run again.** A value, date or serial changed on a check card (see below) returns to the
  reading card, so the checks run again on confirmation, and takes back a reset chosen on the
  earlier reading.
- **Refused edits.** One message may change several fields. Every change is checked first, so an
  edit the card cannot take changes nothing and the card is shown again. A missing or unfitting
  value is answered with the field's question naming its current value, and the card then holds
  that field as asked: the next bare input fills it, not the default field, until the card is shown
  anew or moves to another step. Any other edit the card cannot take is answered with what the
  card takes (its buttons, a typed correction of any detail on it, or a cancel).

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
`src/common/texts.py`, chosen by the intent and the field table; the model never writes it.

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
- **A request the app does not offer**, such as adding a delegate, says so, then what it can do.
- **Anything else** gets what the assistant can do, with an example naming one of the account's own
  apartments; an account with no apartment is told to start with a photo. On a card, it gets what
  that card takes, and the card again. A record the account lacks, an unknown field on a record,
  and an unknown field on a dropped photo each name what can be done instead.
- **A dropped card.** A card whose records a correction removed is dropped as soon as it would
  show, with the reason and the next step.

## Checks before a reading is saved

Run in this order on confirmation, each stopping at its own card:

1. **Same date.** The meter already has a reading on that date: confirming replaces its value
   and photo.
2. **Lower than before.** The value is below the meter's latest earlier reading. The user picks
   misread (retake or type the value), replaced (refused: a replaced meter has a new serial, so
   the new one is photographed instead), or reset (the reading is saved flagged as a reset).
3. **No open period.** The apartment is vacant: confirming opens a period starting on the
   reading's date, with the apartment's length, and the reading opens it.
4. **Near the latest closing reading** of this meter:
   - dated before it, yet on or after the closed period's end — a **swap** is offered;
   - dated after it by at most `reclose_days` days — a **re-close** is offered.

   Either way, accepting moves both of the old reading's period flags to the new reading and
   sets the closed period's `closed_on` to its latest closing reading's date. A next period, when
   there is one, moves to start the day after, as a close opens it. Declining saves an ordinary
   reading.
5. **Period end near or reached.** With no close already waiting, a reading dated on or after the
   open period's last day, or at most `close_days_before_end` days before it, offers to close the
   period with it or keep it as an ordinary reading; typing that the apartment is empty
   (`vacate`) closes the period with it and marks the apartment vacant.

A meter's first saved reading in the open period becomes that period's opening reading for the
meter. After a save that has a previous reading, the reply shows it, the consumption since it
(or that the meter was reset), the period's consumption, final or so far, and the charge when a
tariff is in force. The reply to a reading that closes a period is built after the close, so it
reports the closed period.

## Period lifecycle

- **End.** A period's last day is the day before the same day-of-month `months` later, with the
  day clamped to that month's length: a period from 2026-11-01 of 2 months ends 2026-12-31.
- **Close.** Started by a reading at the end (above), by typed text such as "close the period"
  (`close_period`), or by "the apartment is empty" (`vacate`). The closing reading of each live
  meter is its latest reading after its opening reading in the period. While any live meter has
  none, the close is parked: no card shows, the reply names the missing meters, and the
  apartment's next ordinary saved reading resumes it. Asking to close that apartment again
  shows the parked close as a card whose only option cancels it; the next photo parks it again,
  so the photo's own card comes first. A close chosen on a reading's card (the close option, or
  a typed vacate) closes at once, since the owner has just confirmed the reading, or, when parked,
  once its last missing reading is saved; any other close shows a card listing every closing
  reading, and if the readings change before it is answered, the card is shown again with the new
  ones. The card offers confirm alone, or vacate alone for a typed vacate; a typed vacate on a
  confirm card closes without a next period.
- **On closing** each closing reading is flagged as closing the period, `closed_on` is the
  latest closing reading's date, and the next period opens the day after with the same length,
  the closing readings flagged as its opening readings. A close always opens the next period the
  day after; a correction may also start it on the closing day itself (see Warn or refuse).
  **Vacate** closes without opening a next period; the apartment's next photo offers a new one
  (check 3). The reply lists the closing readings.
- **Change.** Typed text naming a start date, a length or both (`change_period`) shows a card
  with the effect. On an open period that card is a correction: it changes the length in place or
  moves the start, and moving the start re-points the readings that open or close the period. On a
  vacant apartment it opens a period, from today unless a date is given. A new length is the
  apartment's too, written in one transaction with the period.
- **Welcome.** The first `GET /chat` of an account with no messages adds a welcome that says to
  start with a photo of a water or electricity meter.
- **Greeting.** A greeting appears only when an apartment's period is past its end: the open period
  ended before today while a live meter has no reading dated on or after its last day. Then the
  first `GET /chat` of a day that has no message yet adds a greeting naming every such
  apartment; with none, it adds nothing.

## Consumption and charge

- **Consumption** of a meter in a period runs from its opening reading to its closing reading,
  or to its latest reading while the period is open. A reading flagged as a reset ends one
  stretch at the reading before it and starts the next at itself; the period's units are the
  sum of the stretches. Consumption is per meter, so a replaced meter and its successor are
  reported separately.
- **Final** when the meter's last reading in the period is its closing reading; while the period
  is open, the consumption is the count so far.
- **Charge** is the consumption × the price for the meter's type, rounded to 2 decimal places.
  The price comes from the tariff with the latest effective date on or before the closing
  reading's date when final, or on or before today while the period is open. With no tariff in
  force on that date the reply says so and shows no amount; no later or guessed tariff is used.
  A tariff that leaves a type unpriced counts as no tariff for that type.

## Period report

- **When.** Once per period, when its bill is complete: the period is closed, the apartment has a
  live electricity meter and a live water meter, each with its closing reading for the period,
  and each type has a price in force on the closing date. The step that completes it sends the
  report: the close, or saving the tariff that prices the closed period for the first time. A
  reading in an open period, a correction, and a tariff that reprices an already priced period
  send nothing. A period that lacks any of these sends nothing, and the turn that marked it adds
  a line naming what it lacks. An apartment with only one type of meter gets no report. A late
  meter's reading that closes a closed period for it marks the period too.
- **Outcome.** The turn that sends a report adds a line naming the address it went to. A failed
  send adds a line saying so, in the owner's chat as well when a delegate caused it, marks the
  period, and the first chat load of a later day sends the report again until it goes out.
- **Figures.** Each live meter shows its opening reading, its closing reading, its units, its
  price and its cost, all as in the section above.
- **Total.** The sum of the costs.
- **Re-close.** It sends the corrected report, which names the one to ignore by the period's
  closing date before the move, the date that report carried.

## Tariffs

The tariffs flow (`src/common/flows/tariffs.py`) writes a tariff row only from its confirmed
card, which shows the electricity price per kWh, the water price per cubic meter, a VAT note and
the effective date.

- **Typed.** Typed prices (`set_tariffs`) with no card waiting show the card, effective today.
  On the card, typed prices replace the ones shown, a typed date moves the effective date, and
  a cancel drops it.
- **Statement photo.** The card shows the VAT-inclusive prices billed for the apartment's own
  meters and no other detail of the statement, since tariffs belong to the account rather than
  to one apartment. The row keeps the photo's key. Prices that exclude VAT are shown with no save
  button until prices are typed.
- **Carry-forward.** A price not given is the one in force on the effective date, recomputed
  when the date changes. With none in force the type is saved unpriced (null).
- **Asking.** A close whose closing readings have no price in force is followed by a
  card that asks for tariffs, with no buttons, effective from the earliest such closing date;
  the tariffs card then names that date as the period's closing date until a typed date
  replaces it. The request never blocks other cards: it shows only when no other card awaits,
  and typed text reaches it only when no other card awaits and the text is prices or a date
  (moving it to the tariffs card), a cancel (dropping it) or a question; other typed requests pass
  it by. An ordinary save without a tariff only says there is no charge.
- **One at a time.** A statement photo replaces whichever tariffs card waits, the request or a
  tariffs card, so at most one tariffs card waits at a time.
- **Saving** writes the row under its effective date, replacing a row of the same date, and the
  reply lists each period, closed or open, whose charge the row changes, per meter, with its
  units and its charge now. The earlier charge is left out: a tariff changes only the charge on
  units the line already names, and a correction's card is where figures stand beside the ones
  they replace.
