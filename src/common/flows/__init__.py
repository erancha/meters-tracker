"""The conversation's shared vocabulary and the helpers every flow uses.

A flow is a small state machine persisted as a pending item: {seq, kind, status, step, version,
data}. seq orders the queue of pending items and doubles as the reading's seq once one is saved;
kind names the flow module; status is "reading" while the model reads the photo, "awaiting" while a
card waits for the user, "parked" while a close waits for a missing meter's photo and shows no card,
"standby" while a card waits that shows, and takes typed text, only when no card awaits; step is the
state within the flow; version grows every time the item shows a card or a choice claims it, so a
choice names the exact card it answers; data holds the flow's accumulated answers, enough to render
its card without another store read.

Building blocks:
- Action / Choice / Reply / Snapshot / TurnResult: what a turn takes and returns
- new_item / make_card / ask / drop / decide: a new pending item, card rendering, the pending
  write, reading a choice or text
- tag_photo: the reading field on a photo's message, kept current by ask and drop
- classify_context / request_context: what the classifier is given with an answer to a card,
  and with a request no card takes
- propose / edit_reading: the value a photo proposes and the edits a reading card accepts
- save_reading / summary / price_in / price_on: the reading record, the reply that follows a
  save, and the price it is charged at
- Figures / period_figures: one meter's period figures, shared by the summary and the report
- failed_photos / failure_record: the photos the model failed on, recorded with the reading
- put_period / flagged / move_period: period rows and the reading flags that point at them
- with_apartment / dated_rows: resolving a typed apartment name; card rows with parsed dates
"""

from dataclasses import dataclass
from datetime import timedelta

from common import texts
from common.dates import parse_iso
from common.model import IntentResult
from common.periods import (
    Consumption,
    charge,
    consumption,
    forecast,
    open_period,
    period_readings,
    tariff_on,
)

READING = "reading"
AWAITING = "awaiting"
PARKED = "parked"
STANDBY = "standby"
POSITIONS = ("top", "bottom")
FAILURE_FIELDS = ("failed_photos", "model")
# The flows a meter photo's pending item passes through.
READING_KINDS = ("meter_photo", "link_meter")
PRICES = {"electricity": "electricity_per_kwh", "water": "water_per_m3"}


class AlreadyHandled(Exception):
    """A choice names a card its pending item no longer shows, or an option that card lacks; or
    a retry names a message that is not marked failed."""


class MessageFailed(Exception):
    """Handling a stored text or photo failed, so the message is marked failed and can be
    retried. The original exception is the cause."""


@dataclass
class Choice:
    pending: str
    version: int
    option: str


@dataclass
class Action:
    text: str | None = None
    image: bytes | None = None
    media_type: str | None = None
    note: str | None = None
    choice: Choice | None = None
    retry: str | None = None


@dataclass
class Reply:
    text: str
    card: dict | None = None
    image_key: str | None = None


@dataclass
class Snapshot:
    card: dict | None
    waiting: int
    tariffs_missing: bool


@dataclass
class TurnResult:
    replies: list[Reply]
    snapshot: Snapshot
    # The stored key of the photo the turn sent, so the browser can pair it with its own copy.
    image_key: str | None


def new_item(seq, kind, status, step, data) -> dict:
    return {"seq": seq, "kind": kind, "status": status, "step": step, "version": 0, "data": data}


def make_card(
    item,
    kind,
    text: tuple[str, list[str]],
    options: list[str],
    labels: dict[str, str] | None = None,
) -> dict:
    """labels names the options whose label depends on the item (a date); the rest take their
    fixed label."""
    title, lines = text
    labels = {
        **{o: texts.OPTION_LABELS[o] for o in options if o in texts.OPTION_LABELS},
        **(labels or {}),
    }
    return {
        "kind": kind,
        "pending": item["seq"],
        "version": item["version"],
        "title": title,
        "lines": lines,
        "options": [{"id": option, "label": labels[option]} for option in options],
    }


def card_text(card) -> str:
    return "\n".join([card["title"], *card["lines"]])


def classify_context(card) -> str:
    """The card as the classifier is given it: its text, then its button labels when it has
    any, so a message can name a button."""
    labels = [option["label"] for option in card["options"]]
    context = card_text(card)
    return context + "\nכפתורים: " + " | ".join(labels) if labels else context


def request_context(apartment_names: list[str], standby_card=None) -> str:
    """The context of typed text no awaiting card takes: the standby card when one waits, then
    the account's apartment names, so a request names its apartment exactly as stored."""
    parts = [] if standby_card is None else [classify_context(standby_card)]
    if apartment_names:
        parts.append("דירות: " + " | ".join(apartment_names))
    return "\n".join(parts)


def card_reply(card) -> Reply:
    return Reply(card_text(card), card)


def ask(engine, user, item, step, render) -> list[Reply]:
    """Shows the item's card for step, under a new version."""
    item.update(step=step, status=AWAITING, version=item["version"] + 1)
    engine.store.put_pending(user.email, item)
    tag_photo(engine, user, item, "awaiting")
    return [card_reply(render(item))]


