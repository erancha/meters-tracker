"""The conversation engine: one turn in, replies and a snapshot out. It records every user action
and every reply in the user's message history, then routes the action: a photo starts the meter
photo flow, a choice goes to the pending item it names once it claims that item, text goes to the
oldest awaiting item, and text with nothing awaiting is classified into a close, vacate,
period-change or tariffs request, with the account's apartment names, so the request names one
of them exactly. A standby item (a request for tariffs) waits without
blocking the queue: text with nothing awaiting is classified with its card as context, and only
the answers it takes go to it.

A text or photo is stored before it is handled, so when handling fails the message is marked
failed and a retry handles that same message again, with no second message. A retried photo is
read from its stored copy and keeps the day it was sent as its reading date.

The snapshot is the oldest awaiting item's card, else the oldest standby item's, plus the count of
items behind it and whether a request for tariffs is open, even behind other cards; the browser
shows it and never orders the queue itself. A photo still being read after the api Lambda's time
limit was being read by an invocation that was killed, so the snapshot turns it into a retake card
and the queue clears. "Today" is the injected clock throughout, including the date prefix of every
pending seq and message timestamp, so the once-a-day greeting and the reading dates agree on what
day it is.

Flows mark a period for a report during a turn and the engine sends it once the turn's replies are
stored, so a reading that also closes its period sends one report, with the final figures. A
report that fails is logged and never fails the turn: the reading is already saved.
"""

import secrets
from datetime import timedelta

from common import dates, report, texts
from common.flows import (
    AWAITING,
    READING,
    STANDBY,
    Action,
    AlreadyHandled,
    Choice,
    MessageFailed,
    Reply,
    Snapshot,
    TurnResult,
    close_period,
    link_meter,
    meter_photo,
    period_change,
    request_context,
    tariffs,
)
from common.log import get_logger

logger = get_logger(__name__)

__all__ = [
    "Action",
    "AlreadyHandled",
    "Choice",
    "Engine",
    "MessageFailed",
    "Reply",
    "Snapshot",
    "TurnResult",
]

FLOWS = {
    meter_photo.KIND: meter_photo,
    link_meter.KIND: link_meter,
    close_period.KIND: close_period,
    period_change.KIND: period_change,
    tariffs.KIND: tariffs,
}


