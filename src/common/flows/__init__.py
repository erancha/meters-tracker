"""The conversation's shared vocabulary and the helpers every flow uses.

A flow is a small state machine persisted as a pending item: {seq, kind, status, step, version,
data}. seq orders the queue of pending items and doubles as the reading's seq once one is saved;
kind names the flow module; status is "reading" while the model reads the photo, "awaiting" while a
card waits for the user, "parked" while a close waits for a missing meter's photo and shows no card,
"standby" while a card waits that shows, and takes typed text, only when no card awaits; step is the
state within the flow; version grows every time the item shows a card or a choice claims it, so a
choice names the exact card it answers; data holds the flow's accumulated answers, enough to render
its card without another store read.

Every flow module offers form(item), the fields a typed edit may change with their current values,
in the order edits apply, None for a field not answered yet, and default_field(item), the field a
bare input fills, or None when there is none. A flow with fields also has edit(engine, user, item,
field, value), which changes one of them, and after_edit(engine, user, item), which shows the card
the edits lead to. A question card is a form whose asked field is empty, so its typed answer is an
edit too. A card that asked for one of its fields again, after a missing or unfitting value, holds
that field as asked until it is shown anew or a refresh moves it to another step, and a bare input
fills it in place of the default.

An item holds copies of saved records, which go stale when a correction lands, so every flow module
also offers refresh(engine, user, item), which re-reads them into the item in memory and may move
it to another step or kind. It raises CardGone when no card can stand on the records any more.

Building blocks:
- Action / Choice / Reply / Snapshot / TurnResult: what a turn takes and returns
- new_item / make_card / ask / drop / decide: a new pending item, card rendering, the pending
  write, reading a choice or text
- tag_photo: the reading field on a photo's message, kept current by ask and drop
- classify_context / form_context / message_context: what the classifier is given with typed text
  (a card's text, fields, asked field and buttons, then the account's records)
- ask_back / not_understood: the reply to a message that is no change the app can make, with no
  card or on one; never a bare "not understood", always what can be done or a question
- on_card / record_operation / apply_edits: what tells a card's operation from a saved record's,
  and the one path of a typed edit on a card, whatever the flow and the step
- propose / edit_read / choose_position: the value a photo proposes and the changes to what the
  photo read
- save_reading / summary / price_on: the reading record, the reply that follows a
  save, and the price it is charged at
- period_figures: one meter's period figures at the engine's clock, shared by the summary and
  the report
- add_failed_photos / failed_photos / failure_record: the photos the model failed on, each once,
  recorded with the reading
- put_period: a new period's row
- known_meter / live_apartment: the records a refresh re-reads a card from
- with_apartment / dated_rows: resolving a typed apartment name; card rows with parsed dates
"""

from dataclasses import dataclass

from common import account, corrections, texts
from common.dates import parse_iso
from common.fields import (
    DISPLAY_POSITIONS,
    FIELDS,
    InvalidValue,
    UnknownField,
    UnknownTarget,
    field_for,
    parse,
)
from common.model import IntentResult
from common.periods import (
    Figures,
    consumption,
    figures,
    new_period,
    open_period,
    period_readings,
    price_in,
)

READING = "reading"
AWAITING = "awaiting"
PARKED = "parked"
STANDBY = "standby"
POSITIONS = DISPLAY_POSITIONS
FAILURE_FIELDS = ("failed_photos", "model")
# The flows a meter photo's pending item passes through.
READING_KINDS = ("meter_photo", "link_meter")
# The target of an edit on the card on screen.
CARD_TARGET = "card"
# The item key holding the field a card asked for again; absent while it asks for none.
ASKED = "asked"
# The kind of the question flow's item, which a newer card replaces.
QUESTION = "question"
# The intents answered the same on any card or none, since none of them changes anything.
ASK_BACK_INTENTS = ("how_to", "which", "not_offered")


class AlreadyHandled(Exception):
    """A choice names a card its pending item no longer shows, or an option that card lacks; or
    a retry names a message that is not marked failed."""


class MessageFailed(Exception):
    """Handling a stored text or photo failed, so the message is marked failed and can be
    retried. The original exception is the cause."""


class CardGone(Exception):
    """A waiting card's records can no longer carry it; text tells the person why."""

    def __init__(self, text: str):
        super().__init__(text)
        self.text = text


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
    # What the model made of text; the engine sets it before a flow sees the action.
    intent: IntentResult | None = None


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


def form_context(form: dict) -> str:
    """A card's fields as the classifier is given them, one per line under their title, each by
    the name an edit gives it."""
    lines = [f"{name}: {texts.EMPTY_VALUE if v is None else v}" for name, v in form.items()]
    return "\n".join([f"{texts.FORM_TITLE}:", *lines])


def asked_field(item) -> str | None:
    return item.get(ASKED)


def classify_context(card, form: dict, asked: str | None = None) -> str:
    """The card as the classifier is given it: its text, its fields when it has any, so an edit
    can name one, and the field it asked for again, then its button labels when it has any, so a
    message can name a button."""
    parts = [card_text(card)]
    if form:
        parts.append(form_context(form))
    if asked is not None:
        parts.append(f"{texts.ASKED_TITLE}: {asked}")
    labels = [option["label"] for option in card["options"]]
    if labels:
        parts.append(f"{texts.BUTTONS_TITLE}: " + " | ".join(labels))
    return "\n".join(parts)