def drop(engine, user, item, replies: list[Reply], state="dropped") -> list[Reply]:
    tag_photo(engine, user, item, state)
    engine.store.delete_pending(user.email, item["seq"])
    return replies


def tag_photo(engine, user, item, state):
    """Records on a meter photo's message what the photo read and where it stands: awaiting,
    saved or dropped. The meter type and the place are None until the flow knows them."""
    if item["kind"] not in READING_KINDS:
        return
    data = item["data"]
    reading = {key: data.get(key) for key in ("meter_type", "apartment", "property")}
    engine.store.set_message_reading(user.email, data["message_at"], {"state": state, **reading})


def decide(engine, card, action) -> tuple[str, IntentResult | None]:
    """Returns the option id of a choice, or the intent name of typed text with its arguments.
    The engine has already checked that a choice's option is on the card."""
    if action.choice is not None:
        return action.choice.option, None
    labels = {option["label"]: option["id"] for option in card["options"]}
    intent = engine.model.classify(action.text, classify_context(card), engine.clock())
    # A typed button label answers as the button does; a label the card lacks is not understood.
    if intent.intent == "choose_option" and intent.name in labels:
        return labels[intent.name], None
    return intent.intent, intent


def not_understood(card, key) -> list[Reply]:
    """The reply to an answer the card cannot take, then the card again: what the card asks when
    the owner asked that and the card has an explanation, else the general help."""
    explain = key == "explain" and card["kind"] in texts.EXPLANATIONS
    text = texts.EXPLANATIONS[card["kind"]] if explain else texts.unknown_intent()
    return [Reply(text), card_reply(card)]


def readable(candidate) -> bool:
    return candidate["value"] is not None and not candidate["unreadable_digits"]


def propose(data, display_choice) -> bool:
    """Sets data's value, position and alternatives from the photo's candidates: the saved
    display position, else both numbers of a two-number display with the top one preselected,
    else the single counter. Returns False when a needed number is unreadable."""
    values = {c["position"]: c["value"] for c in data["candidates"] if readable(c)}
    if display_choice is None:
        wanted = [c["position"] for c in data["candidates"]]
    else:
        wanted = [POSITIONS[display_choice]]
    if not wanted or any(position not in values for position in wanted):
        return False
    if len(wanted) == 1:
        data.update(value=values[wanted[0]], position=wanted[0], alternatives=None)
    else:
        alternatives = {position: values[position] for position in POSITIONS}
        data.update(value=values["top"], position="top", alternatives=alternatives)
    return True


def display_choice(data) -> int | None:
    return POSITIONS.index(data["position"]) if data["position"] in POSITIONS else None


def edit_reading(data, key, intent) -> bool:
    """Applies a reading card's edit (a display position, a typed value, a date); False when key
    is not an edit."""
    if key in POSITIONS:
        data.update(value=data["alternatives"][key], position=key, alternatives=None)
    elif key == "correct_field" and intent.field == "value":
        data.update(value=intent.value, position=None, alternatives=None)
    elif key == "set_date":
        data["reading_date"] = intent_date(intent)
    else:
        return False
    return True


def correct_serial(data, key, intent) -> bool:
    """Applies a typed serial correction; False when key is not one. The serial the model read
    is kept as read_serial, so the saved reading records the misread."""
    if key != "correct_field" or intent.field != "serial":
        return False
    if intent.serial is None:
        raise ValueError("a serial correction came without the serial")
    data.setdefault("read_serial", data["serial"])
    data["serial"] = intent.serial
    return True


def intent_date(intent) -> str:
    if intent.date is None:
        raise ValueError("set_date intent came without a date")
    return intent.date


def reading_text(data):
    return texts.reading_card(
        data["meter_type"],
        data["serial"],
        data["apartment"],
        data["property"],
        data["value"],
        parse_iso(data["reading_date"]),
        data["alternatives"],
        data["position"],
    )


def reading_options(data) -> list[str]:
    edits = [] if data["alternatives"] is None else list(POSITIONS)
    return [*edits, "confirm"]


def by_id(records, record_id) -> dict:
    return {r["id"]: r for r in records}[record_id]


def place(engine, user, apartment_id) -> tuple[dict, dict]:
    apartment = by_id(engine.store.apartments(user.account), apartment_id)
    return apartment, by_id(engine.store.properties(user.account), apartment["property_id"])


def current_period(engine, user, apartment_id) -> dict | None:
    return open_period(engine.store.periods(user.account, apartment_id))


def failed_photos(data) -> list[str]:
    """The photos the model failed on along the way to this reading: the superseded unreadable
    ones, then this one when the confirmed value is not among the values it read or the owner
    corrected its serial."""
    read_values = [c["value"] for c in data["candidates"] if readable(c)]
    serial_corrected = "read_serial" in data and data["read_serial"] != data["serial"]
    if data["value"] in read_values and not serial_corrected:
        return data["failed_photos"]
    return [*data["failed_photos"], data["image_key"]]


