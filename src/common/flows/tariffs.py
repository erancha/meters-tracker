"""Tariffs flow: typed prices or a photo of a previous statement become a tariff row after its
card is confirmed; a close that found no tariff in force asks for them.

Steps: ask -> confirm. ask follows a close priced without a tariff. It waits on standby, so it
never blocks the queue: its card has no buttons and shows only when no other card awaits, and
typed text with nothing awaiting reaches it only as prices (moving it to confirm), a cancel or a
question. A statement photo's card replaces any tariffs item, so at most one waits at a time.
confirm shows the prices, the VAT note and the effective date (naming a date taken from a close
as such); typed prices replace the ones shown, a typed date moves the effective date, and saving
writes the row.

A price not given carries forward from the tariff in force on the effective date, recomputed
whenever the date or the given prices change; with none in force that meter type stays unpriced
(None). A statement whose prices exclude VAT shows them but offers no save until prices are
typed. Saving reports the charge of each closed period the new row prices for the first time.
"""

from common import texts
from common.dates import parse_iso
from common.flows import (
    AWAITING,
    PRICES,
    STANDBY,
    Reply,
    ask,
    by_id,
    card_reply,
    decide,
    drop,
    flagged,
    intent_date,
    make_card,
    new_item,
    not_understood,
    price_in,
    price_on,
)
from common.model import StatementResult
from common.periods import charge, consumption, tariff_on

KIND = "tariffs"
ASK = "ask"
CONFIRM = "confirm"
# The typed answers the standby ask item takes; any other request passes it by.
STANDBY_ANSWERS = ("set_tariffs", "reject", "explain")


def card(item) -> dict:
    data = item["data"]
    if item["step"] == ASK:
        return make_card(item, "tariffs_ask", texts.tariffs_ask(), [])
    text = texts.tariffs_card(
        data["prices"],
        parse_iso(data["effective_date"]),
        data["from_close"],
        data["source_image_key"] is not None,
        data["ex_vat"],
    )
    labels = {"confirm": texts.SAVE_TARIFFS_LABEL}
    return make_card(item, KIND, text, _options(data), labels)


def _options(data) -> list[str]:
    priced = any(price is not None for price in data["prices"].values())
    return ["confirm"] if data["ex_vat"] is None and priced else []


def _data(effective_date, from_close, image_key=None) -> dict:
    """from_close marks an effective date taken from a period's closing date, which the card
    names as the reason, until a typed date replaces it. image_key is the statement photo's key,
    so only a card read from a statement has one."""
    return {
        "given": {meter_type: None for meter_type in PRICES},
        "prices": {},
        "effective_date": effective_date,
        "from_close": from_close,
        "source_image_key": image_key,
        "ex_vat": None,
    }


def _give(data, prices: dict):
    """Takes each price that was given; a None leaves the earlier one."""
    for meter_type, price in prices.items():
        if price is not None:
            data["given"][meter_type] = price


def _resolve(engine, user, data):
    """Sets the prices the card shows: each given price, else the one carried forward."""
    effective = parse_iso(data["effective_date"])
    data["prices"] = {
        meter_type: price if price is not None else price_on(engine, user, meter_type, effective)
        for meter_type, price in data["given"].items()
    }


def _intent_prices(intent) -> dict:
    return {"electricity": intent.electricity_price, "water": intent.water_price}


def request(engine, user, intent) -> list[Reply]:
    """A set_tariffs intent typed with no card pending: a card effective today."""
    data = _data(engine.clock().isoformat(), from_close=False)
    item = new_item(engine.stamp(), KIND, AWAITING, CONFIRM, data)
    return _typed(engine, user, item, intent)


def _typed(engine, user, item, intent) -> list[Reply]:
    data = item["data"]
    data["ex_vat"] = None
    _give(data, _intent_prices(intent))
    _resolve(engine, user, data)
    return ask(engine, user, item, CONFIRM, card)


def ask_after_close(engine, user, closing) -> list[Reply]:
    """Asks for tariffs when a closing reading of a (meter, reading) pair in closing has no price
    in force on its date, effective from the earliest such date, unless a tariffs item already
    waits. The request is stored on standby."""
    unpriced = [
        r["date"]
        for meter, r in closing
        if price_on(engine, user, meter["type"], parse_iso(r["date"])) is None
    ]
    if not unpriced or _pending(engine, user):
        return []
    item = new_item(engine.stamp(), KIND, STANDBY, ASK, _data(min(unpriced), from_close=True))
    engine.store.put_pending(user.email, item)
    return [card_reply(card(item))]


