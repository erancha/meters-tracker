"""Meter photo flow: a photo becomes a proposed reading, and a confirmed one becomes a saved
reading of a known meter. An electricity meter's serial is the label handwritten beside it, since
the number printed on one is often the model number rather than a unique serial number; a water
meter's is the serial printed on it. A photo of no known meter family and no serial is read
again as a previous statement, and one that is a statement is handed to the tariffs flow, dated
like a reading. A water serial one character off exactly one live meter is taken as that
meter's, and any other unknown serial is handed to the link flow. On a reading near the
period's end, the close option or a typed vacate hands the apartment to the close flow after the
reading is saved, and the reading's reply is built after the close, so it reports the closed
period. The value, date and serial take a typed edit on every step. A value that is the meter's
own serial is no reading, so the card asks for the reading; one that is far above what the
meter's history makes plausible is questioned before it is saved.

Steps: unreadable -> reading -> one of duplicate, lower_reading, jump, swap, propose_reclose,
open_period, propose_close -> saved. A reading is never saved twice for one meter and date. A
back-dated reading never displaces a confirmed closing reading without the user's choice. A
two-number display's choice becomes the meter's only when a reading is saved. A failed read
leaves no pending row, and a retake replaces the unreadable card it answers, carrying its
photos as the ones the model failed on. A re-close marks its period for a report that names the
report it replaces, dated by the period's closing date before the move.

A photo the app dropped (cancelled, not a meter, a retired serial, a statement's) is reopened by a
typed correction: read again from its stored copy, as the kind the owner gives, its typed fields
applied, then routed as a new photo is. A photo the owner reopens as a meter's is never taken for
a statement or dropped as no meter, so a type the model cannot tell is asked for instead.
"""

import json
from dataclasses import dataclass
from datetime import datetime

from common import dates, prompts, texts
from common.dates import parse_iso
from common.fields import (
    PHOTO_KINDS,
    InvalidValue,
    UnknownField,
    field_for,
    normalize_serial,
    parse,
)
from common.flows import (
    CARD_TARGET,
    FAILURE_FIELDS,
    POSITIONS,
    READING,
    CardGone,
    Reply,
    add_failed_photos,
    apply_edits,
    ask,
    choose_position,
    close_period,
    current_period,
    decide,
    display_choice,
    drop,
    edit_read,
    failure_record,
    form_context,
    known_meter,
    link_meter,
    make_card,
    new_item,
    not_understood,
    period_change,
    place,
    propose,
    put_period,
    reading_default,
    reading_options,
    reading_text,
    save_reading,
    settle_back_dated,
    summary,
    tariffs,
)
from common.log import get_logger
from common.model import MeterReadingResult, Operation, StatementResult
from common.periods import (
    apartment_meters,
    is_closing_candidate,
    joining_opens,
    period_end,
    within_reclose_window,
)

logger = get_logger(__name__)

KIND = "meter_photo"
SWAP_OPTIONS = ["reclose", "keep_open"]
# The steps whose card rests on data["other"], a copy of a saved reading.
OTHER_STEPS = ("duplicate", "lower_reading", "jump", "swap", "propose_reclose")
STATEMENT, METER = PHOTO_KINDS
# The fields a typed correction may give a dropped photo, in the order they apply: an unreadable
# card's, since the photo has no card until it is read again.
PHOTO_FIELDS = ("value", "date", "serial", "meter_type")


@dataclass(frozen=True)
class Reopen:
    """What a typed correction asks of a dropped photo: the kind to read it as, and the (field,
    typed value) pairs to apply, in PHOTO_FIELDS order; a statement has none."""

    kind: str
    edits: list[tuple[str, object]]


def _photo_value(field, operation: Operation, today, current=texts.EMPTY_VALUE):
    """current is the value on record as a question words it: the photo holds no field of a
    reading before it is read again, only the kind it was read as."""
    try:
        return parse(field, operation.value, today)
    except InvalidValue as error:
        raise InvalidValue(field, error.raw, current, operation.target) from error


