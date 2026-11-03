"""New-meter flow: a photo of an unknown serial is linked to an apartment before its reading is
saved as that meter's opening reading.

Steps: property -> apartment -> period (only when the apartment has no open period) -> link.
next_step derives the step from what is filled, so a typed answer and a correction are the same
thing on any step: an edit of a field, then next_step. A property or apartment edit forgets the
period answered for the earlier name, so a typo is fixed by typing the name again rather than by
cancelling. The property and apartment questions offer the existing names as buttons, and a new
typed name close to an existing one first asks which was meant (property_near, apartment_near).
A summary whose reading is dated after the period it would open ends offers no confirm and asks
for the reading's date.
Nothing is written until the final confirm, or a number pressed on a two-number display; then
the property, apartment and period are created as needed, the meter and its opening reading are
saved, and a live meter of the same type in the apartment is retired as replaced on the
reading's date. A serial that another photo linked or a correction made known while this item
waited, or that a meter held before a correction renamed it, sends the item back to the meter
photo flow as an ordinary reading of that meter: at its next answer, and for a correction by the
same person as soon as it is saved. The names typed for a saved property or apartment follow that
record when a correction renames it.
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
    reading_default,
    reading_options,
    save_reading,
    settle_back_dated,
    summary,
)
from common.periods import apartment_meters, covers, period_end

KIND = "link_meter"
DEFAULT_FIELDS = {
    "property": "property",
    "property_near": "property",
    "apartment": "apartment",
    "apartment_near": "apartment",
    "period": "period_start",
    "link": "value",
}


def _link_text(d):
    title, lines = texts.link_confirm(
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
    if _after_period(d):
        lines.append(texts.reading_after_period(_period_end(d)))
    elif d["closed_start"] is not None:
        lines.append(texts.reading_in_closed_period(parse_iso(d["closed_start"])))
    return title, lines


def _period_end(d):
    return period_end({"start": d["period_start"], "months": d["months"]})


def _after_period(d) -> bool:
    """True when the reading is dated after the last day of the period it would open, which no
    reading may open; the card then asks for the reading's date."""
    return parse_iso(d["reading_date"]) > _period_end(d)


def _link_options(d) -> list[str]:
    return [] if _after_period(d) else reading_options(d)


# The period-start options in the order the card offers them. A stored map comes back with its
# keys in an order of the store's own, so no card reads its order from one.
START_OPTIONS = tuple(texts.START_LABELS)

NEAR_OPTIONS = ["near_match", "near_new"]
# A typed name this many letters from an existing one is taken for a slip of that one.
NEAR_EDITS = 2


def _name_options(d) -> dict[str, str]:
    """Option id -> the existing name its button answers the question with."""
    return {f"name_{i}": name for i, name in enumerate(d["choices"])}


def _near_text(d):
    field, typed, match = d["near"]
    return texts.near_name(field, typed, match)


STEPS = {
    "property": (
        lambda d: texts.link_property(d["meter_type"], d["serial"], d["choices"]),
        lambda d: list(_name_options(d)),
    ),
    "property_near": (_near_text, lambda d: NEAR_OPTIONS),
    "apartment": (
        lambda d: texts.link_apartment(d["property"], d["choices"]),
        lambda d: list(_name_options(d)),
    ),
    "apartment_near": (_near_text, lambda d: NEAR_OPTIONS),
    "period": (
        lambda d: texts.link_period(d["apartment"], d["property"], d["months"]),
        lambda d: [option for option in START_OPTIONS if option in d["starts"]],
    ),
    "link": (_link_text, _link_options),
}


