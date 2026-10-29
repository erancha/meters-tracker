"""Meter photo flow: a photo becomes a proposed reading, and a confirmed one becomes a saved
reading of a known meter. An unknown serial is handed to the link flow; the close or vacate
option on a reading hands the apartment to the close flow after the reading is saved.

Steps: unreadable -> reading -> one of duplicate, lower_reading, swap, propose_reclose,
open_period, propose_close -> saved. A reading is never saved twice for one meter and date. A
back-dated reading never displaces a confirmed closing reading without the user's choice. A
two-number display's choice becomes the meter's only when a reading is saved. A failed read
leaves no pending row, and a retake replaces the unreadable card it answers, carrying its
photos as the ones the model failed on.
"""

from datetime import datetime

from common import dates, texts
from common.dates import parse_iso
from common.flows import (
    FAILURE_FIELDS,
    READING,
    Reply,
    ask,
    close_period,
    current_period,
    decide,
    display_choice,
    drop,
    edit_reading,
    failure_record,
    intent_date,
    link_meter,
    make_card,
    new_item,
    not_understood,
    period_change,
    place,
    propose,
    put_period,
    reading_options,
    reading_text,
    save_reading,
    summary,
)
from common.model import MeterReadingResult
from common.periods import apartment_meters, is_closing_candidate, period_end, within_reclose_window

KIND = "meter_photo"
SWAP_OPTIONS = ["reclose", "keep_open"]


def _date(data, key):
    return parse_iso(data[key]["date"])


STEPS = {
    "unreadable": (lambda d: texts.unreadable(d["unreadable"]), lambda d: ["reject"]),
    "reading": (reading_text, reading_options),
    "duplicate": (
        lambda d: texts.duplicate_reading(d["other"]["value"], _date(d, "other")),
        lambda d: ["confirm", "reject"],
    ),
    "lower_reading": (
        lambda d: texts.lower_reading(d["other"], d["value"]),
        lambda d: ["misread", "replaced", "reset"],
    ),
    "swap": (lambda d: texts.propose_swap(_date(d, "other")), lambda d: SWAP_OPTIONS),
    "propose_reclose": (lambda d: texts.propose_reclose(_date(d, "other")), lambda d: SWAP_OPTIONS),
    "open_period": (
        lambda d: texts.propose_open(parse_iso(d["reading_date"])),
        lambda d: ["confirm", "reject"],
    ),
    "propose_close": (
        lambda d: texts.propose_close(parse_iso(d["period_end"])),
        lambda d: ["close", "keep_open", "vacate"],
    ),
}


def card(item) -> dict:
    text, options = STEPS[item["step"]]
    return make_card(item, item["step"], text(item["data"]), options(item["data"]))


def start(engine, user, seq, image_key, image, media_type, note) -> list[Reply]:
    close_period.park_waiting(engine, user)
    data = {
        "image_key": image_key,
        "read_started": dates.now().isoformat(),
        "failed_photos": [],
    }
    item = new_item(seq, KIND, READING, None, data)
    engine.store.put_pending(user.email, item)
    try:
        result = engine.model.read_meter(image, media_type)
        reading_date = _reading_date(engine, note)
    except Exception:
        engine.store.delete_pending(user.email, seq)
        raise
    _set_read(item, result, reading_date)
    _absorb_superseded(engine, user, item)
    return route(engine, user, item)


def _set_read(item, result: MeterReadingResult, reading_date):
    item["data"].update(result.model_dump(), reading_date=reading_date, value=None, resets=False)


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
        item["data"]["failed_photos"].extend(
            [*other["data"]["failed_photos"], other["data"]["image_key"]]
        )
    engine.store.put_pending(user.email, item)
    for other in absorbed:
        engine.store.delete_pending(user.email, other["seq"])


def _reading_date(engine, note) -> str:
    if note is not None:
        intent = engine.model.classify(note, "")
        if intent.intent == "set_date":
            return intent_date(intent)
    return engine.clock().isoformat()


def route(engine, user, item) -> list[Reply]:
    data = item["data"]
    meter = None if data["serial"] is None else engine.store.meter(user.account, data["serial"])
    if data["meter_type"] == "unknown" and meter is None:
        return drop(engine, user, item, [Reply(texts.not_a_meter())])
    if _no_serial(data):
        data["unreadable"] = [texts.UNREADABLE_SERIAL]
        return ask(engine, user, item, "unreadable", card)
    if meter is not None and meter["retired_at"] is not None:
        successor = _successor(engine, user, meter)
        return drop(
            engine, user, item, [Reply(texts.retired_meter(meter["serial"], successor["serial"]))]
        )
    if data["value"] is None and not propose(
        data, None if meter is None else meter["display_choice"]
    ):
        data["unreadable"] = [texts.UNREADABLE_VALUE]
        return ask(engine, user, item, "unreadable", card)
    if meter is None:
        return link_meter.start(engine, user, item)
    apartment, prop = place(engine, user, meter["apartment_id"])
    data.update(meter_type=meter["type"], apartment=apartment["name"], property=prop["name"])
    return ask(engine, user, item, "reading", card)