def message_context(records: str, card_context: str | None = None) -> str:
    """The context of typed text: the waiting card's classify_context when a card takes the text,
    then the account's records block, so a change or a request can name a saved record exactly."""
    parts = [] if card_context is None else [card_context]
    if records:
        parts.append(records)
    return "\n".join(parts)


def card_reply(card) -> Reply:
    return Reply(card_text(card), card)


def ask(engine, user, item, step, render) -> list[Reply]:
    """Shows the item's card for step, under a new version, which asks for no field. An item's
    first card drops a question still waiting, since the person has moved on to something newer
    and the question's answer must not take text meant for the new card."""
    if item["version"] == 0 and item["kind"] != QUESTION:
        for waiting in engine.store.pending(user.email):
            if waiting["kind"] == QUESTION:
                drop(engine, user, waiting, [])
    item.update(step=step, status=AWAITING, version=item["version"] + 1)
    item.pop(ASKED, None)
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


def decide(engine, item, action) -> tuple[str, IntentResult | None]:
    """Returns the option id of a choice, or the intent name of typed text with its arguments.
    The engine has already checked that a choice's option is on the card."""
    if action.choice is not None:
        return action.choice.option, None
    intent = action.intent
    card = engine.flow(item).card(item)
    labels = {option["label"]: option["id"] for option in card["options"]}
    # A typed button label answers as the button does; a label the card lacks gets what the
    # card takes.
    if intent.intent == "choose_option" and intent.name in labels:
        return labels[intent.name], None
    return intent.intent, intent


def not_understood(card, key) -> list[Reply]:
    """The reply to an answer the card cannot take, then the card again: what the card asks when
    the owner asked that and the card has an explanation, else what the card takes."""
    explain = key == "explain" and card["kind"] in texts.EXPLANATIONS
    labels = [option["label"] for option in card["options"]]
    text = texts.EXPLANATIONS[card["kind"]] if explain else texts.card_help(labels)
    return [Reply(text), card_reply(card)]


def _how_to(operations) -> str | None:
    """The how-to text of the field the first operation names, or None when it names none."""
    if not operations:
        return None
    key = (operations[0].target.partition("|")[0], operations[0].field)
    return FIELDS[key].how_to if key in FIELDS else None


def _which(engine, user, operations, names) -> str:
    state = account.load(engine.store, user.account, user.role)
    try:
        labels = [corrections.label(state, operation.target) for operation in operations]
    except UnknownTarget:
        return texts.unknown_record(names)
    if not labels:
        return texts.options_reply(names)
    return texts.which_record(list(dict.fromkeys(labels)))


def ask_back(engine, user, intent) -> list[Reply]:
    """The reply to a message that is no change the app can make: a how-to question gets the
    field's how-to text, a message fitting several records lists them, a request the app lacks
    says so, a bare value with no card to take it is asked about, and anything else gets what the
    assistant can do, with the account's own apartment names. Nothing is written."""
    names = [a["name"] for a in engine.store.apartments(user.account)]
    if intent.intent == "how_to":
        how_to = _how_to(intent.operations)
        return [Reply(texts.options_reply(names) if how_to is None else texts.how_to_reply(how_to))]
    if intent.intent == "which":
        return [Reply(_which(engine, user, intent.operations, names))]
    if intent.intent == "not_offered":
        return [Reply(texts.not_offered(names))]
    values = [o.value for o in intent.operations if on_card(o) and o.value is not None]
    if intent.intent == "edit" and values:
        return [Reply(texts.bare_input(values[0]))]
    return [Reply(texts.options_reply(names))]


def on_card(operation) -> bool:
    """True for an operation on a waiting card, as opposed to a saved record."""
    return operation.target.partition("|")[0] == CARD_TARGET


def record_operation(operation, form: dict):
    """The operation with a form field that is a record's id (a close card lists its closing
    readings so) aimed at that record's value, which is what setting such a field means. Any other
    operation is returned as it is."""
    if operation.target == CARD_TARGET and operation.field in form and "|" in operation.field:
        return operation.model_copy(update={"target": operation.field, "field": "value"})
    return operation


def _typed_edit(flow, item, form, operation, today) -> tuple[str, object]:
    """The (field name, typed value) of a set on the card on screen. A field the operation leaves
    out is the card's default field. Raises UnknownField for anything else the card cannot take,
    and InvalidValue for a value that does not fit the field."""
    if operation.action != "set" or operation.target != CARD_TARGET:
        raise UnknownField(f"{operation.action} on {operation.target}")
    name = operation.field
    if name is None:
        asked = asked_field(item)
        name = flow.default_field(item) if asked is None else asked
    if name not in form:
        raise UnknownField(name)
    return name, parse(field_for("card", name), operation.value, today)


