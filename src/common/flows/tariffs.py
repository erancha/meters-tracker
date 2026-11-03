"""Tariffs flow: typed prices or a photo of a previous statement become a tariff row after its
card is confirmed; a close that found no tariff in force asks for them.

Steps: ask -> confirm. ask follows a close priced without a tariff. It waits on standby, so it
never blocks the queue: its card has no buttons and shows only when no other card awaits, and
typed text with nothing awaiting reaches it only as an edit of its prices or date (moving it to
confirm), a cancel or a question. A statement photo's card replaces any tariffs item, so at most
one waits at a time, and it leaves the queue when its photo is reopened as a meter's.
confirm shows the prices, the VAT note and the effective date (naming a date taken from a close
as such); the prices and the date take a typed edit on either step, and saving writes the row.

A price not given carries forward from the tariff in force on the effective date, recomputed
whenever the date or the given prices change; with none in force that meter type stays unpriced
(None). A statement whose prices exclude VAT shows them but offers no save until prices are
typed. Saving reports each period, closed or open, whose charge the new row changes, in the same
comparison a correction's card shows, and marks for a report each closed period it prices first.
"""

from common import account, corrections, texts
from common.dates import parse_iso
from common.flows import (
    AWAITING,
    STANDBY,
    CardGone,
    Reply,
    apply_edits,
    ask,
    card_reply,
    decide,
    drop,
    make_card,
    new_item,
    not_understood,
    price_on,
    tag_photo,
)
from common.model import StatementResult
from common.periods import PRICES, price_in

KIND = "tariffs"
ASK = "ask"
CONFIRM = "confirm"
# The typed answers the standby ask item takes; any other request passes it by.
STANDBY_ANSWERS = ("edit", "set_tariffs", "reject", "explain")
# Card field -> the meter type it prices.
PRICE_FIELDS = {"electricity_price": "electricity", "water_price": "water"}


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


def form(item) -> dict:
    data = item["data"]
    prices = {field: data["prices"][kind] for field, kind in PRICE_FIELDS.items()}
    return {**prices, "effective_date": data["effective_date"]}


def default_field(item) -> str | None:
    """None: a bare number could be either price, so a typed value must name its field."""
    return None


def edit(engine, user, item, field, value):
    data = item["data"]
    if field == "effective_date":
        data.update(effective_date=value, from_close=False)
    else:
        data["ex_vat"] = None
        data["given"][PRICE_FIELDS[field]] = value


def after_edit(engine, user, item) -> list[Reply]:
    _resolve(engine, user, item["data"])
    return ask(engine, user, item, CONFIRM, card)


def refresh(engine, user, item):
    """A carried price is a copy of the tariff in force, and a date from a close a copy of the
    earliest closing date with no price, so a correction may change either. Raises CardGone for
    a request from a close once every closing reading is priced."""
    data = item["data"]
    if data["from_close"]:
        unpriced = _earliest_unpriced(engine, user)
        if unpriced is None:
            raise CardGone(texts.tariffs_not_missing())
        data["effective_date"] = unpriced
    _resolve(engine, user, data)


def _earliest_unpriced(engine, user) -> str | None:
    """The earliest date of a closing reading with no price in force for its meter's type."""
    tariffs = engine.store.tariffs(user.account)
    dates = [
        r["date"]
        for meter in engine.store.meters(user.account)
        for r in engine.store.readings(user.account, meter["serial"])
        if r["closes_period"] is not None
        and price_in(tariffs, meter["type"], parse_iso(r["date"])) is None
    ]
    return min(dates, default=None)


def _options(data) -> list[str]:
    priced = any(price is not None for price in data["prices"].values())
    return ["confirm"] if data["ex_vat"] is None and priced else []


