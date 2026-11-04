"""Delegates flow: an owner adds an address that works in the account, lists the delegates and
removes one, under the rules of ADR 0002. Only an owner may; a delegate's request is refused.

Steps: add and remove, each one card confirmed before its access row is written or deleted. The
add card's address takes a typed edit; an add request with no usable address shows the add step
as a question card with no buttons, whose bare answer is the address. An address is refused when
it is the owner's own, already a delegate here, or holds data or a delegate row elsewhere
(corrections.address_refusal); the check runs on the request, on an edit of the card and again
on confirmation, since an address can gain data while the card waits. The write goes through the
store's conditional access rows, so two simultaneous requests cannot leave an address with the
wrong role, and it clears the waiting items of the address that joins or leaves, as a corrected
delegate address does; that address is then greeted in its own chat with its new place. A
listing writes nothing.
"""

from common import account, corrections, texts
from common.access import DELEGATE, OWNER
from common.fields import InvalidValue, field_for, parse
from common.flows import (
    AWAITING,
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
)
from common.flows.correction import drop_tags, greet_moved, waiting_items
from common.store import AccessChanged

KIND = "delegates"
ADD = "add"
REMOVE = "remove"
# The add card's one field.
FIELD = "delegate_address"


def card(item) -> dict:
    address = item["data"]["address"]
    if item["step"] == ADD and address is None:
        return make_card(item, "delegate_ask", texts.delegate_ask_card(), [])
    if item["step"] == ADD:
        return make_card(item, "delegate_add", texts.delegate_add_card(address), ["confirm"])
    return make_card(item, "delegate_remove", texts.delegate_remove_card(address), ["confirm"])


def form(item) -> dict:
    return {FIELD: item["data"]["address"]} if item["step"] == ADD else {}


def default_field(item) -> str | None:
    return FIELD if item["step"] == ADD else None


def edit(engine, user, item, field, value):
    item["data"]["address"] = value


def after_edit(engine, user, item) -> list[Reply]:
    """An address the account cannot take is answered with why, and the card stays as stored."""
    refusal = _add_refusal(engine, user, item["data"]["address"])
    if refusal is None:
        return ask(engine, user, item, ADD, card)
    stored = {i["seq"]: i for i in engine.store.pending(user.email)}[item["seq"]]
    return [Reply(refusal), card_reply(card(stored))]


def refresh(engine, user, item):
    """The add card holds no record: confirming checks its address again. The remove card names a
    delegate row, gone once it stopped naming this account."""
    address = item["data"]["address"]
    if item["step"] == REMOVE and not _is_delegate(engine, user, address):
        raise CardGone(texts.delegate_gone(address))


def _is_delegate(engine, user, address) -> bool:
    row = engine.store.access_get(address)
    return row is not None and row["role"] == DELEGATE and row["account"] == user.account


def _add_refusal(engine, user, address) -> str | None:
    if address == user.account:
        return texts.own_address()
    if _is_delegate(engine, user, address):
        return texts.delegate_exists(address)
    return corrections.address_refusal(engine.store, address)


def answer(engine, user, item, action) -> list[Reply]:
    key, intent = decide(engine, item, action)
    if key == "reject":
        return drop(engine, user, item, [Reply(texts.delegates_unchanged())])
    if key == "edit" and item["step"] == ADD:
        return apply_edits(engine, user, item, intent.operations)
    if key == "confirm" and item["data"]["address"] is not None:
        return _add(engine, user, item) if item["step"] == ADD else _remove(engine, user, item)
    return not_understood(card(item), key)


def _write(engine, user, item, delegates, reply: str) -> list[Reply]:
    """Writes the account's delegate rows as delegates maps them from the rows it holds, with the
    waiting items of the address that joins or leaves, in one transaction."""
    before = account.load(engine.store, user.account, user.role)
    after = before.copy()
    after.delegates = delegates(after.delegates)
    writes = account.diff(before, after)
    cleared = waiting_items(engine, before, after)
    if cleared:
        writes.deletes["pending"] = cleared
    try:
        engine.store.apply(user.account, writes)
    except AccessChanged:
        return drop(engine, user, item, [Reply(texts.delegate_address_changed())])
    drop_tags(engine, user, cleared)
    greet_moved(engine, user, before, after)
    return drop(engine, user, item, [Reply(reply)])


def _add(engine, user, item) -> list[Reply]:
    address = item["data"]["address"]
    refusal = _add_refusal(engine, user, address)
    if refusal is not None:
        return drop(engine, user, item, [Reply(refusal)])
    row = {"email": address, "account": user.account, "role": DELEGATE}
    return _write(engine, user, item, lambda rows: [*rows, row], texts.delegate_added(address))


def _remove(engine, user, item) -> list[Reply]:
    address = item["data"]["address"]
    return _write(
        engine,
        user,
        item,
        lambda rows: [r for r in rows if r["email"] != address],
        texts.delegate_removed(address),
    )


def request(engine, user, intent) -> list[Reply]:
    """An add_delegate, list_delegates or remove_delegate intent typed with no card pending."""
    if user.role != OWNER:
        return [Reply(texts.owner_only())]
    if intent.intent == "list_delegates":
        addresses = [d["email"] for d in engine.store.delegates(user.account)]
        return [Reply(texts.delegates_list(addresses) if addresses else texts.no_delegates())]
    try:
        address = parse(field_for("card", FIELD), intent.address, engine.clock())
    except InvalidValue:
        if intent.intent == "remove_delegate":
            return [Reply(texts.ask_delegate_address())]
        address = None
    if intent.intent == "add_delegate":
        refusal = None if address is None else _add_refusal(engine, user, address)
        if refusal is not None:
            return [Reply(refusal)]
        step = ADD
    else:
        if not _is_delegate(engine, user, address):
            return [Reply(texts.unknown_delegate(address))]
        step = REMOVE
    item = new_item(engine.stamp(), KIND, AWAITING, step, {"address": address})
    return ask(engine, user, item, step, card)
