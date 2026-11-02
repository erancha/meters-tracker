"""Question flow: a change to saved records or to a dropped photo whose value is missing or does not
fit its field, held as a card that asks for that value, so the answer fills the field asked about
and never a card waiting behind it.

The item's data holds the message's operations as plain dicts, the index of the one asked about,
and that field's name, question and how-to text; the card shows the question, naming the value on
record, then the how-to text. Its one field is the field asked about, which a bare answer fills.
The answer completes the asked operation and sends every operation on as the message would have
gone, so a value that still does not fit asks again. At most one question waits, since a new one
replaces it, and it comes first in the queue, since it is what was just asked. The first card of
any newer item drops it, so its answer never takes text meant for that card. It holds no record:
the operations are planned again from a fresh read once answered.
"""

from common import texts
from common.fields import InvalidValue
from common.flows import (
    AWAITING,
    QUESTION,
    Reply,
    ask,
    decide,
    drop,
    make_card,
    new_item,
    not_understood,
)
from common.model import Operation

KIND = QUESTION
ASK = "ask"


def card(item) -> dict:
    data = item["data"]
    return make_card(item, KIND, (data["question"], [data["how_to"]]), [])


def form(item) -> dict:
    return {item["data"]["field"]: None}


def default_field(item) -> str:
    return item["data"]["field"]


def refresh(engine, user, item):
    """Holds no record."""


def start(engine, user, operations: list[Operation], error: InvalidValue) -> list[Reply]:
    """Asks for the value of the operation error names, in place of any waiting question; nothing
    is written. error carries the operation's target and the value on record."""
    asked = next(
        index
        for index, operation in enumerate(operations)
        if operation.target == error.target and operation.field == error.field.name
    )
    data = {
        "operations": [operation.model_dump() for operation in operations],
        "asked": asked,
        "field": error.field.name,
        "question": error.field.question.format(current=error.current),
        "how_to": error.field.how_to,
    }
    item = new_item(engine.stamp(), KIND, AWAITING, ASK, data)
    replaced = [i for i in engine.store.pending(user.email) if i["kind"] == KIND]
    shown = ask(engine, user, item, ASK, card)
    for old in replaced:
        drop(engine, user, old, [])
    return shown


def _answers(item, operations) -> bool:
    """True for one value for the field asked about, named or bare."""
    if len(operations) != 1:
        return False
    [operation] = operations
    return operation.action == "set" and operation.field in (None, item["data"]["field"])


def answer(engine, user, item, action) -> list[Reply]:
    key, intent = decide(engine, item, action)
    if key == "reject":
        return drop(engine, user, item, [Reply(texts.question_cancelled())])
    if key != "edit" or not _answers(item, intent.operations):
        return not_understood(card(item), key)
    data = item["data"]
    operations = [Operation(**operation) for operation in data["operations"]]
    asked = operations[data["asked"]]
    operations[data["asked"]] = asked.model_copy(update={"value": intent.operations[0].value})
    drop(engine, user, item, [])
    return engine.route(user, operations)
