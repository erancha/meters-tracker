"""Correction flow: a change to saved records, planned on a copy of the account, shown on one card
and written in one transaction on confirmation. Confirmation plans again from a fresh read, and
when the card would say something else it is shown again and nothing is written; a plan that
fails or is refused by then drops the card, saying why. A correction never marks a report due, so
it mails nothing.

The item's data holds the operations as plain dicts and the card's lines, so the card renders with
no store read. The card has no fields. At most one correction waits: a further change to a record
that reaches a card of its own replaces the waiting one in the same turn, so a card the owner has
moved past can no longer be confirmed. An operation that shows no card leaves the waiting one. A
value missing or unfitting its field is asked for through the question flow, which keeps the
operations until the answer. A saved correction that makes a waiting link's serial known turns
that link into its reading card.

Saving records as failed the photos of the readings whose value or serial changed, and brings the
day panel's reading tags on the acting person's own photos in step with the records.

A delegate's new address is judged here against the store, on the card and again on confirmation.
The transaction's conditions on the access rows catch what changes after that, as a refusal. The
same transaction deletes the waiting items of the addresses that leave or join the account: an
item carries no account, so it would run in whichever account its address works in by then, and
its ids would mean nothing there. The card says when the new address loses one. A turn racing the
confirmation itself gets past this, which needs the other person to act within that one request.
After the write, each of those addresses is greeted in its own chat with its new place.
"""

from common import account, corrections, texts
from common.access import DELEGATE, OWNER, User
from common.corrections import Plan
from common.fields import InvalidValue, UnknownField, UnknownTarget
from common.flows import (
    AWAITING,
    FAILURE_FIELDS,
    Reply,
    ask,
    decide,
    drop,
    link_meter,
    make_card,
    new_item,
    not_understood,
    question,
    tag_photo,
)
from common.model import Operation
from common.store import AccessChanged, TooLarge

KIND = "correction"
CONFIRM = "confirm"


def card(item) -> dict:
    return make_card(item, KIND, texts.correction_card(item["data"]["lines"]), ["confirm"])


def form(item) -> dict:
    return {}


def default_field(item) -> str | None:
    return None


def refresh(engine, user, item):
    """Holds no record: confirming plans again from a fresh read."""


def _lines(engine, before, planned: Plan) -> list[str]:
    """The card's lines, each once: a deleted record's line is also among the plan's removals."""
    changes = [c.line for c in planned.changes]
    figures = [f.line for f in planned.figures]
    _, gained = corrections.lost_and_gained(before, planned.after)
    waiting = [texts.waiting_card_lost(a) for a in gained if engine.store.pending(a)]
    return list(dict.fromkeys([*changes, *figures, *planned.warnings, *waiting]))


def _cannot_confirm(engine, before, planned: Plan) -> str | None:
    """Why the plan has nothing to confirm, or None when it can be confirmed."""
    if planned.refusals:
        return "\n".join(planned.refusals)
    if account.diff(before, planned.after).size() == 0:
        return texts.nothing_to_correct()
    refusals = corrections.access_refusals(engine.store, before, planned.after)
    return "\n".join(refusals) if refusals else None


def start(engine, user, operations: list[Operation]) -> list[Reply]:
    """Shows the card of the operations in place of any waiting correction, or answers why there
    is none, or asks for a value missing or unfitting; nothing is written."""
    before = account.load(engine.store, user.account, user.role)
    try:
        planned = corrections.plan(before, operations, engine.clock(), user.role)
    except (UnknownTarget, UnknownField):
        return [Reply(texts.unknown_record([a["name"] for a in before.apartments]))]
    except InvalidValue as error:
        return question.start(engine, user, operations, error)
    reason = _cannot_confirm(engine, before, planned)
    if reason is not None:
        return [Reply(reason)]
    lines = _lines(engine, before, planned)
    data = {"operations": [o.model_dump() for o in operations], "lines": lines}
    item = new_item(engine.stamp(), KIND, AWAITING, CONFIRM, data)
    replaced = [i for i in engine.store.pending(user.email) if i["kind"] == KIND]
    shown = ask(engine, user, item, CONFIRM, card)
    for old in replaced:
        drop(engine, user, old, [])
    return [Reply(texts.correction_replaced()), *shown] if replaced else shown


def answer(engine, user, item, action) -> list[Reply]:
    key, _ = decide(engine, item, action)
    if key == "reject":
        return drop(engine, user, item, [Reply(texts.correction_cancelled())])
    if key == "confirm":
        return _confirm(engine, user, item)
    return not_understood(card(item), key)