def apply_edits(engine, user, item, operations) -> list[Reply]:
    """Applies a message's operations to the item and shows the card its flow arrives at. Every
    operation is checked before the first is applied, so an edit the card cannot take leaves the
    item as it was and shows the card again: with the field's question when the value is missing
    or invalid, and the card then holds that field as asked, else with what the card takes. Edits
    apply in the order of the form's fields, so a message means one thing whatever order the model
    lists its operations in."""
    flow = engine.flow(item)
    card = flow.card(item)
    form = flow.form(item)
    if not operations:
        return not_understood(card, "edit")
    edits = []
    for operation in operations:
        try:
            edits.append(_typed_edit(flow, item, form, operation, engine.clock()))
        except UnknownField:
            return not_understood(card, "edit")
        except InvalidValue as error:
            item[ASKED] = error.field.name
            engine.store.put_pending(user.email, item)
            current = texts.field_value(error.field.kind, form[error.field.name])
            return [Reply(error.field.question.format(current=current)), card_reply(card)]
    fields = list(form)
    for name, value in sorted(edits, key=lambda edit: fields.index(edit[0])):
        flow.edit(engine, user, item, name, value)
    return flow.after_edit(engine, user, item)


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


def edit_read(data, field, value):
    """Changes a field the photo read. A typed value replaces the numbers the display offered. A
    typed value, date or serial takes back a reset chosen for the earlier reading, since the
    lower-reading check runs again on the changed reading. A typed serial settles the serial, and
    the one the model read is kept as read_serial, so the saved reading records the misread."""
    if field == "value":
        data.update(value=value, position=None, alternatives=None, resets=False)
    elif field == "date":
        data.update(reading_date=value, resets=False)
    elif field == "serial":
        data.setdefault("read_serial", data["serial"])
        data.update(serial=value, serial_unreadable=False, resets=False)
    else:
        data["meter_type"] = value


def choose_position(data, position):
    """The alternatives stay so the other number can still be pressed; position marks the choice."""
    data.update(value=data["alternatives"][position], position=position)


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


def live_apartment(engine, user, apartment_id) -> dict:
    """The apartment a card names. Raises CardGone when a correction deleted it."""
    apartments = {a["id"]: a for a in engine.store.apartments(user.account)}
    if apartment_id not in apartments:
        raise CardGone(texts.card_gone())
    return apartments[apartment_id]


def known_meter(engine, user, data) -> dict | None:
    """The meter data's serial names, setting that serial to the meter's current one when a
    correction renamed it; None when no meter holds or held it. The serial the model read stays as
    read_serial, as a typed serial keeps it, so the saved reading counts the photo as misread. A
    row has former_serials only once it was renamed. Should two meters have held the serial, the
    card shows the first one's, so the person sees which it took."""
    meter = engine.store.meter(user.account, data["serial"])
    if meter is None:
        renamed = (
            m
            for m in engine.store.meters(user.account)
            if data["serial"] in m.get("former_serials", ())
        )
        meter = next(renamed, None)
        if meter is not None:
            data.setdefault("read_serial", data["serial"])
            data["serial"] = meter["serial"]
    return meter


def current_period(engine, user, apartment_id) -> dict | None:
    return open_period(engine.store.periods(user.account, apartment_id))


def add_failed_photos(data, keys):
    """Adds to the photos the model failed on each key not listed yet, so each is listed once."""
    for key in keys:
        if key not in data["failed_photos"]:
            data["failed_photos"].append(key)


def failed_photos(data) -> list[str]:
    """The photos the model failed on along the way to this reading: the superseded unreadable
    ones, then this one when the confirmed value is not among the values it read or the owner
    corrected its serial. A reopened photo the model read no meter type in is listed already."""
    read_values = [c["value"] for c in data["candidates"] if readable(c)]
    serial_corrected = "read_serial" in data and data["read_serial"] != data["serial"]
    listed = data["image_key"] in data["failed_photos"]
    if listed or (data["value"] in read_values and not serial_corrected):
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


def price_on(engine, user, meter_type, d) -> float | None:
    return price_in(engine.store.tariffs(user.account), meter_type, d)


def period_figures(engine, user, meter_type, readings, period) -> Figures | None:
    if consumption(readings, period) is None:
        return None
    return figures(meter_type, readings, period, engine.store.tariffs(user.account), engine.clock())


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


def put_period(engine, user, apartment_id, start, months):
    engine.store.put_period(user.account, apartment_id, new_period(start, months))


def with_apartment(engine, user, name, act) -> list[Reply]:
    apartments = engine.store.apartments(user.account)
    if not apartments:
        return [Reply(texts.no_apartments())]
    matches = apartments if name is None else [a for a in apartments if a["name"] == name]
    if len(matches) == 1:
        return act(matches[0])
    if not matches:
        return [Reply(texts.unknown_apartment(name, [a["name"] for a in apartments]))]
    return [Reply(texts.which_apartment([a["name"] for a in matches]))]


def dated_rows(rows) -> list[tuple]:
    """(type, serial, value, ISO date) rows as stored in an item, with the date parsed."""
    return [(t, serial, value, parse_iso(d)) for t, serial, value, d in rows]
