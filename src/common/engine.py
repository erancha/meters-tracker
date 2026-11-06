"""The conversation engine: one turn in, replies and a snapshot out. It records every user action
and every reply in the user's message history, then routes the action: a photo starts the meter
photo flow, a choice goes to the pending item it names once it claims that item, and text is
classified with the first awaiting item's card, when one waits, and a lookup of the account's
records, so an operation or a request names a saved record exactly. The context also lists every
waiting card by its seq with its fields, and the user's last dropped photos by their message's at.
An
edit's operations go to what each names, in this order and in the same turn: the card the text was
classified with, a card behind it, a dropped photo, which is read again from its stored copy, and
saved records, through a correction. A how-to question, a message fitting several records and a
request the app lacks are answered alike on any card or none, and the card shows again. Any other
answer goes to the card, and with nothing awaiting text is a close, vacate, period-change or
tariffs request; anything else is answered by ask_back, which says what the assistant can do or
asks a question, and never only that it did not understand. A standby item (a request for tariffs)
waits without blocking the queue: text with nothing awaiting is classified with its card as
context, and only the answers it takes go to it.

A question comes first in the queue, whatever its seq, then a correction, so the card of a change
to saved records never waits behind an older card, and the answer to a question asked for a change
never reaches a card behind it. At most one of each waits, since a new one replaces it. A
correction never marks a report due.

Cards hold copies of saved records, and another person's correction can land while one waits. So
every answer to a waiting card first refreshes it: when its text changed it is shown again and the
answer does nothing, and when its records are gone it is dropped with the reason.

A text or photo is stored before it is handled, so when handling fails the message is marked
failed and a retry handles that same message again, with no second message. A retried photo is
read from its stored copy and keeps the day it was sent as its reading date.

The snapshot is the first awaiting item's card, else the oldest standby item's, plus the count of
items behind it and whether a request for tariffs is open, even behind other cards; the browser
shows it and never orders the queue itself. A photo still being read after the api Lambda's time
limit was being read by an invocation that was killed, so the snapshot turns it into a retake card
and the queue clears. "Today" is the injected clock throughout, including the date prefix of every
pending seq and message timestamp, so the once-a-day greeting and the reading dates agree on what
day it is.

Flows mark a period for a report during a turn: a close, a re-close, a tariff that completes a
closed period's prices, and a late meter's reading that closes a closed period for it. After the
turn's replies the engine sends each marked report that report.missing finds due, and adds a line
to the turn per report: sent, held with what the bill lacks, or failed. A report that fails is
logged and never fails the turn, since the reading is already saved; it is marked on its period,
shown in the owner's chat too when a delegate triggered it, and sent again on the first chat load
of a later day, or at once when a message asks for it, after a check that the owner's address is
a verified SES identity.
"""

import secrets
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import timedelta

from common import account, dates, report, texts
from common.access import OWNER, User
from common.dates import parse_iso
from common.fields import InvalidValue, UnknownField
from common.flows import (
    ASK_BACK_INTENTS,
    ASKED,
    AWAITING,
    CARD_TARGET,
    READING,
    READING_KINDS,
    STANDBY,
    Action,
    AlreadyHandled,
    CardGone,
    Choice,
    MessageFailed,
    Reply,
    Snapshot,
    TurnResult,
    ask,
    ask_back,
    asked_field,
    card_reply,
    card_text,
    classify_context,
    close_period,
    correction,
    delegates,
    drop,
    link_meter,
    message_context,
    meter_photo,
    on_card,
    period_change,
    place,
    question,
    record_operation,
    tariffs,
)
from common.log import get_logger
from common.model import IntentResult, RecordsQuery

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
    correction.KIND: correction,
    question.KIND: question,
    delegates.KIND: delegates,
}

# The kinds that come first in the queue, in this order, whatever their seq.
FIRST_KINDS = (question.KIND, correction.KIND)


# The record name of a dropped photo's id in the classifier's context.
PHOTO_TARGET = "photo"


@dataclass
class Listed:
    """The waiting cards and dropped photos the classifier's context lists, by the id an
    operation names each with after its record name."""

    # Key: pending seq; value: the item, awaiting cards first in queue order, then standby ones
    cards: dict
    # Key: message at; value: the photo's message
    photos: dict