def failure_record(engine, data) -> dict:
    """The reading row's failed photos and the model that failed on them, or nothing when the
    model read the reading right from its first photo."""
    failed = failed_photos(data)
    return dict(zip(FAILURE_FIELDS, (failed, engine.model_name))) if failed else {}


def save_reading(engine, user, item, opens=None, closes=None) -> dict:
    data = item["data"]
    reading = {
        "date": data["reading_date"],
        "seq": item["seq"],
        "value": data["value"],
        "image_key": data["image_key"],
        "recorded_by": user.email,
        "opens_period": opens,
        "closes_period": closes,
        "resets": data["resets"],
        **failure_record(engine, data),
    }
    engine.store.put_reading(user.account, data["serial"], reading)
    return reading


def price_in(tariffs, meter_type, d) -> float | None:
    """The price of meter_type in the tariff in force on d; None when no tariff is in force or
    that tariff leaves the type unpriced."""
    tariff = tariff_on(tariffs, d)
    return None if tariff is None else tariff[PRICES[meter_type]]


def price_on(engine, user, meter_type, d) -> float | None:
    return price_in(engine.store.tariffs(user.account), meter_type, d)


@dataclass(frozen=True)
class Figures:
    """One meter's figures for a period. outlook is the period's units: the final count, else the
    forecast, which is None until time has passed since the opening reading."""

    consumption: Consumption
    outlook: float | None
    price: float | None
    amount: float | None


def period_figures(engine, user, meter_type, readings, period) -> Figures | None:
    """The meter's figures for the period, or None when it has no reading there. A final count is
    priced on its last reading's date, a forecast on today."""
    c = consumption(readings, period)
    if c is None:
        return None
    outlook = c.units if c.final else forecast(c, period)
    priced_on = parse_iso(c.last["date"]) if c.final else engine.clock()
    price = price_on(engine, user, meter_type, priced_on)
    amount = None if outlook is None or price is None else charge(outlook, price)
    return Figures(c, outlook, price, amount)


def summary(engine, user, meter, reading) -> Reply:
    apartment, prop = place(engine, user, meter["apartment_id"])
    readings = engine.store.readings(user.account, meter["serial"])
    index = [r["seq"] for r in readings].index(reading["seq"])
    reading = readings[index]
    previous = readings[index - 1] if index > 0 else None
    since_previous = None
    if previous is not None and not reading["resets"]:
        since_previous = reading["value"] - previous["value"]
    periods = engine.store.periods(user.account, meter["apartment_id"])
    containing = [p for p in periods if reading in period_readings(readings, p)]
    period_units = outlook = amount = None
    final = False
    if containing:
        figures = period_figures(engine, user, meter["type"], readings, containing[-1])
        period_units, final = figures.consumption.units, figures.consumption.final
        outlook, amount = figures.outlook, figures.amount
    return Reply(
        texts.summary_after_save(
            prop["name"],
            apartment["name"],
            meter["type"],
            previous,
            reading,
            since_previous,
            period_units,
            outlook,
            final,
            amount,
        )
    )


def next_day(d: str) -> str:
    return (parse_iso(d) + timedelta(days=1)).isoformat()


def put_period(engine, user, apartment_id, start, months):
    engine.store.put_period(
        user.account,
        apartment_id,
        {"start": start, "months": months, "closed_on": None, "last_reminded_on": None},
    )


def flagged(engine, user, apartment_id, flag, start) -> list[tuple[dict, dict]]:
    """(meter, reading) pairs whose flag ("opens_period" or "closes_period") names the period
    starting on start, over every meter the apartment ever had, retired ones included."""
    return [
        (meter, r)
        for meter in engine.store.meters(user.account)
        if meter["apartment_id"] == apartment_id
        for r in engine.store.readings(user.account, meter["serial"])
        if r[flag] == start
    ]


def move_period(engine, user, apartment_id, period, start, months):
    """Re-keys a period to a new start and points its opening readings at it."""
    if start == period["start"]:
        raise ValueError(f"period {start} is already at that start")
    engine.store.put_period(
        user.account, apartment_id, {**period, "start": start, "months": months}
    )
    for meter, r in flagged(engine, user, apartment_id, "opens_period", period["start"]):
        engine.store.update_reading(
            user.account, meter["serial"], r["date"], r["seq"], opens_period=start
        )
    engine.store.delete_period(user.account, apartment_id, period["start"])


def with_apartment(engine, user, name, act) -> list[Reply]:
    apartments = engine.store.apartments(user.account)
    matches = apartments if name is None else [a for a in apartments if a["name"] == name]
    if len(matches) == 1:
        return act(matches[0])
    if not matches:
        return [Reply(texts.no_apartments() if name is None else texts.unknown_apartment(name))]
    return [Reply(texts.which_apartment([a["name"] for a in matches]))]


def dated_rows(rows) -> list[tuple]:
    """(type, serial, value, ISO date) rows as stored in an item, with the date parsed."""
    return [(t, serial, value, parse_iso(d)) for t, serial, value, d in rows]
