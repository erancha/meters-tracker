"""Close flow: ends an apartment's open period with each live meter's latest reading after its
opening reading, and by default opens the next period the day after the latest of them, with
those same readings as its opening readings. A vacated apartment is closed without a next period.
A close whose closing readings have no tariff in force asks for tariffs after it.

A close the owner chose on a reading's card closes at once; any other close shows its card listing
each meter's closing reading and writes only on that card's answer. The close's reply lists the
closing readings. While a meter has no reading to close with, the item is parked at the waiting
step: it shows no card, and the next saved reading of that apartment resumes it. Asking to close
that apartment again shows the waiting close as a card whose only option cancels it; the next photo
parks it again, so the photo's own card is not queued behind it. A close marks its period for a
report, so the final figures reach the owner however the close was reached. Also here: the list of
apartments the daily greeting names.
"""

from datetime import date

from common import texts
from common.account import reading_id
from common.dates import next_day, parse_iso
from common.flows import (
    AWAITING,
    PARKED,
    CardGone,
    Reply,
    apply_edits,
    ask,
    current_period,
    dated_rows,
    decide,
    drop,
    live_apartment,
    make_card,
    new_item,
    not_understood,
    place,
    put_period,
    tariffs,
    with_apartment,
)
from common.periods import apartment_meters, period_end, period_readings

KIND = "close_period"
CLOSE = "close"
WAITING = "waiting"


def card(item) -> dict:
    data = item["data"]
    if item["step"] == WAITING:
        text = texts.close_waiting(data["apartment"], data["missing"])
        return make_card(item, "close_waiting", text, [])
    options = ["vacate"] if data["vacate"] else ["confirm", "vacate"]
    text = texts.close_card(
        data["apartment"], parse_iso(data["period_start"]), dated_rows(data["closing"])
    )
    return make_card(item, "close", text, options)


def form(item) -> dict:
    """The closing readings' values, each by its record id: the card lists saved readings, so a
    change to one is a correction of that record, which the card's next answer then shows."""
    if item["step"] == WAITING:
        return {}
    data = item["data"]
    return {
        f"reading|{target}": value
        for target, (_, _, value, _) in zip(data["closing_ids"], data["closing"])
    }


def default_field(item) -> str | None:
    return None


def refresh(engine, user, item):
    """Re-reads the apartment, its open period and the readings the card lists. A close card is
    for the period it lists; once another open period has taken its place, it is done."""
    data = item["data"]
    apartment = live_apartment(engine, user, data["apartment_id"])
    period = current_period(engine, user, apartment["id"])
    if period is None:
        raise CardGone(texts.no_open_period(apartment["name"]))
    if item["step"] == CLOSE and period["start"] != data["period_start"]:
        listed = parse_iso(data["period_start"])
        raise CardGone(texts.period_already_closed(apartment["name"], listed))
    closing, missing = _closing(engine, user, apartment["id"], period)
    if missing:
        _hold_missing(data, apartment, missing)
        item["step"] = WAITING
    else:
        _hold_closing(data, apartment, period, closing)
        item["step"] = CLOSE


def _hold_missing(data, apartment, missing):
    data.update(apartment=apartment["name"], missing=[list(m) for m in missing])


def _hold_closing(data, apartment, period, closing):
    data.update(
        apartment=apartment["name"],
        period_start=period["start"],
        closing=[[m["type"], m["serial"], r["value"], r["date"]] for m, r in closing],
        closing_ids=[reading_id(m["serial"], r) for m, r in closing],
    )


def parked(engine, user, apartment_id) -> dict | None:
    found = [
        i
        for i in engine.store.pending(user.email)
        if i["kind"] == KIND and i["step"] == WAITING and i["data"]["apartment_id"] == apartment_id
    ]
    if len(found) > 1:
        raise ValueError(f"{len(found)} parked closes for apartment {apartment_id}")
    return found[0] if found else None


def start(engine, user, apartment_id, vacate, at_once=False) -> list[Reply]:
    """Starts a close, or shows a parked one again. at_once closes without a card, for a close
    the owner chose on a reading's card; a meter with no closing reading still parks it."""
    item = parked(engine, user, apartment_id)
    asked_again = item is not None
    if item is None:
        item = new_item(engine.stamp(), KIND, AWAITING, CLOSE, {"apartment_id": apartment_id})
    item["data"]["vacate"] = vacate
    return _advance(engine, user, item, decided=False, offer_cancel=asked_again, at_once=at_once)