def reopening(operations: list[Operation], today, kind: str) -> Reopen:
    """The Reopen a message's operations on one dropped photo ask for; kind is how the photo was
    read. A field given makes it a meter's photo. Raises UnknownField for an operation a photo
    cannot take, two kinds or a statement given a meter's field among them, and InvalidValue for
    a value that does not fit."""
    kinds, edits = set(), []
    for operation in operations:
        if operation.action != "set":
            raise UnknownField(f"{operation.action} on {operation.target}")
        if operation.field == "kind":
            field = field_for("photo", "kind")
            current = texts.field_value(field.kind, kind)
            kinds.add(_photo_value(field, operation, today, current))
        elif operation.field in PHOTO_FIELDS:
            field = field_for("card", operation.field)
            edits.append((operation.field, _photo_value(field, operation, today)))
        else:
            raise UnknownField(f"photo.{operation.field}")
    if len(kinds) > 1 or (STATEMENT in kinds and edits):
        raise UnknownField(f"photo kinds {sorted(kinds)} with {len(edits)} fields")
    [kind] = kinds or {METER}
    return Reopen(kind, sorted(edits, key=lambda edit: PHOTO_FIELDS.index(edit[0])))


def _date(data, key):
    return parse_iso(data[key]["date"])


STEPS = {
    "unreadable": (lambda d: texts.unreadable(d["unreadable"]), lambda d: []),
    "reading": (reading_text, reading_options),
    "duplicate": (
        lambda d: texts.duplicate_reading(d["other"]["value"], _date(d, "other")),
        lambda d: ["confirm"],
    ),
    "lower_reading": (
        lambda d: texts.lower_reading(d["other"], d["value"]),
        lambda d: ["misread", "replaced", "reset"],
    ),
    "jump": (
        lambda d: texts.jump_reading(
            d["meter_type"], d["other"], d["value"], d["jump"]["per_day"], d["jump"]["average"]
        ),
        lambda d: ["confirm"],
    ),
    "swap": (lambda d: texts.propose_swap(_date(d, "other")), lambda d: SWAP_OPTIONS),
    "propose_reclose": (lambda d: texts.propose_reclose(_date(d, "other")), lambda d: SWAP_OPTIONS),
    "open_period": (
        lambda d: texts.propose_open(parse_iso(d["reading_date"])),
        lambda d: ["confirm"],
    ),
    "propose_close": (
        lambda d: texts.propose_close(parse_iso(d["period_end"]), parse_iso(d["reading_date"])),
        lambda d: ["close", "keep_open"],
    ),
}


# The answer each question card expects, which the browser shows as its primary button; a card
# not listed offers confirm, or options with no usual answer.
DEFAULTS = {
    "lower_reading": "misread",
    "swap": "reclose",
    "propose_reclose": "reclose",
    "propose_close": "close",
}


def card(item) -> dict:
    data, step = item["data"], item["step"]
    text, options = STEPS[step]
    default = reading_default(data) if step == "reading" else DEFAULTS.get(step)
    return make_card(item, step, text(data), options(data), default=default)


def form(item) -> dict:
    """The fields of the photo. A known meter's type and place are saved records, so only an
    unreadable card, whose meter is not settled yet, takes the type."""
    data = item["data"]
    fields = {
        "value": data["value"],
        "date": data["reading_date"],
        "serial": None if _no_serial(data) else data["serial"],
    }
    if item["step"] == "unreadable":
        known_type = data["meter_type"] != "unknown"
        fields["meter_type"] = data["meter_type"] if known_type else None
    return fields


def default_field(item) -> str:
    """The serial on an unreadable card that lacks one, else the reading."""
    return "serial" if item["step"] == "unreadable" and _no_serial(item["data"]) else "value"


def edit(engine, user, item, field, value):
    edit_read(item["data"], field, value)


def refresh(engine, user, item):
    """Follows the meter the photo's serial names. A step whose reason no longer holds returns to
    the reading card, whose confirm runs every check again."""
    data = item["data"]
    if data["serial"] is None:
        return
    meter = known_meter(engine, user, data)
    if item["step"] == "unreadable":
        return
    if meter is None:
        link_meter.take_over(engine, user, item)
        return
    if meter["retired_at"] is not None:
        raise CardGone(_retired_reply(engine, user, meter))
    _name_meter(engine, user, data, meter)
    if not _step_holds(engine, user, item, meter):
        item["step"] = "reading"


def _step_holds(engine, user, item, meter) -> bool:
    data, step = item["data"], item["step"]
    if step in OTHER_STEPS:
        return data["other"] in engine.store.readings(user.account, meter["serial"])
    current = current_period(engine, user, meter["apartment_id"])
    if step == "open_period":
        return current is None
    if step == "propose_close":
        return current is not None and period_end(current).isoformat() == data["period_end"]
    return True