def _data(effective_date, from_close, image_key=None) -> dict:
    """from_close marks an effective date taken from a period's closing date, which the card
    names as the reason, until a typed date replaces it. image_key is the statement photo's key,
    so only a card read from a statement has one."""
    return {
        "given": {meter_type: None for meter_type in PRICES},
        "prices": {meter_type: None for meter_type in PRICES},
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
    return after_edit(engine, user, item)


def ask_after_close(engine, user, closing) -> list[Reply]:
    """Asks for tariffs when a closing reading of a (meter, reading) pair in closing has no price
    in force on its date, unless a tariffs item already waits, effective from the earliest closing
    date with no price in the account. The request is stored on standby."""
    unpriced = any(
        price_on(engine, user, meter["type"], parse_iso(r["date"])) is None for meter, r in closing
    )
    if not unpriced or _pending(engine, user):
        return []
    effective = _earliest_unpriced(engine, user)
    item = new_item(engine.stamp(), KIND, STANDBY, ASK, _data(effective, from_close=True))
    engine.store.put_pending(user.email, item)
    return [card_reply(card(item))]


def _pending(engine, user) -> list[dict]:
    return [i for i in engine.store.pending(user.email) if i["kind"] == KIND]


def from_statement(engine, user, item, statement: StatementResult, effective_date) -> list[Reply]:
    """Turns a photo's pending item into a tariffs card holding the statement's prices. It
    replaces the tariffs item already waiting; when that item's date came from a close, the card
    takes the earliest closing date still unpriced, if any. The replaced item leaves the queue
    once the card is stored."""
    replaced = _pending(engine, user)
    from_close = False
    if replaced:
        [old] = replaced
        unpriced = _earliest_unpriced(engine, user) if old["data"]["from_close"] else None
        if unpriced is not None:
            effective_date, from_close = unpriced, True
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
    tag_photo(engine, user, item, "dropped")
    item.update(kind=KIND, data=data)
    replies = ask(engine, user, item, CONFIRM, card)
    for old in replaced:
        engine.store.delete_pending(user.email, old["seq"])
    return replies


def drop_read_from(engine, user, image_key):
    """Takes the card read from a statement photo out of the queue, once the owner says the photo
    shows a meter."""
    for item in _pending(engine, user):
        if item["data"]["source_image_key"] == image_key:
            drop(engine, user, item, [])


def answer(engine, user, item, action) -> list[Reply]:
    key, intent = decide(engine, item, action)
    return _apply(engine, user, item, key, intent)


def answer_standby(engine, user, item, intent) -> list[Reply]:
    """Typed text, already classified, that the standby ask item takes (STANDBY_ANSWERS)."""
    return _apply(engine, user, item, intent.intent, intent)


def _apply(engine, user, item, key, intent) -> list[Reply]:
    data = item["data"]
    if key == "reject":
        return drop(engine, user, item, [Reply(texts.tariffs_cancelled())])
    if key == "edit":
        return apply_edits(engine, user, item, intent.operations)
    # Prices on a card are edits; prices read as a request still land on this card, since a
    # second tariffs item would break the one-at-a-time rule.
    if key == "set_tariffs":
        return _typed(engine, user, item, intent)
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
    before = account.load(engine.store, user.account, user.role)
    engine.store.put_tariff(user.account, row)
    after = before.copy()
    after.tariffs = engine.store.tariffs(user.account)
    changes = corrections.repriced(before, after, engine.clock())
    _mark_first_priced(engine, after, changes)
    lines = [texts.tariffs_saved(parse_iso(data["effective_date"])), *(c.priced for c in changes)]
    return drop(engine, user, item, [Reply("\n".join(lines))])


def _mark_first_priced(engine, after, changes):
    """Marks for a report each closed period in which the new row prices a meter for the first
    time. A period already reported had every meter priced, so none is marked twice."""
    apartment_of = {m["serial"]: m["apartment_id"] for m in after.meters}
    for change in changes:
        first_priced = change.before.amount is None and change.after.amount is not None
        if change.after.consumption.final and first_priced:
            engine.report_due(apartment_of[change.serial], change.start)
