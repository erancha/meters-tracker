"""New-meter flow: a photo of an unknown serial is linked to an apartment before its reading is
saved as that meter's opening reading.

Steps: property -> apartment -> period (only when the apartment has no open period) -> link. A
property answer that also names the apartment skips the apartment question. A property or
apartment name typed on a later step corrects the earlier answer and the flow resumes from it,
so a typo is fixed by typing the name again rather than by cancelling.
Nothing is written until the final confirm; then the property, apartment and period are created
as needed, the meter and its opening reading are saved, and a live meter of the same type in the
apartment is retired as replaced on the reading's date. A serial that another photo linked while
this item waited is known by the time its answer arrives, so the item goes back to the meter
photo flow as an ordinary reading of that meter.
"""

from common import texts
from common.dates import first_of_next_month, parse_iso
from common.flows import (
    Reply,
    ask,
    correct_serial,
    current_period,
    decide,
    display_choice,
    drop,
    edit_reading,
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
        lambda d: list(d["starts"]),
    ),
    "link": (_link_text, reading_options),
}


def card(item) -> dict:
    data, step = item["data"], item["step"]
    kind = "replaced" if step == "link" and data["replaced_serial"] is not None else step
    text, options = STEPS[step]
    labels = texts.start_labels(data["starts"]) if step == "period" else None
    return make_card(item, kind, text(data), options(data), labels)


def start(engine, user, item) -> list[Reply]:
    item["kind"] = KIND
    item["data"]["choices"] = [p["name"] for p in engine.store.properties(user.account)]
    item["data"]["photo_date"] = item["data"]["reading_date"]
    return ask(engine, user, item, "property", card)


def answer(engine, user, item, action) -> list[Reply]:
    data, step = item["data"], item["step"]
    key, intent = decide(engine, card(item), action)
    if key == "reject":
        return drop(engine, user, item, [Reply(texts.discarded())])
    if (
        correct_serial(data, key, intent)
        and engine.store.meter(user.account, data["serial"]) is None
    ):
        return ask(engine, user, item, step, card)
    if engine.store.meter(user.account, data["serial"]) is not None:
        item["kind"] = meter_photo.KIND
        return meter_photo.route(engine, user, item)
    if key == "choose_property":
        _choose_property(engine, user, item, _name(intent))
        if intent.apartment is not None:
            return _choose_apartment(engine, user, item, intent.apartment)
        if step == "property":
            return ask(engine, user, item, "apartment", card)
        # A property name typed on a later question corrects the one given; the apartment is
        # matched again, since it belongs to the corrected property.
        return _choose_apartment(engine, user, item, data["apartment"])
    if step != "property" and key == "choose_apartment":
        return _choose_apartment(engine, user, item, _name(intent))
    if step == "period" and key in data["starts"]:
        _start_period(data, data["starts"][key])
        return ask(engine, user, item, "link", card)
    # The question asks for a date, so a date read as the reading's own also answers it.
    if step == "period" and key in ("set_period", "set_date"):
        if intent.months is not None:
            data["months"] = intent.months
        if intent.date is None:
            return ask(engine, user, item, "period", card)
        _start_period(data, intent.date)
        return ask(engine, user, item, "link", card)
    if step == "link" and edit_reading(data, key, intent):
        return ask(engine, user, item, "link", card)
    if step == "link" and key == "confirm":
        return _link(engine, user, item)
    return not_understood(card(item), key)


def _start_period(data, start):
    """Sets a new period's start. A start before the photo's date makes the photo stand for the
    meter on that start, so the opening reading is dated at the start; a later start keeps the
    photo's date."""
    data["period_start"] = start
    data["reading_date"] = min(data["photo_date"], start)


def _name(intent) -> str:
    if intent.name is None:
        raise ValueError(f"intent {intent.intent} came without a name")
    return intent.name


def _choose_property(engine, user, item, name):
    data = item["data"]
    matches = [p for p in engine.store.properties(user.account) if p["name"] == name]
    data.update(property=name, property_new=not matches, property_id=None, choices=[])
    if matches:
        [prop] = matches
        data["property_id"] = prop["id"]
        data["choices"] = [
            a["name"]
            for a in engine.store.apartments(user.account)
            if a["property_id"] == prop["id"]
        ]


def _choose_apartment(engine, user, item, name) -> list[Reply]:
    data = item["data"]
    matches = [
        a
        for a in engine.store.apartments(user.account)
        if a["property_id"] == data["property_id"] and a["name"] == name
    ]
    today = engine.clock()
    # Option id -> the start date it offers; on the first of a month both would be today.
    starts = {"start_today": today.isoformat()}
    if today.day != 1:
        starts["start_month"] = first_of_next_month(today).isoformat()
    data.update(
        apartment=name,
        apartment_new=not matches,
        replaced_serial=None,
        period_start=today.isoformat(),
        starts=starts,
        months=engine.config["default_period_months"],
        period_new=True,
    )
    if not matches:
        data["apartment_id"] = None
        return ask(engine, user, item, "period", card)
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
    if current is None:
        data["months"] = apartment["period_months"]
        return ask(engine, user, item, "period", card)
    data.update(period_start=current["start"], months=current["months"], period_new=False)
    return ask(engine, user, item, "link", card)


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
    }
    engine.store.put_meter(account, meter)
    reading = save_reading(engine, user, item, opens=data["period_start"])
    if data["replaced_serial"] is not None:
        engine.store.update_meter(account, data["replaced_serial"], retired_at=data["reading_date"])
    return drop(engine, user, item, [summary(engine, user, meter, reading)], state="saved")