def _name_meter(engine, user, data, meter):
    apartment, prop = place(engine, user, meter["apartment_id"])
    data.update(meter_type=meter["type"], apartment=apartment["name"], property=prop["name"])


def start(
    engine, user, seq, image_key, image, media_type, note, sent_on, message_at, reopen=None
) -> list[Reply]:
    """reopen is None for a photo as it arrives, else the Reopen of a dropped photo read again.
    The data's as_meter marks a photo the owner says is a meter's."""
    close_period.park_waiting(engine, user)
    kind = None if reopen is None else reopen.kind
    data = {
        "image_key": image_key,
        "message_at": message_at,
        "read_started": dates.now().isoformat(),
        "failed_photos": [],
        "as_meter": kind == METER,
    }
    item = new_item(seq, KIND, READING, None, data)
    engine.store.put_pending(user.email, item)
    try:
        result, statement = _read(engine, image, media_type, kind)
        reading_date = _reading_date(engine, note, sent_on)
    except Exception:
        engine.store.delete_pending(user.email, seq)
        raise
    if statement is not None:
        return tariffs.from_statement(engine, user, item, statement, reading_date)
    _set_read(item, result, reading_date)
    if reopen is not None:
        _log_read(result, item["data"])
        return _reopened(engine, user, item, reopen)
    _absorb_superseded(engine, user, item)
    matched = _match_near_serial(engine, user, item["data"])
    _log_read(result, item["data"])
    return [*matched, *route(engine, user, item)]


def _log_read(result: MeterReadingResult, data):
    """One JSON line with "event": "meter_read" per photo read as a meter: what the model read,
    with its doubt flags, and the known serial a near-miss match took instead, else null."""
    matched = data["serial"] if data["serial"] != meter_identity(result)["serial"] else None
    line = {
        "event": "meter_read",
        "meter_type": result.meter_type,
        "serial": result.serial,
        "serial_unreadable": result.serial_unreadable,
        "serial_uncertain": result.serial_uncertain,
        "candidates": [c.model_dump(exclude={"note"}) for c in result.candidates],
        "handwritten_label": result.handwritten_label,
        "matched_serial": matched,
    }
    logger.info(json.dumps(line))


def _read(
    engine, image, media_type, kind
) -> tuple[MeterReadingResult, None] | tuple[None, StatementResult]:
    """The meter the model reads in the photo, or the statement: kind forces one, and with none a
    photo of no known meter family and no serial that the model reads as a statement is one."""
    if kind == STATEMENT:
        return None, engine.model.read_statement(image, media_type)
    result = engine.model.read_meter(image, media_type)
    if kind is None and result.meter_type == "unknown" and result.serial is None:
        statement = engine.model.read_statement(image, media_type)
        if statement.is_statement:
            return None, statement
    return result, None


def _reopened(engine, user, item, reopen: Reopen) -> list[Reply]:
    """Routes a dropped photo reopened as a meter's with its typed fields. A model that read no
    meter type failed on the photo, so it counts among the failed photos once. A statement card
    read from the same photo leaves the queue."""
    data = item["data"]
    if data["meter_type"] == "unknown":
        add_failed_photos(data, [data["image_key"]])
    for field, value in reopen.edits:
        edit_read(data, field, value)
    tariffs.drop_read_from(engine, user, data["image_key"])
    return route(engine, user, item)


def _match_near_serial(engine, user, data) -> list[Reply]:
    """Takes a read serial that names no meter as the one live meter of its type whose serial
    differs from it in a single character, since a misread digit is the model's usual serial
    error. The read serial is kept as read_serial, as a typed correction keeps it, so the saved
    reading records the misread. Only the model's read of a printed serial is matched: a typed
    serial stands, and labels a character apart name different apartments."""
    read = data["serial"]
    if data["meter_type"] == "electricity":
        return []
    if _no_serial(data) or engine.store.meter(user.account, read) is not None:
        return []
    near = [
        m["serial"]
        for m in engine.store.meters(user.account)
        if m["retired_at"] is None
        and m["type"] == data["meter_type"]
        and _one_character_apart(m["serial"], read)
    ]
    if len(near) != 1:
        return []
    data.update(read_serial=read, serial=near[0], serial_uncertain=False)
    return [Reply(texts.serial_matched(read, data["serial"]))]


def _one_character_apart(a: str, b: str) -> bool:
    return len(a) == len(b) and sum(x != y for x, y in zip(a, b)) == 1