def _record_failed_photos(engine, before, after):
    """Adds to failed_photos, once, the photo of each reading whose value or serial the correction
    changes: the model read that photo wrong. A row that names a model keeps it, since that model
    failed on the photos listed first. Such a row is already among the writes, so this adds none."""
    # Key: reading seq
    # Value: the (serial, value) pairs under that seq
    held = {}
    for serial, readings in before.readings.items():
        for r in readings:
            held.setdefault(r["seq"], set()).add((serial, r["value"]))
    photos, model = FAILURE_FIELDS
    for serial, readings in after.readings.items():
        for r in readings:
            failed = r.get(photos, [])
            if (serial, r["value"]) not in held[r["seq"]] and r["image_key"] not in failed:
                r[photos] = [*failed, r["image_key"]]
                r.setdefault(model, engine.model_name)


def _refresh_tags(engine, user, after):
    """Brings the reading tags of the acting person's photos in step with the saved records. A
    message and its reading share an image_key, which a renamed or moved reading keeps, so a saved
    photo whose key no reading holds counts no more: its reading was deleted, or a later photo of
    the same day took its place."""
    apartments = account.records(after, "apartment")
    properties = account.records(after, "property")
    # Key: image_key of a reading that remains
    # Value: that photo's tag
    saved = {}
    for meter in after.meters:
        apartment = apartments[meter["apartment_id"]]
        tag = {
            "state": "saved",
            "meter_type": meter["type"],
            "apartment": apartment["name"],
            "property": properties[apartment["property_id"]]["name"],
        }
        saved.update({r["image_key"]: tag for r in after.readings[meter["serial"]]})
    for message in engine.store.photo_messages(user.email):
        key, tag = message["image_key"], message["reading"]
        if key in saved:
            tag = saved[key]
        elif tag["state"] == "saved":
            tag = {**tag, "state": "dropped"}
        if tag != message["reading"]:
            engine.store.set_message_reading(user.email, message["at"], tag)


def waiting_items(engine, before, after) -> list[tuple[str, dict]]:
    """(address, item) of each item waiting on an address that leaves or joins the account."""
    lost, gained = corrections.lost_and_gained(before, after)
    return [(a, item) for a in [*lost, *gained] for item in engine.store.pending(a)]


def greet_moved(engine, user, before, after):
    """Greets each address that joins the account as its delegate, and each that leaves as the
    owner of its own account, which its next sign-in opens, since its chat shows no other sign of
    the move."""
    lost, gained = corrections.lost_and_gained(before, after)
    for address in gained:
        engine.greet(User(address, user.account, DELEGATE))
    for address in lost:
        engine.greet(User(address, address, OWNER))


def drop_tags(engine, user, cleared: list[tuple[str, dict]]):
    for address, item in cleared:
        tag_photo(engine, User(address, user.account, DELEGATE), item, "dropped")


def _confirm(engine, user, item) -> list[Reply]:
    data = item["data"]
    before = account.load(engine.store, user.account, user.role)
    operations = [Operation(**operation) for operation in data["operations"]]
    try:
        planned = corrections.plan(before, operations, engine.clock(), user.role)
    except UnknownTarget:
        return drop(engine, user, item, [Reply(texts.correction_gone())])
    except InvalidValue as error:
        return drop(engine, user, item, question.start(engine, user, operations, error))
    reason = _cannot_confirm(engine, before, planned)
    if reason is not None:
        return drop(engine, user, item, [Reply(reason)])
    lines = _lines(engine, before, planned)
    if lines != data["lines"]:
        data["lines"] = lines
        return [Reply(texts.correction_changed()), *ask(engine, user, item, CONFIRM, card)]
    _record_failed_photos(engine, before, planned.after)
    writes = account.diff(before, planned.after)
    cleared = waiting_items(engine, before, planned.after)
    if cleared:
        writes.deletes["pending"] = cleared
    try:
        engine.store.apply(user.account, writes)
    except AccessChanged:
        return drop(engine, user, item, [Reply(texts.delegate_address_changed())])
    except TooLarge:
        return drop(engine, user, item, [Reply(texts.correction_too_large(writes.size()))])
    drop_tags(engine, user, cleared)
    greet_moved(engine, user, before, planned.after)
    _refresh_tags(engine, user, planned.after)
    saved = drop(engine, user, item, [Reply("\n".join([texts.correction_saved(), *lines]))])
    return [*saved, *link_meter.hand_over_known(engine, user)]