def _pending(engine, user) -> list[dict]:
    return [i for i in engine.store.pending(user.email) if i["kind"] == KIND]


def from_statement(engine, user, item, statement: StatementResult, effective_date) -> list[Reply]:
    """Turns a photo's pending item into a tariffs card holding the statement's prices. It
    replaces the tariffs item already waiting, taking its effective date when that date came
    from a close; the replaced item leaves the queue once the card is stored."""
    replaced = _pending(engine, user)
    from_close = False
    if replaced:
        [old] = replaced
        if old["data"]["from_close"]:
            effective_date, from_close = old["data"]["effective_date"], True
    read = {
        "electricity": statement.electricity_per_kwh,
        "water": statement.water_per_m3,
    }
    data = _data(effective_date, from_close, item["data"]["image_key"])
    if statement.includes_vat:
        _give(data, read)
    else:
        data["ex_vat"] = read
    _resolve(engine, user, data)
    item.update(kind=KIND, data=data)
    replies = ask(engine, user, item, CONFIRM, card)
    for old in replaced:
        engine.store.delete_pending(user.email, old["seq"])
    return replies


def answer(engine, user, item, action) -> list[Reply]:
    key, intent = decide(engine, card(item), action)
    return _apply(engine, user, item, key, intent)


def answer_standby(engine, user, item, intent) -> list[Reply]:
    """Typed text, already classified, that the standby ask item takes (STANDBY_ANSWERS)."""
    return _apply(engine, user, item, intent.intent, intent)


def _apply(engine, user, item, key, intent) -> list[Reply]:
    data = item["data"]
    if key == "reject":
        return drop(engine, user, item, [Reply(texts.tariffs_cancelled())])
    if key == "set_tariffs":
        return _typed(engine, user, item, intent)
    if key == "set_date":
        data.update(effective_date=intent_date(intent), from_close=False)
        _resolve(engine, user, data)
        return ask(engine, user, item, CONFIRM, card)
    if key == "confirm" and _options(data):
        return _save(engine, user, item)
    return not_understood(card(item), key)


def _save(engine, user, item) -> list[Reply]:
    data = item["data"]
    row = {
        "effective_date": data["effective_date"],
        **{column: data["prices"][meter_type] for meter_type, column in PRICES.items()},
        "source_image_key": data["source_image_key"],
    }
    before = engine.store.tariffs(user.account)
    engine.store.put_tariff(user.account, row)
    lines = [
        texts.tariffs_saved(parse_iso(data["effective_date"])),
        *_repriced(engine, user, before, row),
    ]
    return drop(engine, user, item, [Reply("\n".join(lines))])


def _repriced(engine, user, before, row) -> list[str]:
    """The charge of each closed period whose closing reading had no price before row and is
    priced by row now, one line per closing meter."""
    after = engine.store.tariffs(user.account)
    lines = []
    for apartment, period, meter, r in _closing_readings(engine, user):
        d = parse_iso(r["date"])
        price = price_in(after, meter["type"], d)
        if price is None or price_in(before, meter["type"], d) is not None:
            continue
        if tariff_on(after, d)["effective_date"] != row["effective_date"]:
            continue
        units = consumption(engine.store.readings(user.account, meter["serial"]), period).units
        prop = by_id(engine.store.properties(user.account), apartment["property_id"])
        lines.append(
            texts.repriced(
                prop["name"],
                apartment["name"],
                meter["type"],
                parse_iso(period["start"]),
                units,
                charge(units, price),
            )
        )
    return lines


def _closing_readings(engine, user):
    """(apartment, closed period, meter, closing reading) over every closed period."""
    for apartment in engine.store.apartments(user.account):
        for period in engine.store.periods(user.account, apartment["id"]):
            if period["closed_on"] is None:
                continue
            for meter, r in flagged(
                engine, user, apartment["id"], "closes_period", period["start"]
            ):
                yield apartment, period, meter, r
