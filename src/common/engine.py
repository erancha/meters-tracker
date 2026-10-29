"""The conversation engine: one turn in, replies and a snapshot out. It records every user action
and every reply in the user's message history, then routes the action: a photo starts the meter
photo flow, a choice goes to the pending item it names once it claims that item, text goes to the
oldest awaiting item, and text with nothing awaiting is classified into a close, vacate or
period-change request.

The snapshot is the oldest awaiting item's card plus the count of items behind it; the browser
shows it and never orders the queue itself. A photo still being read after the api Lambda's time
limit was being read by an invocation that was killed, so the snapshot turns it into a retake
card and the queue clears. "Today" is the injected clock throughout, including the date prefix
of every pending seq and message timestamp, so the once-a-day greeting and the reading dates
agree on what day it is.
"""

import secrets
from datetime import timedelta

from common import dates, texts
from common.flows import (
    AWAITING,
    READING,
    Action,
    AlreadyHandled,
    Choice,
    Reply,
    Snapshot,
    TurnResult,
    close_period,
    link_meter,
    meter_photo,
    period_change,
)

__all__ = ["Action", "AlreadyHandled", "Choice", "Engine", "Reply", "Snapshot", "TurnResult"]

FLOWS = {
    meter_photo.KIND: meter_photo,
    link_meter.KIND: link_meter,
    close_period.KIND: close_period,
    period_change.KIND: period_change,
}


class Engine:
    """Runs one user turn against the store, model and files it is built with, and computes the
    snapshot the browser shows after it."""

    def __init__(
        self,
        store,
        model,
        files,
        config: dict,
        lambda_timeout: timedelta,
        model_name: str,
        clock=dates.today,
    ):
        self.store = store
        self.model = model
        self.files = files
        self.config = config
        self.lambda_timeout = lambda_timeout
        self.model_name = model_name
        self.clock = clock

    def stamp(self) -> str:
        """A sortable, unique key for a pending item or a message: the clock's date, the wall
        time to the microsecond, and a random suffix for two writes in the same microsecond."""
        time_of_day = dates.now().strftime("%H:%M:%S.%f")
        return f"{self.clock().isoformat()}T{time_of_day}_{secrets.token_hex(2)}"

    def turn(self, user, action: Action) -> TurnResult:
        replies = self._dispatch(user, action)
        for reply in replies:
            self._append(user, "assistant", reply.text, reply.card, reply.image_key)
        return TurnResult(replies, self.snapshot(user))

    def _dispatch(self, user, action: Action) -> list[Reply]:
        if action.image is not None:
            seq = self.stamp()
            key = self.files.put_image(user.account, seq, action.image, action.media_type)
            self._append(user, "user", action.note, image_key=key)
            return meter_photo.start(
                self, user, seq, key, action.image, action.media_type, action.note
            )
        if action.choice is not None:
            item, label = self._claim(user, action.choice)
            self._append(user, "user", label)
            return FLOWS[item["kind"]].answer(self, user, item, action)
        self._append(user, "user", action.text)
        awaiting = self._awaiting(user)
        if awaiting:
            item = awaiting[0]
            return FLOWS[item["kind"]].answer(self, user, item, action)
        return self._request(user, action.text)

    def _claim(self, user, choice: Choice) -> tuple[dict, str]:
        """The awaiting item a choice answers, with the chosen option's label. The item is
        claimed by advancing its version, so of two requests carrying one card exactly one
        proceeds. Raises AlreadyHandled when the item no longer shows that card or the card has
        no such option."""
        item = {i["seq"]: i for i in self.store.pending(user.email)}.get(choice.pending)
        if item is None or item["status"] != AWAITING or item["version"] != choice.version:
            raise AlreadyHandled(f"pending item {choice.pending} no longer shows that card")
        options = FLOWS[item["kind"]].card(item)["options"]
        labels = {o["id"]: o["label"] for o in options}
        if choice.option not in labels:
            raise AlreadyHandled(f"option {choice.option} is not on the card")
        if not self.store.claim_pending(user.email, item["seq"], item["version"], AWAITING):
            raise AlreadyHandled(f"pending item {choice.pending} was claimed by another request")
        item["version"] += 1
        return item, labels[choice.option]

    def _request(self, user, text) -> list[Reply]:
        intent = self.model.classify(text, "")
        if intent.intent in ("close_period", "vacate"):
            return close_period.request(self, user, intent)
        if intent.intent == "change_period":
            return period_change.change(self, user, intent)
        return [Reply(texts.unknown_intent())]

    def _awaiting(self, user) -> list[dict]:
        return [i for i in self.store.pending(user.email) if i["status"] == AWAITING]

    def snapshot(self, user) -> Snapshot:
        pending = self.store.pending(user.email)
        for item in pending:
            if item["status"] == READING and meter_photo.stalled(self, item):
                meter_photo.ask_retake(self, user, item)
        queue = [i for i in pending if i["status"] in (READING, AWAITING)]
        awaiting = [i for i in queue if i["status"] == AWAITING]
        if not awaiting:
            return Snapshot(None, len(queue))
        oldest = awaiting[0]
        return Snapshot(FLOWS[oldest["kind"]].card(oldest), len(queue) - 1)

    def load(self, user) -> tuple[list[dict], Snapshot]:
        messages = self.store.messages(user.email)
        today = self.clock().isoformat()
        if not any(m["at"].startswith(today) for m in messages):
            due = close_period.due_for_close(self, user)
            if due:
                messages.append(self._append(user, "assistant", texts.greeting(due)))
        return messages, self.snapshot(user)

    def _append(self, user, role, text, card=None, image_key=None) -> dict:
        message = {
            "at": self.stamp(),
            "role": role,
            "text": text,
            "card": card,
            "image_key": image_key,
            "pdf_key": None,
        }
        self.store.append_message(user.email, message)
        return message