def _successor(engine, user, meter) -> dict:
    [live] = [
        m
        for m in apartment_meters(engine.store.meters(user.account), meter["apartment_id"])
        if m["type"] == meter["type"]
    ]
    return live


def answer(engine, user, item, action) -> list[Reply]:
    data, step = item["data"], item["step"]
    key, intent = decide(engine, card(item), action)
    if key == "reject":
        return drop(engine, user, item, [Reply(texts.discarded())])
    if step == "unreadable" and edit_reading(data, key, intent):
        return route(engine, user, item)
    if step == "reading" and edit_reading(data, key, intent):
        return ask(engine, user, item, step, card)
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
    if step in ("swap", "propose_reclose") and key == "reclose":
        return _reclose(engine, user, item)
    if step == "open_period" and key == "confirm":
        apartment, _ = place(engine, user, _meter(engine, user, data)["apartment_id"])
        put_period(engine, user, apartment["id"], data["reading_date"], apartment["period_months"])
        return _save(engine, user, item)
    if key == "keep_open":
        return _save(engine, user, item)
    if step == "propose_close" and key in ("close", "vacate"):
        replies = _save(engine, user, item)
        apartment_id = _meter(engine, user, data)["apartment_id"]
        return [*replies, *close_period.start(engine, user, apartment_id, vacate=key == "vacate")]
    return not_understood(card(item))


def _meter(engine, user, data) -> dict:
    return engine.store.meter(user.account, data["serial"])


def _check(engine, user, item, lower_checked=False) -> list[Reply]:
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
    periods = engine.store.periods(user.account, meter["apartment_id"])
    current = current_period(engine, user, meter["apartment_id"])
    if current is None:
        return ask(engine, user, item, "open_period", card)
    closings = [r for r in readings if r["closes_period"] is not None]
    if closings:
        closing = closings[-1]
        [closed] = [p for p in periods if p["start"] == closing["closes_period"]]
        step = _closing_step(closing, closed, day, engine.config["reclose_days"])
        if step is not None:
            data["other"] = closing
            return ask(engine, user, item, step, card)
    parked = close_period.parked(engine, user, meter["apartment_id"])
    if parked is None and is_closing_candidate(current, parse_iso(day)):
        data["period_end"] = period_end(current).isoformat()
        return ask(engine, user, item, "propose_close", card)
    return [*_save(engine, user, item), *close_period.resume(engine, user, parked)]


def _closing_step(closing, closed, day, reclose_days) -> str | None:
    """The step a reading near the meter's latest closing reading calls for: a back-dated one
    that would close the same period earlier offers a swap; one shortly after offers a re-close."""
    if day < closing["date"] and is_closing_candidate(closed, parse_iso(day)):
        return "swap"
    if closing["date"] < day and within_reclose_window(
        parse_iso(closing["date"]), parse_iso(day), reclose_days
    ):
        return "propose_reclose"
    return None


def _save(engine, user, item) -> list[Reply]:
    meter = _meter(engine, user, item["data"])
    current = current_period(engine, user, meter["apartment_id"])
    opens = None
    if current is not None:
        readings = engine.store.readings(user.account, meter["serial"])
        if all(r["opens_period"] != current["start"] for r in readings):
            opens = current["start"]
    return _done(engine, user, item, save_reading(engine, user, item, opens=opens))


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
    period_change.realign(
        engine,
        user,
        _meter(engine, user, data)["apartment_id"],
        other["closes_period"],
        other["opens_period"],
    )
    return _done(engine, user, item, reading)


def _done(engine, user, item, reading) -> list[Reply]:
    """Finishes a saved reading: a two-number display's choice becomes the meter's, the item
    leaves the queue, and the summary follows."""
    data = item["data"]
    meter = _meter(engine, user, data)
    if meter["display_choice"] is None and display_choice(data) is not None:
        engine.store.update_meter(
            user.account, meter["serial"], display_choice=display_choice(data)
        )
    return drop(engine, user, item, [summary(engine, user, meter, reading)])