def meter_identity(result: MeterReadingResult) -> dict:
    """The serial fields a photo identifies its meter by. An electricity photo goes by its
    handwritten label, stored as a typed serial is, and is unreadable when it has none; the
    printed number plays no part. Any other photo goes by the serial the model read, with its
    doubt flags."""
    if result.meter_type != "electricity":
        return result.model_dump(include={"serial", "serial_unreadable", "serial_uncertain"})
    label = result.handwritten_label
    return {
        "serial": None if label is None else normalize_serial(label),
        "serial_unreadable": label is None,
        "serial_uncertain": False,
    }


def _set_read(item, result: MeterReadingResult, reading_date):
    item["data"].update(
        result.model_dump(),
        **meter_identity(result),
        reading_date=reading_date,
        value=None,
        value_uncertain=False,
        resets=False,
    )


def stalled(engine, item) -> bool:
    """True when the photo has been read for longer than an api invocation may live, so the
    invocation reading it was killed."""
    started = datetime.fromisoformat(item["data"]["read_started"])
    return dates.now() - started > engine.lambda_timeout


def ask_retake(engine, user, item) -> list[Reply]:
    """Shows a stalled photo as an unreadable card with nothing read, which the next photo
    replaces."""
    nothing_read = MeterReadingResult(
        meter_type="unknown",
        serial=None,
        serial_unreadable=True,
        serial_uncertain=False,
        candidates=[],
        handwritten_label=None,
    )
    _set_read(item, nothing_read, engine.clock().isoformat())
    item["data"]["unreadable"] = [texts.UNREADABLE_PHOTO]
    return ask(engine, user, item, "unreadable", card)


def _no_serial(data) -> bool:
    return data["serial_unreadable"] or data["serial"] is None


def _absorb_superseded(engine, user, item):
    """A new photo is the retake an unreadable card asked for when that card has no serial or
    the same one, so the old card leaves the queue and its photos join the new one's failed
    photos, oldest first. The new item is stored with the joined photos before the old cards
    are deleted, so a crash in between leaves the joined photos on a card rather than losing
    them."""
    absorbed = [
        other
        for other in engine.store.pending(user.email)
        if other["kind"] == KIND
        and other["step"] == "unreadable"
        and other["seq"] != item["seq"]
        and (_no_serial(other["data"]) or other["data"]["serial"] == item["data"]["serial"])
    ]
    if not absorbed:
        return
    for other in absorbed:
        add_failed_photos(
            item["data"], [*other["data"]["failed_photos"], other["data"]["image_key"]]
        )
    engine.store.put_pending(user.email, item)
    for other in absorbed:
        drop(engine, user, other, [])


def _reading_date(engine, note, sent_on) -> str:
    """The note's date, else sent_on, since a retry may read a photo days after it was sent. The
    note is classified as an edit of a form holding only the date. A date that does not fit leaves
    sent_on, which the card shows and a typed date corrects."""
    if note is None:
        return sent_on
    sent_day = parse_iso(sent_on)
    intent = engine.model.classify(
        note, form_context({"date": sent_on}), sent_day, lambda query: prompts.NOTE_HAS_NO_RECORDS
    )
    # A bare date has no field, and the note's form has only the date for it to fill.
    named = [
        o.value for o in intent.operations if o.target == CARD_TARGET and o.field in (None, "date")
    ]
    if intent.intent != "edit" or not named:
        return sent_on
    try:
        return parse(field_for("card", "date"), named[0], sent_day)
    except InvalidValue:
        return sent_on


def route(engine, user, item) -> list[Reply]:
    data = item["data"]
    meter = None if data["serial"] is None else known_meter(engine, user, data)
    unknown_type = data["meter_type"] == "unknown" and meter is None
    if unknown_type and not data["as_meter"]:
        return drop(engine, user, item, [Reply(texts.not_a_meter())])
    if _no_serial(data):
        data["unreadable"] = [texts.UNREADABLE_SERIAL]
        return ask(engine, user, item, "unreadable", card)
    if meter is not None and meter["retired_at"] is not None:
        return drop(engine, user, item, [Reply(_retired_reply(engine, user, meter))])
    if unknown_type:
        data["unreadable"] = [texts.UNREADABLE_TYPE]
        return ask(engine, user, item, "unreadable", card)
    if data["value"] is None and not propose(
        data, None if meter is None else meter["display_choice"]
    ):
        data["unreadable"] = [texts.UNREADABLE_VALUE]
        return ask(engine, user, item, "unreadable", card)
    if meter is None:
        return link_meter.start(engine, user, item)
    if str(data["value"]) == meter["serial"]:
        data.update(value=None, unreadable=[texts.UNREADABLE_VALUE])
        reply = Reply(texts.value_is_serial(meter["serial"]))
        return [reply, *ask(engine, user, item, "unreadable", card)]
    _name_meter(engine, user, data, meter)
    return ask(engine, user, item, "reading", card)


