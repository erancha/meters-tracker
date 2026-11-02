"""Period-change flow: a change_period request opens a period on a vacant apartment, after a card
showing it is confirmed; the card's start and length take a typed edit. The period's length
becomes the apartment's too, written in one transaction with the period. On an apartment with an
open period the request becomes operations on the saved records, so the correction card is the one
card that shows it. Also here: realign, which writes what account.realign works out once a
closing reading moved, so a closed period's closing date and the next period's start stay in step.
"""

from common import account, texts
from common.dates import parse_iso
from common.flows import (
    AWAITING,
    CardGone,
    Reply,
    apply_edits,
    ask,
    correction,
    current_period,
    decide,
    drop,
    live_apartment,
    make_card,
    new_item,
    not_understood,
    with_apartment,
)
from common.model import Operation
from common.periods import new_period, open_period

KIND = "period_change"


def card(item) -> dict:
    data = item["data"]
    text = texts.change_card(data["apartment"], parse_iso(data["start"]), data["months"])
    return make_card(item, "change_period", text, ["confirm"])


def form(item) -> dict:
    data = item["data"]
    return {"period_start": data["start"], "months": data["months"]}


def default_field(item) -> str:
    return "period_start"


def edit(engine, user, item, field, value):
    item["data"]["start" if field == "period_start" else "months"] = value


def after_edit(engine, user, item) -> list[Reply]:
    return ask(engine, user, item, "change", card)


def refresh(engine, user, item):
    """The card opens a period on a vacant apartment, so a period opened since ends it."""
    data = item["data"]
    apartment = live_apartment(engine, user, data["apartment_id"])
    data["apartment"] = apartment["name"]
    if current_period(engine, user, apartment["id"]) is not None:
        raise CardGone(texts.period_already_open(apartment["name"]))


def answer(engine, user, item, action) -> list[Reply]:
    key, intent = decide(engine, item, action)
    if key == "reject":
        return drop(engine, user, item, [Reply(texts.change_cancelled())])
    if key == "edit":
        return apply_edits(engine, user, item, intent.operations)
    if key == "confirm":
        return _open(engine, user, item)
    return not_understood(card(item), key)


def _operations(apartment, current, intent) -> list[Operation]:
    """A request's length is the apartment's as well as its open period's, so it goes through the
    apartment's field."""
    operations = []
    if intent.months is not None:
        operations.append(
            Operation(
                action="set",
                target=f"apartment|{apartment['id']}",
                field="months",
                value=str(intent.months),
            )
        )
    if intent.date is not None:
        operations.append(
            Operation(
                action="set",
                target=f"period|{account.period_id(apartment['id'], current['start'])}",
                field="start",
                value=intent.date,
            )
        )
    return operations


def change(engine, user, intent) -> list[Reply]:
    """A change_period intent: operations on the open period, or a card that opens a period on a
    vacant apartment, starting today unless a date was given."""

    def act(apartment):
        current = current_period(engine, user, apartment["id"])
        if current is not None:
            return correction.start(engine, user, _operations(apartment, current, intent))
        data = {
            "apartment_id": apartment["id"],
            "apartment": apartment["name"],
            "start": engine.clock().isoformat() if intent.date is None else intent.date,
            "months": apartment["period_months"] if intent.months is None else intent.months,
        }
        item = new_item(engine.stamp(), KIND, AWAITING, "change", data)
        return ask(engine, user, item, "change", card)

    return with_apartment(engine, user, intent.name, act)


def _open(engine, user, item) -> list[Reply]:
    data = item["data"]
    apartment_id, start, months = data["apartment_id"], data["start"], data["months"]
    before = account.load(engine.store, user.account, user.role)
    after = before.copy()
    current = open_period(after.periods[apartment_id])
    if current is not None:
        raise ValueError(f"open period is {current['start']}, card was for a vacant apartment")
    after.periods[apartment_id].append(new_period(start, months))
    account.records(after, "apartment")[apartment_id]["period_months"] = months
    engine.store.apply(user.account, account.diff(before, after))
    reply = texts.period_opened(data["apartment"], parse_iso(start), months)
    return drop(engine, user, item, [Reply(reply)])


def realign(engine, user, apartment_id, closed_start, next_start):
    """Writes what account.realign works out after a closing reading was saved in another's
    place."""
    before = account.load(engine.store, user.account, user.role)
    after = before.copy()
    account.realign(after, apartment_id, closed_start, next_start)
    engine.store.apply(user.account, account.diff(before, after))
