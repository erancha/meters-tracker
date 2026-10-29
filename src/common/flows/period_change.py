"""Period-change flow: a change_period request moves the open period's start or length, or
opens a period on a vacant apartment, after a card showing the effect is confirmed. Also here:
realign, which keeps the period rows in step when a closing reading moves.

Invariant: a closed period's closed_on is its latest closing reading's date, and the period
after it starts the next day; moving a period re-flags its opening readings.
"""

from common import texts
from common.dates import parse_iso
from common.flows import (
    AWAITING,
    Reply,
    ask,
    current_period,
    dated_rows,
    decide,
    drop,
    flagged,
    make_card,
    move_period,
    new_item,
    next_day,
    not_understood,
    put_period,
    with_apartment,
)

KIND = "period_change"


def card(item) -> dict:
    data = item["data"]
    text = texts.change_card(
        data["apartment"],
        parse_iso(data["start"]),
        data["months"],
        data["current_start"] is None,
        dated_rows(data["reflag"]),
    )
    return make_card(item, "change_period", text, ["confirm", "reject"])


def answer(engine, user, item, action) -> list[Reply]:
    key, _ = decide(engine, card(item), action)
    if key == "reject":
        return drop(engine, user, item, [Reply(texts.change_cancelled())])
    if key == "confirm":
        return _apply_change(engine, user, item)
    return not_understood(card(item))


def change(engine, user, intent) -> list[Reply]:
    """A change_period intent: proposes moving the open period's start or length, or opening a
    period on a vacant apartment (starting today unless a date was given)."""

    def act(apartment):
        current = current_period(engine, user, apartment["id"])
        reflag = []
        if current is None:
            start = engine.clock().isoformat() if intent.date is None else intent.date
            months = apartment["period_months"] if intent.months is None else intent.months
        else:
            start = current["start"] if intent.date is None else intent.date
            months = current["months"] if intent.months is None else intent.months
            if start != current["start"]:
                reflag = [
                    [m["type"], m["serial"], r["value"], r["date"]]
                    for m, r in flagged(
                        engine, user, apartment["id"], "opens_period", current["start"]
                    )
                ]
        data = {
            "apartment_id": apartment["id"],
            "apartment": apartment["name"],
            "start": start,
            "months": months,
            "reflag": reflag,
            "current_start": None if current is None else current["start"],
        }
        item = new_item(engine.stamp(), KIND, AWAITING, "change", data)
        return ask(engine, user, item, "change", card)

    return with_apartment(engine, user, intent.name, act)


def _apply_change(engine, user, item) -> list[Reply]:
    data = item["data"]
    current = current_period(engine, user, data["apartment_id"])
    current_start = None if current is None else current["start"]
    if current_start != data["current_start"]:
        raise ValueError(f"open period is {current_start}, card was for {data['current_start']}")
    start, months = data["start"], data["months"]
    if current is None:
        put_period(engine, user, data["apartment_id"], start, months)
        reply = texts.period_opened(data["apartment"], parse_iso(start), months)
    elif start == current["start"]:
        engine.store.update_period(user.account, data["apartment_id"], start, months=months)
        reply = texts.period_changed(data["apartment"], parse_iso(start), months)
    else:
        move_period(engine, user, data["apartment_id"], current, start, months)
        reply = texts.period_changed(data["apartment"], parse_iso(start), months)
    return drop(engine, user, item, [Reply(reply)])


def realign(engine, user, apartment_id, closed_start, next_start):
    """After a closing reading moved: closed_on becomes the latest closing reading's date again,
    and the next period (when there is one) moves to start the day after it."""
    closed_on = max(
        r["date"] for _, r in flagged(engine, user, apartment_id, "closes_period", closed_start)
    )
    engine.store.update_period(user.account, apartment_id, closed_start, closed_on=closed_on)
    if next_start is None or next_day(closed_on) == next_start:
        return
    [following] = [
        p for p in engine.store.periods(user.account, apartment_id) if p["start"] == next_start
    ]
    move_period(engine, user, apartment_id, following, next_day(closed_on), following["months"])