# An edit on any step returns to route, so a changed value or date goes through the reading card
# and its checks run again on confirm.
after_edit = route


def _retired_reply(engine, user, meter) -> str:
    """Names the live meter of the type in the apartment; a correction can leave none."""
    live = [
        m
        for m in apartment_meters(engine.store.meters(user.account), meter["apartment_id"])
        if m["type"] == meter["type"]
    ]
    if not live:
        return texts.retired_meter_alone(meter["serial"])
    [successor] = live
    return texts.retired_meter(meter["serial"], successor["serial"])


def answer(engine, user, item, action) -> list[Reply]:
    data, step = item["data"], item["step"]
    key, intent = decide(engine, item, action)
    if key == "reject":
        return drop(engine, user, item, [Reply(texts.discarded())])
    if key == "edit":
        return apply_edits(engine, user, item, intent.operations)
    if step == "reading" and key in POSITIONS:
        choose_position(data, key)
        return _check(engine, user, item)
    if step == "reading" and key == "confirm":
        return _check(engine, user, item)
    if step == "duplicate" and key == "confirm":
        return _replace(engine, user, item)
    if step == "lower_reading" and key == "misread":
        data.update(value=None, unreadable=[texts.UNREADABLE_VALUE])
        return [Reply(texts.misread()), *ask(engine, user, item, "unreadable", card)]
    if step == "lower_reading" and key == "replaced":
        return drop(engine, user, item, [Reply(texts.replaced_has_new_serial())])
    if step == "lower_reading" and key == "reset":
        data["resets"] = True
        return _check(engine, user, item, lower_checked=True)
    if step == "jump" and key == "confirm":
        return _check(engine, user, item, jump_checked=True)
    if step in ("swap", "propose_reclose") and key == "reclose":
        return _reclose(engine, user, item)
    if step == "open_period" and key == "confirm":
        apartment, _ = place(engine, user, _meter(engine, user, data)["apartment_id"])
        put_period(engine, user, apartment["id"], data["reading_date"], apartment["period_months"])
        return _save(engine, user, item)
    if key == "keep_open":
        return _save(engine, user, item)
    if step == "propose_close" and key in ("close", "vacate"):
        apartment_id = _meter(engine, user, data)["apartment_id"]
        return _save(
            engine,
            user,
            item,
            then=lambda: close_period.start(
                engine, user, apartment_id, vacate=key == "vacate", at_once=True
            ),
        )
    return not_understood(card(item), key)


def _meter(engine, user, data) -> dict:
    return engine.store.meter(user.account, data["serial"])


def _check(engine, user, item, lower_checked=False, jump_checked=False) -> list[Reply]:
    data = item["data"]
    meter = _meter(engine, user, data)
    day = data["reading_date"]
    readings = engine.store.readings(user.account, meter["serial"])
    same_day = [r for r in readings if r["date"] == day]
    if same_day:
        data["other"] = same_day[-1]
        return ask(engine, user, item, "duplicate", card)
    earlier = [r for r in readings if r["date"] < day]
    if not lower_checked and earlier and data["value"] < earlier[-1]["value"]:
        data["other"] = earlier[-1]
        return ask(engine, user, item, "lower_reading", card)
    if not jump_checked and earlier and not data["resets"]:
        jump = _jump(data, earlier, meter["type"], engine.config)
        if jump is not None:
            data.update(other=earlier[-1], jump=jump)
            return ask(engine, user, item, "jump", card)
    periods = engine.store.periods(user.account, meter["apartment_id"])
    current = current_period(engine, user, meter["apartment_id"])
    if current is None:
        return ask(engine, user, item, "open_period", card)
    closings = [r for r in readings if r["closes_period"] is not None]
    if closings:
        closing = closings[-1]
        [closed] = [p for p in periods if p["start"] == closing["closes_period"]]
        step = _closing_step(closing, closed, day, engine.config)
        if step is not None:
            data["other"] = closing
            return ask(engine, user, item, step, card)
    parked = close_period.parked(engine, user, meter["apartment_id"])
    early = engine.config["close_days_before_end"]
    if parked is None and is_closing_candidate(current, parse_iso(day), early):
        data["period_end"] = period_end(current).isoformat()
        return ask(engine, user, item, "propose_close", card)
    return [*_save(engine, user, item), *close_period.resume(engine, user, parked)]


