"""New-meter flow: a photo of an unknown serial is linked to an apartment before its reading is
saved as that meter's opening reading.

Steps: property -> apartment -> period (only when the apartment has no open period) -> link.
next_step derives the step from what is filled, so a typed answer and a correction are the same
thing on any step: an edit of a field, then next_step. A property or apartment edit forgets the
period answered for the earlier name, so a typo is fixed by typing the name again rather than by
cancelling.
Nothing is written until the final confirm; then the property, apartment and period are created
as needed, the meter and its opening reading are saved, and a live meter of the same type in the
apartment is retired as replaced on the reading's date. A serial that another photo linked or a
correction made known while this item waited, or that a meter held before a correction renamed it,
sends the item back to the meter photo flow as an ordinary reading of that meter: at its next
answer, and for a correction by the same person as soon as it is saved. The names typed for a
saved property or apartment follow that record when a correction renames it.
"""

from common import texts
from common.dates import first_of_next_month, parse_iso
from common.flows import (
    POSITIONS,
    Reply,
    apply_edits,
    ask,
    choose_position,
    current_period,
    decide,
    display_choice,
    drop,
    edit_read,
    known_meter,
    make_card,
    meter_photo,
    not_understood,
    put_period,
    reading_options,
    save_reading,
    summary,
)
from common.periods import apartment_meters

KIND = "link_meter"
DEFAULT_FIELDS = {
    "property": "property",
    "apartment": "apartment",
    "period": "period_start",
    "link": "value",
}


def _link_text(d):
    return texts.link_confirm(
        d["meter_type"],
        d["serial"],
        d["property"],
        d["property_new"],
        d["apartment"],
        d["apartment_new"],
        d["value"],
        parse_iso(d["reading_date"]),
        d["alternatives"],
        d["position"],
        parse_iso(d["period_start"]),
        d["months"],
        d["replaced_serial"],
    )


# The period-start options in the order the card offers them. A stored map comes back with its
# keys in an order of the store's own, so no card reads its order from one.
START_OPTIONS = tuple(texts.START_LABELS)

STEPS = {
    "property": (
        lambda d: texts.link_property(d["meter_type"], d["serial"], d["choices"]),
        lambda d: [],
    ),
    "apartment": (
        lambda d: texts.link_apartment(d["property"], d["choices"]),
        lambda d: [],
    ),
    "period": (
        lambda d: texts.link_period(d["apartment"], d["property"], d["months"]),
        lambda d: [option for option in START_OPTIONS if option in d["starts"]],
    ),
    "link": (_link_text, reading_options),
}


def card(item) -> dict:
    data, step = item["data"], item["step"]
    kind = "replaced" if step == "link" and data["replaced_serial"] is not None else step
    text, options = STEPS[step]
    labels = texts.start_labels(data["starts"]) if step == "period" else None
    return make_card(item, kind, text(data), options(data), labels)


def form(item) -> dict:
    """The place, the photo's fields and the period. The place comes first because a new place
    forgets the period, and the date before the start because the start may move it. The period's
    are fields only while the link creates the period; an open one is a saved record."""
    data = item["data"]
    fields = {
        "property": data["property"],
        "apartment": data["apartment"],
        "value": data["value"],
        "date": data["reading_date"],
        "serial": data["serial"],
        "meter_type": data["meter_type"],
    }
    if data["period_new"]:
        fields.update(period_start=data["period_start"], months=data["months"])
    return fields


def default_field(item) -> str:
    return DEFAULT_FIELDS[item["step"]]


def edit(engine, user, item, field, value):
    """The period question asks for a date, so a date named there is its answer, not the
    reading's own."""
    data = item["data"]
    if field in ("property", "apartment"):
        if data["apartment"] is not None:
            _forget_period(data)
        data[field] = value
    elif field == "period_start" or (field == "date" and item["step"] == "period"):
        _start_period(data, value)
    elif field == "months":
        data["months"] = value
    else:
        edit_read(data, field, value)
        if field == "date":
            data["photo_date"] = value


def refresh(engine, user, item):
    data = item["data"]
    if known_meter(engine, user, data) is not None:
        item.update(kind=meter_photo.KIND, step="reading")
        _date_at_photo(data)
        meter_photo.refresh(engine, user, item)
        return
    _follow_renames(engine, user, data)
    item["step"] = _question(engine, user, data)


def _follow_renames(engine, user, data):
    """A matched name's id still names the record after a rename, when its typed name no longer
    does. An id that is gone leaves the name, which the card then shows as new."""
    properties = {p["id"]: p for p in engine.store.properties(user.account)}
    apartments = {a["id"]: a for a in engine.store.apartments(user.account)}
    if data["property_id"] in properties:
        data["property"] = properties[data["property_id"]]["name"]
    if data["apartment_id"] in apartments:
        data["apartment"] = apartments[data["apartment_id"]]["name"]


def take_over(engine, user, item):
    """Turns a photo's card into a link's, for a meter a correction deleted while it waited."""
    _begin(item)
    refresh(engine, user, item)


def start(engine, user, item) -> list[Reply]:
    _begin(item)
    return next_step(engine, user, item)


def _begin(item):
    item["kind"] = KIND
    item["data"].update(
        photo_date=item["data"]["reading_date"],
        property=None,
        property_new=True,
        property_id=None,
        choices=[],
        apartment=None,
        apartment_new=True,
        apartment_id=None,
        replaced_serial=None,
        starts={},
        period_start=None,
        months=None,
        period_new=True,
    )


