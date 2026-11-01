# Domain rules

This document details the conversation engine that [Architecture](architecture.md) summarizes:
the lifecycle of meters, readings and billing periods, and the arithmetic of consumption,
forecast and charge, with the exact values the code uses. The arithmetic lives in
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
| `sidebar_min_width_px`: 900 | `config/app.json` | Narrowest window with the planned sidebar |
| 4 MiB, JPEG or PNG | `src/handlers/api.py` | Largest accepted photo, after base64 decoding |
| 200 | `src/common/store.py` | Messages `GET /chat` returns |
| Asia/Jerusalem | `src/common/dates.py` | The zone of every date and of "today" |

## A meter photo

Each photo is its own turn. The model returns the meter type, the serial, every counter value it
sees with its position on the display (`top` and `bottom` on a two-number electricity display,
`single` on a water counter), whether any digit is unreadable, and any handwritten label. A note
sent with the photo that names a date sets the reading date; otherwise the reading is dated
the day the photo was sent, which a retry on a later day keeps.

- **Statement.** A photo with no known meter type and no serial is read again as a previous
  statement; a statement becomes a tariffs card (see [Tariffs](#tariffs)), dated like a reading.
- **Not a meter.** Any other photo of an unknown type and an unknown serial is answered as
  "no meter found" and nothing is kept.
- **Unreadable.** A missing or unreadable serial asks for a retake. An unreadable value asks for
  a retake or a typed value. A new photo replaces an earlier unreadable card that had no serial
  or had the same serial.
- **Retired meter.** A photo of a retired meter is refused, naming the live meter that replaced
  it.
- **Two-number display.** With no saved choice, the card offers both numbers with the top one
  selected. The position of the first saved reading becomes the meter's display choice, and
  later photos propose that position's number directly.
- **Unknown serial.** A link flow (`src/common/flows/link_meter.py`) asks for the property and then
  the apartment, existing or new by name; an answer naming both ("לאונר 18, דירה קדמית") skips the
  apartment question. An apartment with no open period also asks for the period's start (buttons for
  today and for the first of next month, which is left out on the first of a month; a typed date
  also answers) and length (default `default_period_months`, or the apartment's own length when it
  exists). A new period that starts before the photo's date dates the opening reading at the start,
  since the photo then stands for the meter on that day; a later start keeps the photo's date. The
  final card shows everything that will be created; on confirmation the property, apartment and
  period are created as needed, the meter is saved, and its reading becomes the period's opening
  reading. A live meter of the same type in that apartment is retired, dated the new reading's date;
  each keeps its own consumption ([open item](architecture.md#open-items)). When another photo of
  the same serial was linked while this one waited, the next answer turns it into an ordinary
  reading card for that meter.
- **Known serial.** The card proposes the reading; the user confirms, picks the other number,
  or types a corrected value, a date, or a cancel. Cards carry no reject button: corrections and
  cancels are typed.
- **Corrected serial.** A typed serial, whole or as a few corrected digits, replaces the one the
  model read on a reading or link card. The photo then goes to that serial's meter: a reading
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
   there is one, moves to start the day after. Declining saves an ordinary reading.
5. **Period end near or reached.** With no close already waiting, a reading dated on or after the
   open period's last day, or at most `close_days_before_end` days before it, offers to close the
   period with it or keep it as an ordinary reading; typing that the apartment is empty
   (`vacate`) closes the period with it and marks the apartment vacant.

A meter's first saved reading in the open period becomes that period's opening reading for the
meter. After a save that has a previous reading, the reply shows it, the consumption since it
(or that the meter was reset), the period's consumption so far, the forecast or final figure,
and the charge when a tariff is in force.

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
  a typed vacate) closes at once, since the owner has just confirmed the reading; any other
  close, including a parked one resumed, shows a card listing every closing reading, and if the
  readings change before it is answered, the card is shown again with the new ones.
- **On closing** each closing reading is flagged as closing the period, `closed_on` is the
  latest closing reading's date, and the next period opens the day after with the same length,
  the closing readings flagged as its opening readings. **Vacate** closes without opening a next
  period; the apartment's next photo offers a new one (check 3). The reply lists the closing
  readings.
- **Change.** Typed text naming a start date, a length or both (`change_period`) shows a card
  with the effect. On an open period it changes the length in place or moves the start, and
  moving the start re-points the period's opening readings to the new start. On a vacant
  apartment it opens a period, from today unless a date is given.
- **Welcome.** The first `GET /chat` of an account with no messages adds a welcome that says to
  start with a photo of a water or electricity meter.
- **Greeting.** A greeting appears only when an apartment's period is past its end: the open period
  ended before today while a live meter has no reading dated on or after its last day. Then the
  first `GET /chat` of a day that has no message yet adds a greeting naming every such
  apartment; with none, it adds nothing.

## Consumption, forecast and charge

- **Consumption** of a meter in a period runs from its opening reading to its closing reading,
  or to its latest reading while the period is open. A reading flagged as a reset ends one
  stretch at the reading before it and starts the next at itself; the period's units are the
  sum of the stretches. Consumption is per meter, so a replaced meter and its successor are
  reported separately ([open item](architecture.md#open-items)).
- **Final** when the meter's last reading in the period is its closing reading; the outlook is
  then the consumption itself.
- **Forecast** for an open period: units ÷ days from the opening reading to the latest reading
  × the period's days, counting both its first and last day. There is no forecast while the
  latest reading is on the opening reading's date. Replies round the outlook to whole units.
- **Charge** is the outlook × the price for the meter's type, rounded to 2 decimal places. The
  price comes from the tariff with the latest effective date on or before the closing reading's
  date when final, or on or before today for a forecast. With no tariff in force on that date
  the reply says so and shows no amount; no later or guessed tariff is used. A tariff that
  leaves a type unpriced counts as no tariff for that type.

## Tariffs

The tariffs flow (`src/common/flows/tariffs.py`) writes a tariff row only from its confirmed
card, which shows the electricity price per kWh, the water price per cubic meter, a VAT note and
the effective date.

- **Typed.** Typed prices (`set_tariffs`) with no card awaiting show the card, effective today.
  On the card, typed prices replace the ones shown, a typed date moves the effective date, and
  a cancel drops it.
- **Statement photo.** The card is titled from the statement: the property, apartment and
  period end it names, the VAT-inclusive prices, and each tariff line it does not save (such as
  a lower water tariff for yard maintenance). The row keeps the photo's key. Prices that exclude
  VAT are shown with no save button until prices are typed.
- **Carry-forward.** A price not given is the one in force on the effective date, recomputed
  when the date changes. With none in force the type is saved unpriced (null).
- **Asking.** A close whose closing readings have no price in force is followed by a
  card that asks for tariffs, with no buttons, effective from the earliest such closing date;
  the tariffs card then names that date as the period's closing date until a typed date
  replaces it. The request never blocks other cards: it shows only when no other card awaits,
  and typed text reaches it only when no other card awaits and the text is prices (moving it to
  the tariffs card), a cancel (dropping it) or a question; other typed requests pass it by. An
  ordinary save without a tariff only says there is no charge.
- **One at a time.** A statement photo replaces whichever tariffs card waits, the request or a
  tariffs card, so at most one tariffs card waits at a time.
- **Saving** writes the row under its effective date, replacing a row of the same date, and the
  reply lists the charge of each closed period whose closing reading the new row prices for the
  first time, per meter.