def park_waiting(engine, user):
    """Hides every waiting close's cancel card again, as a new photo arrives."""
    for item in engine.store.pending(user.email):
        if item["kind"] == KIND and item["step"] == WAITING and item["status"] == AWAITING:
            item["status"] = PARKED
            engine.store.put_pending(user.email, item)


def resume(engine, user, item) -> list[Reply]:
    """Retries a parked close after a reading was saved; None means nothing was parked."""
    if item is None:
        return []
    return _advance(engine, user, item, decided=False)


def answer(engine, user, item, action) -> list[Reply]:
    key, intent = decide(engine, item, action)
    if key == "reject":
        return drop(engine, user, item, [Reply(texts.close_cancelled())])
    if key == "edit":
        return apply_edits(engine, user, item, intent.operations)
    if key in ("confirm", "vacate"):
        item["data"]["vacate"] = key == "vacate"
        return _advance(engine, user, item, decided=True)
    return not_understood(card(item), key)


def _advance(engine, user, item, decided, offer_cancel=False, at_once=False) -> list[Reply]:
    """Closes when the owner decided on the readings shown, or at once; parks while a meter
    has no closing reading; otherwise shows the close card."""
    data = item["data"]
    apartment, _ = place(engine, user, data["apartment_id"])
    period = current_period(engine, user, apartment["id"])
    if period is None:
        return drop(engine, user, item, [Reply(texts.no_open_period(apartment["name"]))])
    closing, missing = _closing(engine, user, apartment["id"], period)
    if missing:
        _hold_missing(data, apartment, missing)
        if offer_cancel:
            return ask(engine, user, item, WAITING, card)
        item.update(step=WAITING, status=PARKED)
        engine.store.put_pending(user.email, item)
        return [Reply(texts.missing_photos(apartment["name"], missing))]
    if at_once or decided:
        return _close(engine, user, item, apartment, period, closing)
    _hold_closing(data, apartment, period, closing)
    return ask(engine, user, item, CLOSE, card)


def _closing(engine, user, apartment_id, period) -> tuple[list, list]:
    """Pairs each live meter with its latest reading after the period's opening reading;
    meters with no such reading are returned as (type, serial) in the second list."""
    closing, missing = [], []
    for meter in apartment_meters(engine.store.meters(user.account), apartment_id):
        readings = engine.store.readings(user.account, meter["serial"])
        after_opening = period_readings(readings, period)[1:]
        if after_opening:
            closing.append((meter, after_opening[-1]))
        else:
            missing.append((meter["type"], meter["serial"]))
    return closing, missing


def _close(engine, user, item, apartment, period, closing) -> list[Reply]:
    closed_on = max(r["date"] for _, r in closing)
    next_start = None if item["data"]["vacate"] else next_day(closed_on)
    for meter, r in closing:
        engine.store.update_reading(
            user.account,
            meter["serial"],
            r["date"],
            r["seq"],
            closes_period=period["start"],
            opens_period=next_start,
        )
    engine.store.update_period(user.account, apartment["id"], period["start"], closed_on=closed_on)
    engine.report_due(apartment["id"], period["start"])
    if next_start is not None:
        put_period(engine, user, apartment["id"], next_start, period["months"])
    reply = texts.closed(
        apartment["name"],
        parse_iso(closed_on),
        None if next_start is None else parse_iso(next_start),
        [(m["type"], m["serial"], r["value"], parse_iso(r["date"])) for m, r in closing],
    )
    replies = drop(engine, user, item, [Reply(reply)])
    return [*replies, *tariffs.ask_after_close(engine, user, closing)]


def request(engine, user, intent) -> list[Reply]:
    """A close_period or vacate intent typed with no card pending."""
    return with_apartment(
        engine,
        user,
        intent.name,
        lambda apartment: start(engine, user, apartment["id"], vacate=intent.intent == "vacate"),
    )


def due_for_close(engine, user) -> list[tuple[str, date]]:
    """Apartments whose open period ended before today with a live meter lacking a reading on or
    after the end, as (name, end)."""
    today = engine.clock()
    meters = engine.store.meters(user.account)
    due = []
    for apartment in engine.store.apartments(user.account):
        period = current_period(engine, user, apartment["id"])
        if period is None or today <= period_end(period):
            continue
        end = period_end(period).isoformat()
        for meter in apartment_meters(meters, apartment["id"]):
            readings = period_readings(engine.store.readings(user.account, meter["serial"]), period)
            if all(r["date"] < end for r in readings):
                due.append((apartment["name"], period_end(period)))
                break
    return due