def next_step(engine, user, item) -> list[Reply]:
    """Shows the first question the item has no answer to, or the summary, matching the typed
    names against the account as they stand now. A serial that became known goes back to the meter
    photo flow."""
    data = item["data"]
    if known_meter(engine, user, data) is not None:
        item["kind"] = meter_photo.KIND
        _date_at_photo(data)
        return meter_photo.route(engine, user, item)
    return ask(engine, user, item, _question(engine, user, data), card)


def _question(engine, user, data) -> str:
    """The step of the first question with no answer, else the summary, matching the names."""
    _match_property(engine, user, data)
    if data["property"] is None:
        return "property"
    if data["apartment"] is None:
        return "apartment"
    _match_apartment(engine, user, data)
    return "period" if data["period_start"] is None else "link"


after_edit = next_step


def answer(engine, user, item, action) -> list[Reply]:
    data, step = item["data"], item["step"]
    key, intent = decide(engine, item, action)
    if key == "reject":
        return drop(engine, user, item, [Reply(texts.discarded())])
    if key == "edit":
        return apply_edits(engine, user, item, intent.operations)
    if step == "period" and key in data["starts"]:
        _start_period(data, data["starts"][key])
        return next_step(engine, user, item)
    if step == "link" and key in POSITIONS:
        choose_position(data, key)
        return next_step(engine, user, item)
    if step == "link" and key == "confirm":
        return _link(engine, user, item)
    return not_understood(card(item), key)


def _start_period(data, start):
    """Sets a new period's start. A start before the photo's date makes the photo stand for the
    meter on that start, so the opening reading is dated at the start; a later start keeps the
    photo's date."""
    data["period_start"] = start
    data["reading_date"] = min(data["photo_date"], start)


def _date_at_photo(data):
    """Undoes a date a typed start moved the reading to: only a new period's start moves it."""
    data["reading_date"] = data["photo_date"]


def _forget_period(data):
    """Clears the period answered for an earlier apartment, and the opening reading's date that
    followed from it, so the corrected apartment is asked about or takes its own open period."""
    data.update(period_start=None, months=None, period_new=True)
    _date_at_photo(data)


def _match_property(engine, user, data):
    """Sets what the typed property name means in the account, and the names the next question
    lists: the account's properties until one is named, then that property's apartments."""
    properties = engine.store.properties(user.account)
    if data["property"] is None:
        data["choices"] = [p["name"] for p in properties]
        return
    matches = [p for p in properties if p["name"] == data["property"]]
    data.update(property_new=not matches, property_id=None, choices=[])
    if matches:
        [prop] = matches
        data["property_id"] = prop["id"]
        data["choices"] = [
            a["name"]
            for a in engine.store.apartments(user.account)
            if a["property_id"] == prop["id"]
        ]


def _match_apartment(engine, user, data):
    """Sets what the typed apartment name means in the account: the meter it replaces, and the
    open period the link joins, else the length a new period takes unless one was typed."""
    matches = [
        a
        for a in engine.store.apartments(user.account)
        if a["property_id"] == data["property_id"] and a["name"] == data["apartment"]
    ]
    today = engine.clock()
    # Option id -> the start date it offers; on the first of a month both would be today.
    starts = {"start_today": today.isoformat()}
    if today.day != 1:
        starts["start_month"] = first_of_next_month(today).isoformat()
    data.update(apartment_new=not matches, apartment_id=None, replaced_serial=None, starts=starts)
    months = engine.config["default_period_months"]
    current = None
    if matches:
        apartment = matches[0]
        data["apartment_id"] = apartment["id"]
        same_type = [
            m
            for m in apartment_meters(engine.store.meters(user.account), apartment["id"])
            if m["type"] == data["meter_type"] and m["serial"] != data["serial"]
        ]
        if same_type:
            [data["replaced_serial"]] = [m["serial"] for m in same_type]
        current = current_period(engine, user, apartment["id"])
        months = apartment["period_months"]
    if current is not None:
        data.update(period_start=current["start"], months=current["months"], period_new=False)
        _date_at_photo(data)
        return
    if not data["period_new"]:
        _forget_period(data)
    if data["months"] is None:
        data["months"] = months


def hand_over_known(engine, user) -> list[Reply]:
    """Sends each waiting link of a serial that a saved correction made known to the meter photo
    flow at once, rather than on its next answer."""
    replies = []
    links = [i for i in engine.store.pending(user.email) if i["kind"] == KIND]
    for item in links:
        if known_meter(engine, user, item["data"]) is not None:
            replies.extend(next_step(engine, user, item))
    return replies


def _link(engine, user, item) -> list[Reply]:
    data = item["data"]
    account = user.account
    if data["property_new"]:
        data["property_id"] = engine.store.put_property(account, data["property"])["id"]
    if data["apartment_new"]:
        data["apartment_id"] = engine.store.put_apartment(
            account, data["property_id"], data["apartment"], data["period_start"], data["months"]
        )["id"]
    if data["period_new"]:
        put_period(engine, user, data["apartment_id"], data["period_start"], data["months"])
    meter = {
        "serial": data["serial"],
        "apartment_id": data["apartment_id"],
        "type": data["meter_type"],
        "display_choice": display_choice(data),
        "label_hint": data["handwritten_label"],
        "retired_at": None,
        "replaces": data["replaced_serial"],
    }
    engine.store.put_meter(account, meter)
    reading = save_reading(engine, user, item, opens=data["period_start"])
    if data["replaced_serial"] is not None:
        engine.store.update_meter(account, data["replaced_serial"], retired_at=data["reading_date"])
    return drop(engine, user, item, [summary(engine, user, meter, reading)], state="saved")