def _held_photos(item) -> list[str]:
    """The image keys a pending item holds: a meter photo's own and those it superseded. A
    tariffs card read from a statement photo holds nothing saved, so the photo stays reopenable,
    and reopening it as a meter's drops the card."""
    data = item["data"]
    if item["kind"] in READING_KINDS:
        return [data["image_key"], *data["failed_photos"]]
    return []


def _questions_and_corrections_first(items: list[dict]) -> list[dict]:
    """The items in queue order, with any question, then any correction, moved ahead of the
    rest."""

    def rank(item):
        kind = item["kind"]
        return FIRST_KINDS.index(kind) if kind in FIRST_KINDS else len(FIRST_KINDS)

    return sorted(items, key=rank)


def _face(card) -> tuple:
    """What a card shows the person, its version aside: a button keeps its id when its label
    changes with the day, so the labels count too."""
    return card_text(card), card["options"]


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

    def flow(self, item):
        """The module that runs the item's flow."""
        return FLOWS[item["kind"]]

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
        """The turn's replies and the snapshot after it, the card it shows rebuilt first."""
        reported = self._send_reports(user)
        self._say(user, [*replies, *reported])
        replies = [*replies, *reported, *self._renew_shown(user)]
        return TurnResult(replies, self.snapshot(user), image_key)

    def _say(self, user, replies: list[Reply]):
        for reply in replies:
            self._append(user, "assistant", reply.text, reply.card, reply.image_key)

    def _renew_shown(self, user) -> list[Reply]:
        """Rebuilds the card the snapshot shows from the stored records, so a card is never shown
        with records a correction, or anyone else, has changed since: a card that differs is shown
        anew, and one whose records are gone is dropped, saying why, and the card that then shows
        is rebuilt in turn. A card an answer took meanwhile is left to that answer. Returns the
        replies, already recorded."""
        replies, rebuilt = [], set()
        while True:
            card = self.snapshot(user).card
            if card is None or card["pending"] in rebuilt:
                return replies
            rebuilt.add(card["pending"])
            # An answer may finish the item between the snapshot and this read.
            item = {i["seq"]: i for i in self.store.pending(user.email)}.get(card["pending"])
            if item is None:
                continue
            renewed = self._renewal(user, item, [], claim=True)
            if renewed is None:
                return replies
            self._say(user, renewed)
            replies.extend(renewed)

    def _send_reports(self, user) -> list[Reply]:
        """Sends the report of each marked period that is due; a line per report for the turn."""
        due, self._reports_due = self._reports_due, {}
        return [
            self._send_report(user, apartment_id, period_start, ignored)
            for (apartment_id, period_start), ignored in due.items()
        ]

    def _send_report(self, user, apartment_id, period_start, ignored) -> Reply:
        """Sends one period's report to the account's owner, or says what it still lacks. Any
        failure is the report's alone: the turn goes on, and the period keeps the mark that a
        later chat load sends it again by, until a send succeeds."""
        apartment, prop = place(self, user, apartment_id)
        names = prop["name"], apartment["name"]
        start = parse_iso(period_start)
        try:
            periods = self.store.periods(user.account, apartment_id)
            [period] = [p for p in periods if p["start"] == period_start]
            gaps = report.missing(self, user, apartment_id, period)
            if gaps:
                logger.info(
                    "period report held apartment=%s period=%s missing=%s",
                    apartment_id,
                    period_start,
                    gaps,
                )
                worded = [texts.report_gap(gap, named) for gap, named in gaps]
                return Reply(texts.report_held(*names, start, worded))
            built = report.build(self, user, apartment_id, period, ignored)
            report.send(built, self.mailer, user.account)
        except Exception:
            logger.exception(
                "sending a period report failed apartment=%s period=%s",
                apartment_id,
                period_start,
            )
            self.store.update_period(
                user.account, apartment_id, period_start, report_failed=True, report_ignored=ignored
            )
            failed = texts.report_failed(*names, start, user.account)
            if user.role != OWNER:
                owner = User(email=user.account, account=user.account, role=OWNER)
                self._append(owner, "assistant", failed)
            return Reply(failed)
        if "report_failed" in period:
            self.store.update_period(
                user.account, apartment_id, period_start, remove=("report_failed", "report_ignored")
            )
        return Reply(texts.report_sent(*names, start, user.account))

    def _failed_reports(self, user) -> list[tuple[str, dict]]:
        """Every (apartment id, period) a failed send marked."""
        return [
            (apartment["id"], p)
            for apartment in self.store.apartments(user.account)
            for p in self.store.periods(user.account, apartment["id"])
            if "report_failed" in p
        ]

    def _resend_failed_reports(self, user) -> list[Reply]:
        """Sends again every report a failure marked on its period."""
        return [
            self._send_report(user, apartment_id, p["start"], p["report_ignored"])
            for apartment_id, p in self._failed_reports(user)
        ]

    def _resend_on_request(self, user) -> list[Reply]:
        """Answers a message asking to send the failed reports now. An owner's address that is
        not a verified SES identity yet is named and nothing is sent, since the SES sandbox
        would refuse every send again."""
        if not self._failed_reports(user):
            return [Reply(texts.no_failed_reports())]
        if not self.mailer.verified(user.account):
            return [Reply(texts.address_unverified(user.account))]
        return self._resend_failed_reports(user)

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

    def _photo_again(self, user, message, reopen=None) -> list[Reply]:
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
            reopen,
        )

    def _reopen(self, user, at, operations) -> list[Reply]:
        """Reads the dropped photo of the message at at again with the operations' fields, once
        it may be reopened and they all fit; a value that does not fit is asked for."""
        state = account.load(self.store, user.account, user.role)
        messages = {m["at"]: m for m in self._reopenable(user, state)}
        if at not in messages:
            return [Reply(texts.photo_not_reopenable())]
        message = messages[at]
        try:
            reopen = meter_photo.reopening(
                operations, self.clock(), account.photo_kind(message["reading"])
            )
        except UnknownField:
            return [Reply(texts.photo_help())]
        except InvalidValue as error:
            return question.start(self, user, operations, error)
        return self._photo_again(user, message, reopen)

    def _choose(self, user, action: Action) -> list[Reply]:
        item, label = self._claim(user, action.choice)
        answered = {"pending": action.choice.pending, "version": action.choice.version}
        self._append(user, "user", label, choice=answered)
        return self._answer(user, item, action)

    def _answer(self, user, item, action: Action) -> list[Reply]:
        """Gives a waiting card its answer unless the records it copies changed since it was
        shown: a card whose text or buttons differ now is shown again and nothing else is done,
        since the answer was to the old one."""
        renewed = self._renewal(user, item, [Reply(texts.card_changed())])
        if renewed is not None:
            return renewed
        return self.flow(item).answer(self, user, item, action)

    def _renewal(self, user, item, lead: list[Reply], claim=False) -> list[Reply] | None:
        """Rebuilds the item's card from the stored records: None when it shows what it showed;
        else why it was dropped, when its records are gone, or lead then the card shown anew.
        Its copies are stored only when that shows it. With claim, an item no answer has claimed
        is claimed before it is written, and [] says another request claimed it first."""
        shown = _face(self.flow(item).card(item))
        gone = self._refresh(user, item, claim)
        if gone is not None:
            return gone
        flow = self.flow(item)
        if _face(flow.card(item)) == shown:
            return None
        if claim and not self._claimed(user, item):
            return []
        return [*lead, *ask(self, user, item, item["step"], flow.card)]

    def _claimed(self, user, item) -> bool:
        """Advances the item's version as an answer's claim does, so a write that follows cannot
        bring back an item a concurrent answer took and finished; False when it did."""
        if not self.store.claim_pending(user.email, item["seq"], item["version"], item["status"]):
            return False
        item["version"] += 1
        return True

    def _refresh(self, user, item, claim=False) -> list[Reply] | None:
        """Refreshes the item's copies; when its records are gone, drops it and returns why, or
        [] when claim is set and another request claimed it first. A refresh that moves the item
        to another step forgets the field its card asked for, which the new step may not have."""
        step = item["step"]
        try:
            self.flow(item).refresh(self, user, item)
        except CardGone as gone:
            if claim and not self._claimed(user, item):
                return []
            return drop(self, user, item, [Reply(gone.text)])
        if item["step"] != step:
            item.pop(ASKED, None)
        return None

    def _reopenable(self, user, state) -> list[dict]:
        """The user's photo messages a typed correction may read again: dropped, stored under this
        account, since an address keeps its messages when it moves to another, and held by no
        saved reading, tariff or waiting item, so a photo is never counted twice."""
        held = {
            key
            for serial in state.readings
            for r in state.readings[serial]
            for key in [r["image_key"], *r.get("failed_photos", [])]
        }
        held.update(t["source_image_key"] for t in state.tariffs)
        held.update(key for i in self.store.pending(user.email) for key in _held_photos(i))
        return [
            m
            for m in self.store.photo_messages(user.email)
            if m["reading"]["state"] == "dropped"
            and m["image_key"].startswith(f"{user.account}/")
            and m["image_key"] not in held
        ]

    def _listed(self, user, state) -> Listed:
        cards = [*self._awaiting(user), *self._with_status(user, STANDBY)]
        photos = sorted(self._reopenable(user, state), key=lambda m: m["at"])
        recent = photos[-self.config["correction_dropped_photos"] :]
        return Listed({i["seq"]: i for i in cards}, {m["at"]: m for m in recent})

    def records(self, user) -> tuple[str, Listed, Callable[[RecordsQuery], str]]:
        """What the classifier is given with the user's typed text: the block of waiting cards
        and dropped photos, the cards and photos it lists, and the lookup of saved records over
        the account loaded for the turn."""
        state = account.load(self.store, user.account, user.role)
        listed = self._listed(user, state)
        block = account.listed_context(
            {seq: self.flow(item).form(item) for seq, item in listed.cards.items()},
            {at: message["reading"] for at, message in listed.photos.items()},
        )
        return block, listed, self.finder(state)

    def finder(self, state) -> Callable[[RecordsQuery], str]:
        """The find_records lookup over the account the turn loaded."""
        limit = self.config["correction_recent_readings"]
        return lambda query: account.lookup(state, query, limit)

    def _text(self, user, action: Action) -> list[Reply]:
        records, listed, find = self.records(user)
        awaiting = self._awaiting(user)
        if awaiting:
            item = awaiting[0]
            return self._dispatch(
                user,
                self._classify(action, records, find, item),
                lambda intent: self._answer(user, item, replace(action, intent=intent)),
                listed,
                item,
            )
        standby = self._with_status(user, STANDBY)
        if not standby:
            return self._dispatch(
                user,
                self._classify(action, records, find),
                lambda intent: self._request(user, intent),
                listed,
            )
        item = standby[0]
        flow = self.flow(item)

        def answer(intent):
            """A standby card's text has nothing copied, so only a gone item stops the answer."""
            if intent.intent not in flow.STANDBY_ANSWERS:
                return self._request(user, intent)
            gone = self._refresh(user, item)
            return flow.answer_standby(self, user, item, intent) if gone is None else gone

        return self._dispatch(
            user, self._classify(action, records, find, item), answer, listed, item
        )

    def _classify(self, action: Action, records: str, find, item=None):
        """What the model makes of typed text, given the waiting cards and photos, the lookup of
        saved records, and item's card when there is one."""
        card_context = None
        if item is not None:
            flow = self.flow(item)
            card_context = classify_context(flow.card(item), flow.form(item), asked_field(item))
        context = message_context(records, card_context)
        return self.model.classify(action.text, context, self.clock(), find)

    def _dispatch(self, user, intent, answer, listed: Listed, item=None) -> list[Reply]:
        """Sends an edit's operations to what each names, and the rest of the intent to answer,
        which takes the part on item, the card the text was classified with. A card id that
        listed lacks is left to the correction, which answers it as any unknown id, and a photo
        id to the reopen, which refuses one it may not read again. When only the correction
        answers and shows no card of its own, item's card shows again, as a card edit that
        changes nothing does. The intents answered alike everywhere, those of ask_back and a
        request to send the failed reports again, skip answer; item's card shows again after
        their reply."""
        if intent.intent in (*ASK_BACK_INTENTS, "resend_reports"):
            if intent.intent == "resend_reports":
                replies = self._resend_on_request(user)
            else:
                replies = ask_back(self, user, intent)
            shown = [] if item is None else [card_reply(self.flow(item).card(item))]
            return [*replies, *shown]
        if intent.intent != "edit":
            return answer(intent)
        on_item, on_records = [], []
        # Key: seq of a card behind item; value: its operations, aimed at the card
        behind = {}
        # Key: at of a dropped photo; value: its operations
        photos = {}
        for operation in intent.operations:
            record, _, rest = operation.target.partition("|")
            if record == PHOTO_TARGET:
                photos.setdefault(rest, []).append(operation)
                continue
            if operation.target == CARD_TARGET:
                shown = item
            elif record == CARD_TARGET and rest in listed.cards:
                shown = listed.cards[rest]
                operation = operation.model_copy(update={"target": CARD_TARGET})
            else:
                on_records.append(operation)
                continue
            form = {} if shown is None else self.flow(shown).form(shown)
            operation = record_operation(operation, form)
            if not on_card(operation):
                on_records.append(operation)
            elif shown is None or shown["seq"] == item["seq"]:
                on_item.append(operation)
            else:
                behind.setdefault(shown["seq"], []).append(operation)
        replies = []
        if on_item or not (behind or photos or on_records):
            replies += answer(intent.model_copy(update={"operations": on_item}))
        for seq, operations in behind.items():
            edited = intent.model_copy(update={"operations": operations})
            replies += self._answer(user, listed.cards[seq], Action(intent=edited))
        for at, operations in photos.items():
            replies += self._reopen(user, at, operations)
        if on_records:
            replies += correction.start(self, user, on_records)
            shown_none = all(reply.card is None for reply in replies)
            if item is not None and not on_item and shown_none:
                replies.append(card_reply(self.flow(item).card(item)))
        return replies

    def _claim(self, user, choice: Choice) -> tuple[dict, str]:
        """The awaiting item a choice answers, with the chosen option's label. The item is
        claimed by advancing its version, so of two requests carrying one card exactly one
        proceeds. Raises AlreadyHandled when the item no longer shows that card or the card has
        no such option."""
        item = {i["seq"]: i for i in self.store.pending(user.email)}.get(choice.pending)
        if item is None or item["status"] != AWAITING or item["version"] != choice.version:
            raise AlreadyHandled(f"pending item {choice.pending} no longer shows that card")
        options = self.flow(item).card(item)["options"]
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
        if intent.intent in ("add_delegate", "list_delegates", "remove_delegate"):
            return delegates.request(self, user, intent)
        return ask_back(self, user, intent)

    def route(self, user, operations) -> list[Reply]:
        """Sends operations that each name their own target, as an answered question's do, to
        what each names, as an edit typed with no card on screen would go."""
        intent = IntentResult(intent="edit", operations=operations)
        state = account.load(self.store, user.account, user.role)
        listed = self._listed(user, state)
        return self._dispatch(user, intent, lambda i: self._request(user, i), listed)

    def _awaiting(self, user) -> list[dict]:
        return _questions_and_corrections_first(self._with_status(user, AWAITING))

    def _with_status(self, user, status) -> list[dict]:
        return [i for i in self.store.pending(user.email) if i["status"] == status]

    def snapshot(self, user) -> Snapshot:
        pending = self.store.pending(user.email)
        for item in pending:
            if item["status"] == READING and meter_photo.stalled(self, item):
                meter_photo.ask_retake(self, user, item)
        queue = [i for i in pending if i["status"] in (READING, AWAITING, STANDBY)]
        missing = any(i["kind"] == tariffs.KIND and i["step"] == tariffs.ASK for i in queue)
        shown = _questions_and_corrections_first([i for i in queue if i["status"] == AWAITING]) or [
            i for i in queue if i["status"] == STANDBY
        ]
        if not shown:
            return Snapshot(None, len(queue), missing)
        first = shown[0]
        return Snapshot(self.flow(first).card(first), len(queue) - 1, missing)

    def load(self, user, day=None) -> tuple[list[dict], Snapshot]:
        """The messages of day, today when None, with the snapshot, its card rebuilt first. An
        address with no message at all gets the welcome, and the first load of today the greeting
        when a close is due."""
        today = self.clock().isoformat()
        day = today if day is None else day
        self._renew_shown(user)
        messages = self.store.messages(user.email, day)
        if not self.store.has_messages(user.email):
            messages.append(self.greet(user))
        elif day == today and not messages:
            due = close_period.due_for_close(self, user)
            if due:
                messages.append(self._append(user, "assistant", texts.greeting(due)))
            for reply in self._resend_failed_reports(user):
                messages.append(self._append(user, "assistant", reply.text))
        return messages, self.snapshot(user)

    def earlier_failed(self, user, day=None) -> list[dict]:
        """The user's photos from days before today still marked failed, oldest first, when day
        is today or None; empty for an earlier day. Today's view lists them, so a photo that
        failed on an earlier day stays reachable after its day has passed."""
        today = self.clock().isoformat()
        if day not in (None, today):
            return []
        return [
            m
            for m in self.store.failed_messages(user.email)
            if m["image_key"] is not None and m["at"] < today
        ]

    def greet(self, user) -> dict:
        """Writes the welcome for the account user works in, as that user's role there."""
        return self._append(user, "assistant", texts.welcome(user.role, user.account))

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