def card(item) -> dict:
    data, step = item["data"], item["step"]
    kind = "replaced" if step == "link" and data["replaced_serial"] is not None else step
    text, options = STEPS[step]
    default = reading_default(data) if step == "link" and not _after_period(data) else None
    if step == "period":
        labels = texts.start_labels(data["starts"])
    elif step in ("property", "apartment"):
        labels = _name_options(data)
    elif step in ("property_near", "apartment_near"):
        field, typed, match = data["near"]
        labels = {"near_match": match, "near_new": texts.new_name(field, typed)}
        default = "near_match"
    else:
        labels = None
    return make_card(item, kind, text(data), options(data), labels, default)


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
    if item["step"] == "link" and _after_period(item["data"]):
        return "date"
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
        closed_start=None,
        near=None,
        accepted_new=[],
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
    """The step of the first question with no answer, else the summary, matching the names. A
    typed name close to an existing one asks which was meant before it counts as new."""
    data["near"] = None
    _match_property(engine, user, data)
    if data["property"] is None:
        return "property"
    if data["near"] is not None:
        return "property_near"
    if data["apartment"] is None:
        return "apartment"
    _match_apartment(engine, user, data)
    if data["near"] is not None:
        return "apartment_near"
    return "period" if data["period_start"] is None else "link"


def _close_name(typed: str, names: list[str]) -> str | None:
    """The existing name a typed one most likely slipped from: one it is a prefix of, else one
    within NEAR_EDITS letters of it; None when nothing is that close."""
    prefixed = [n for n in names if n.startswith(typed)]
    if prefixed:
        return prefixed[0]
    distances = {n: _edit_distance(typed, n) for n in names}
    near = [n for n in names if distances[n] <= NEAR_EDITS]
    return min(near, key=distances.__getitem__) if near else None


def _edit_distance(a: str, b: str) -> int:
    """The fewest single-letter insertions, deletions and substitutions turning a into b."""
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        previous, row[0] = row[0], i
        for j, cb in enumerate(b, 1):
            previous, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, previous + (ca != cb))
    return row[-1]


def _mark_near(data, field, names):
    """Holds the existing name a new typed name slipped from, unless the owner already chose
    the typed name as new."""
    typed = data[field]
    if typed in data["accepted_new"]:
        return
    match = _close_name(typed, names)
    if match is not None:
        data["near"] = [field, typed, match]


after_edit = next_step


def answer(engine, user, item, action) -> list[Reply]:
    data, step = item["data"], item["step"]
    key, intent = decide(engine, item, action)
    if key == "reject":
        return drop(engine, user, item, [Reply(texts.discarded())])
    if key == "edit":
        return apply_edits(engine, user, item, intent.operations)
    if step in ("property", "apartment") and key in _name_options(data):
        edit(engine, user, item, step, _name_options(data)[key])
        return next_step(engine, user, item)
    if step in ("property_near", "apartment_near") and key in NEAR_OPTIONS:
        field, typed, match = data["near"]
        if key == "near_match":
            edit(engine, user, item, field, match)
        else:
            data["accepted_new"].append(typed)
        return next_step(engine, user, item)
    if step == "period" and key in data["starts"]:
        _start_period(data, data["starts"][key])
        return next_step(engine, user, item)
    if step == "link" and key in (*POSITIONS, "confirm") and _after_period(data):
        return not_understood(card(item), key)
    if step == "link" and key in POSITIONS:
        choose_position(data, key)
        return _link(engine, user, item)
    if step == "link" and key == "confirm":
        return _link(engine, user, item)
    return not_understood(card(item), key)


def _closed_start(engine, user, apartment_id, day) -> str | None:
    """The start of the closed period that holds the reading's date before its closing day; the
    reading counts there once a later reading of the meter inside it closes it for the meter.
    None when no closed period holds the date that way: a reading dated on a closing day stands
    for the meter on the next period, as the closing readings do."""
    periods = engine.store.periods(user.account, apartment_id)
    held = [
        p["start"]
        for p in periods
        if p["closed_on"] is not None and covers(p, day) and day < p["closed_on"]
    ]
    return next(iter(held), None)


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
    if not matches:
        _mark_near(data, "property", [p["name"] for p in properties])
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
    if not matches:
        _mark_near(data, "apartment", data["choices"])
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
        data["closed_start"] = _closed_start(engine, user, apartment["id"], data["reading_date"])
        return
    data["closed_start"] = None
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
    reading = settle_back_dated(engine, user, meter, reading)
    if data["replaced_serial"] is not None:
        engine.store.update_meter(account, data["replaced_serial"], retired_at=data["reading_date"])
    return drop(engine, user, item, [summary(engine, user, meter, reading)], state="saved")