class Engine:
    """Runs one user turn against the store, model and files it is built with, and computes the
    snapshot the browser shows after it."""

    def __init__(
        self,
        store,
        model,
        files,
        mailer,
        config: dict,
        lambda_timeout: timedelta,
        model_name: str,
        clock=dates.today,
    ):
        self.store = store
        self.model = model
        self.files = files
        self.mailer = mailer
        self.config = config
        self.lambda_timeout = lambda_timeout
        self.model_name = model_name
        self.clock = clock
        # Key: (apartment id, period start)
        # Value: ISO date of the report this one replaces, or None
        self._reports_due = {}

    def report_due(self, apartment_id, period_start, ignored=None):
        self._reports_due[(apartment_id, period_start)] = ignored

    def stamp(self) -> str:
        """A sortable, unique key for a pending item or a message: the clock's date, the wall
        time to the microsecond, and a random suffix for two writes in the same microsecond."""
        time_of_day = dates.now().strftime("%H:%M:%S.%f")
        return f"{self.clock().isoformat()}T{time_of_day}_{secrets.token_hex(2)}"

    def turn(self, user, action: Action) -> TurnResult:
        if action.retry is not None:
            return self._result(user, self._retry(user, action.retry))
        if action.choice is not None:
            return self._result(user, self._choose(user, action))
        if action.image is not None:
            seq = self.stamp()
            image_key = self.files.put_image(user.account, seq, action.image, action.media_type)
            message = self._append(user, "user", action.note, image_key=image_key)
            sent_on = self.clock().isoformat()
            replies = self._handle(
                user,
                message,
                lambda: meter_photo.start(
                    self,
                    user,
                    seq,
                    image_key,
                    action.image,
                    action.media_type,
                    action.note,
                    sent_on,
                    message["at"],
                ),
            )
            return self._result(user, replies, image_key)
        message = self._append(user, "user", action.text)
        return self._result(user, self._handle(user, message, lambda: self._text(user, action)))

    def _result(self, user, replies: list[Reply], image_key=None) -> TurnResult:
        for reply in replies:
            self._append(user, "assistant", reply.text, reply.card, reply.image_key)
        self._send_reports(user)
        return TurnResult(replies, self.snapshot(user), image_key)

    def _send_reports(self, user):
        due, self._reports_due = self._reports_due, {}
        for (apartment_id, period_start), ignored in due.items():
            try:
                built = report.build(self, user, apartment_id, period_start, ignored)
                report.send(built, self.mailer, user.account)
            except Exception:
                logger.exception(
                    "sending a period report failed apartment=%s period=%s",
                    apartment_id,
                    period_start,
                )

    def _handle(self, user, message, handler) -> list[Reply]:
        """Runs a stored message's handler; any failure marks the message and raises
        MessageFailed, except AlreadyHandled, which leaves the message as it is."""
        try:
            return handler()
        except AlreadyHandled:
            raise
        except Exception as error:
            self.store.mark_message_failed(user.email, message["at"])
            raise MessageFailed(message["at"]) from error

    def _retry(self, user, at) -> list[Reply]:
        """Handles a failed message again; a request that lost the claim is AlreadyHandled."""
        if not self.store.claim_failed_message(user.email, at, self.stamp()):
            raise AlreadyHandled(f"message {at} is not marked failed")
        message = self.store.message(user.email, at)
        if message["image_key"] is None:
            return self._handle(
                user, message, lambda: self._text(user, Action(text=message["text"]))
            )
        return self._handle(user, message, lambda: self._photo_again(user, message))

    def _photo_again(self, user, message) -> list[Reply]:
        image, media_type = self.files.get_image(message["image_key"])
        sent_on = message["at"][: len("YYYY-MM-DD")]
        return meter_photo.start(
            self,
            user,
            self.stamp(),
            message["image_key"],
            image,
            media_type,
            message["text"],
            sent_on,
            message["at"],
        )

    def _choose(self, user, action: Action) -> list[Reply]:
        item, label = self._claim(user, action.choice)
        answered = {"pending": action.choice.pending, "version": action.choice.version}
        self._append(user, "user", label, choice=answered)
        return FLOWS[item["kind"]].answer(self, user, item, action)

    def _text(self, user, action: Action) -> list[Reply]:
        awaiting = self._awaiting(user)
        if awaiting:
            item = awaiting[0]
            return FLOWS[item["kind"]].answer(self, user, item, action)
        names = sorted(a["name"] for a in self.store.apartments(user.account))
        standby = self._with_status(user, STANDBY)
        if not standby:
            context = request_context(names)
            return self._request(user, self.model.classify(action.text, context, self.clock()))
        item = standby[0]
        flow = FLOWS[item["kind"]]
        context = request_context(names, flow.card(item))
        intent = self.model.classify(action.text, context, self.clock())
        if intent.intent in flow.STANDBY_ANSWERS:
            return flow.answer_standby(self, user, item, intent)
        return self._request(user, intent)

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

    def _request(self, user, intent) -> list[Reply]:
        if intent.intent in ("close_period", "vacate"):
            return close_period.request(self, user, intent)
        if intent.intent == "change_period":
            return period_change.change(self, user, intent)
        if intent.intent == "set_tariffs":
            return tariffs.request(self, user, intent)
        return [Reply(texts.unknown_intent())]

    def _awaiting(self, user) -> list[dict]:
        return self._with_status(user, AWAITING)

    def _with_status(self, user, status) -> list[dict]:
        return [i for i in self.store.pending(user.email) if i["status"] == status]

    def snapshot(self, user) -> Snapshot:
        pending = self.store.pending(user.email)
        for item in pending:
            if item["status"] == READING and meter_photo.stalled(self, item):
                meter_photo.ask_retake(self, user, item)
        queue = [i for i in pending if i["status"] in (READING, AWAITING, STANDBY)]
        missing = any(i["kind"] == tariffs.KIND and i["step"] == tariffs.ASK for i in queue)
        shown = [i for i in queue if i["status"] == AWAITING] or [
            i for i in queue if i["status"] == STANDBY
        ]
        if not shown:
            return Snapshot(None, len(queue), missing)
        oldest = shown[0]
        return Snapshot(FLOWS[oldest["kind"]].card(oldest), len(queue) - 1, missing)

    def load(self, user, day=None) -> tuple[list[dict], Snapshot]:
        """The messages of day, today when None, with the snapshot. An address with no message
        at all gets the welcome, and the first load of today the greeting when a close is due."""
        today = self.clock().isoformat()
        day = today if day is None else day
        messages = self.store.messages(user.email, day)
        if not self.store.has_messages(user.email):
            messages.append(self._append(user, "assistant", texts.welcome()))
        elif day == today and not messages:
            due = close_period.due_for_close(self, user)
            if due:
                messages.append(self._append(user, "assistant", texts.greeting(due)))
        return messages, self.snapshot(user)

    def _append(self, user, role, text, card=None, image_key=None, choice=None) -> dict:
        """Records one message. A user's choice is recorded as the chosen option's label, with
        choice naming the card it answered by pending seq and version."""
        message = {
            "at": self.stamp(),
            "role": role,
            "text": text,
            "card": card,
            "image_key": image_key,
            "pdf_key": None,
            "choice": choice,
        }
        self.store.append_message(user.email, message)
        return message