def _jump(data, earlier, meter_type, config) -> dict | None:
    """The reading's consumption per day since the meter's latest earlier reading, with the
    meter's own daily average, when it is implausibly high; None for a plausible reading. The
    average runs from the meter's last reset over its earlier readings, and the reading is
    implausible above reading_jump_multiple times it. A meter with fewer than two earlier
    readings since its reset, or no consumption between them, has no average (None) and is
    judged against its type's reading_jump_ceiling_per_day instead."""
    previous = earlier[-1]
    per_day = (data["value"] - previous["value"]) / _days(previous, data["reading_date"])
    resets = [i for i, r in enumerate(earlier) if r["resets"]]
    history = earlier[resets[-1] :] if resets else earlier
    used = history[-1]["value"] - history[0]["value"]
    if len(history) >= 2 and used > 0:
        average = used / _days(history[0], history[-1]["date"])
        limit = config["reading_jump_multiple"] * average
    else:
        average = None
        limit = config["reading_jump_ceiling_per_day"][meter_type]
    if per_day <= limit:
        return None
    return {"per_day": per_day, "average": average}


def _days(reading, day) -> int:
    return (parse_iso(day) - parse_iso(reading["date"])).days


def _closing_step(closing, closed, day, config) -> str | None:
    """The step a reading near the meter's latest closing reading calls for: a back-dated one
    that would close the same period earlier offers a swap; one shortly after offers a re-close."""
    early = config["close_days_before_end"]
    if day < closing["date"] and is_closing_candidate(closed, parse_iso(day), early):
        return "swap"
    if closing["date"] < day and within_reclose_window(
        parse_iso(closing["date"]), parse_iso(day), config["reclose_days"]
    ):
        return "propose_reclose"
    return None


def _save(engine, user, item, then=None) -> list[Reply]:
    meter = _meter(engine, user, item["data"])
    current = current_period(engine, user, meter["apartment_id"])
    readings = engine.store.readings(user.account, meter["serial"])
    opens = joining_opens(current, readings)
    reading = save_reading(engine, user, item, opens=opens)
    reading = settle_back_dated(engine, user, meter, reading)
    return _done(engine, user, item, reading, then=then)


def _replace(engine, user, item) -> list[Reply]:
    data = item["data"]
    other = data["other"]
    record = failure_record(engine, data)
    fields = {"value": data["value"], "image_key": data["image_key"], **record}
    stale = () if record else FAILURE_FIELDS
    engine.store.update_reading(
        user.account, data["serial"], other["date"], other["seq"], remove=stale, **fields
    )
    replaced = {k: v for k, v in {**other, **fields}.items() if k not in stale}
    return _done(engine, user, item, replaced)


def _reclose(engine, user, item) -> list[Reply]:
    data = item["data"]
    other = data["other"]
    apartment_id = _meter(engine, user, data)["apartment_id"]
    periods = engine.store.periods(user.account, apartment_id)
    [closed] = [p for p in periods if p["start"] == other["closes_period"]]
    engine.store.update_reading(
        user.account,
        data["serial"],
        other["date"],
        other["seq"],
        opens_period=None,
        closes_period=None,
    )
    reading = save_reading(
        engine, user, item, opens=other["opens_period"], closes=other["closes_period"]
    )
    period_change.realign(engine, user, apartment_id, other["closes_period"], other["opens_period"])
    engine.report_due(apartment_id, other["closes_period"], closed["closed_on"])
    return _done(engine, user, item, reading)


def _done(engine, user, item, reading, then=None) -> list[Reply]:
    """Finishes a saved reading: a two-number display's choice becomes the meter's, the item
    leaves the queue, and the summary follows. then, a step that writes more, runs once the item
    has left the queue and before the summary is built, so the summary shows what it wrote; its
    replies follow the summary."""
    data = item["data"]
    meter = _meter(engine, user, data)
    if meter["display_choice"] is None and display_choice(data) is not None:
        engine.store.update_meter(
            user.account, meter["serial"], display_choice=display_choice(data)
        )
    drop(engine, user, item, [], state="saved")
    later = [] if then is None else then()
    return [summary(engine, user, meter, reading), *later]
