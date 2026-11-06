"""Shared scaffolding for the engine tests: the example meters and statement, a fake model whose
photos are registered on the fly, an Engine over the moto store with a settable clock, and
seeding helpers for properties, apartments, periods, meters, readings and tariffs."""

import hashlib
import json
import os
from datetime import date, timedelta

import boto3
from fake_model import FakeModel

from common.access import User
from common.engine import Action, Choice, Engine
from common.files import Files
from common.model import (
    Candidate,
    IntentResult,
    MeterReadingResult,
    Operation,
    RecordsQuery,
    StatementResult,
)
from common.notify import Mailer
from common.store import Store

ACCOUNT = "owner@x.com"
USER = User(email=ACCOUNT, account=ACCOUNT, role="owner")


def candidate(value, position, unit, unreadable=False, uncertain=False):
    """unreadable reads no value at all; uncertain reads the value with a doubtful digit."""
    return Candidate(
        value=None if unreadable else value,
        position=position,
        unit=unit,
        unreadable_digits=unreadable or uncertain,
        note="",
    )


# The number printed on an electricity meter where a serial would be, often its model number;
# it plays no part in which meter a photo is of.
MODEL_NUMBER = "99840522"


def electricity(top=392691, bottom=392693, label="E-392"):
    """label is the text handwritten beside the meter, which identifies an electricity meter;
    None is a photo on which the model found none."""
    return MeterReadingResult(
        meter_type="electricity",
        serial=MODEL_NUMBER,
        serial_unreadable=False,
        serial_uncertain=False,
        candidates=[candidate(top, "top", "kWh"), candidate(bottom, "bottom", "kWh")],
        handwritten_label=label,
    )


def water(
    value=1250,
    serial="W-1250",
    unreadable=False,
    label=None,
    serial_uncertain=False,
    uncertain=False,
):
    return MeterReadingResult(
        meter_type="water",
        serial=serial,
        serial_unreadable=False,
        serial_uncertain=serial_uncertain,
        candidates=[candidate(value, "single", "m3", unreadable, uncertain)],
        handwritten_label=label,
    )


UNKNOWN = MeterReadingResult(
    meter_type="unknown",
    serial=None,
    serial_unreadable=False,
    serial_uncertain=False,
    candidates=[],
    handwritten_label=None,
)


def statement(electricity=0.6432, water=15.62, includes_vat=True, is_statement=True):
    return StatementResult(
        is_statement=is_statement,
        electricity_per_kwh=electricity,
        water_per_m3=water,
        includes_vat=includes_vat,
    )


NOT_STATEMENT = statement(None, None, is_statement=False)


def reading(day, value, opens=None, closes=None):
    return {
        "date": day,
        "seq": f"{day}T08:00:00",
        "value": value,
        "image_key": "k",
        "recorded_by": ACCOUNT,
        "opens_period": opens,
        "closes_period": closes,
        "resets": False,
    }


class Kit:
    """Drives the engine as one user over the mocked store: seeds the user's account, sends photos,
    texts and card choices, and reads back the card and the records.

    user is the owner whose account the kit seeds and speaks for; a distinct user per case lets
    one mocked store hold many independent accounts. store and ses, when given, are reused rather
    than built, since building the AWS clients costs more than a turn."""

    def __init__(self, intents=(), today=date(2026, 10, 29), user=USER, store=None, ses=None):
        self.today = today
        self.user = user
        self.account = user.account
        self.store = Store.from_env() if store is None else store
        self._photos = {}
        self._statements = {}
        self.model = FakeModel(self._photos, list(intents), self._statements)
        with open("config/app.json") as f:
            config = json.load(f)
        self.engine = Engine(
            self.store,
            self.model,
            Files("files"),
            Mailer(boto3.client("ses") if ses is None else ses, os.environ["SES_SENDER"]),
            config,
            timedelta(seconds=int(os.environ["LAMBDA_TIMEOUT_SECONDS"])),
            os.environ["LLM_MODEL"],
            clock=lambda: self.today,
        )

    def register_photo(self, image: bytes, result, as_statement=NOT_STATEMENT):
        """Makes the model read image as result, and as as_statement when result is no meter."""
        digest = hashlib.sha256(image).hexdigest()
        self._photos[digest] = result
        self._statements[digest] = as_statement

    def send_photo(self, result, note=None, as_statement=NOT_STATEMENT):
        """Sends a photo the model reads as result, and as as_statement when result is no meter."""
        image = f"photo-{len(self._photos)}".encode()
        self.register_photo(image, result, as_statement)
        return self.engine.turn(self.user, Action(image=image, media_type="image/jpeg", note=note))

    def choose(self, option):
        return self.choose_on(self.card(), option)

    def choose_on(self, card, option):
        """Answers the given card, which may be one the queue has since moved past."""
        return self.engine.turn(self.user, choice(card, option))

    def say(self, text):
        return self.engine.turn(self.user, Action(text=text))

    def card(self):
        return self.engine.snapshot(self.user).card

    def form(self):
        """The fields of the card the snapshot shows, with their current values."""
        shown = self.card()
        item = {i["seq"]: i for i in self.store.pending(self.user.email)}[shown["pending"]]
        return self.engine.flow(item).form(item)

    def photo_keys(self):
        """Keys of the user's photo messages, in order sent."""
        messages, _ = self.engine.load(self.user)
        return [m["image_key"] for m in messages if m["role"] == "user" and m["image_key"]]

    def apartment(
        self, name="עורף", property_name="הגפן 7", start="2026-09-01", months=2, closed_on=None
    ):
        prop = self.store.put_property(self.account, property_name)
        apartment = self.store.put_apartment(self.account, prop["id"], name, start, months)
        self.period(apartment, start, months, closed_on)
        return apartment

    def period(self, apartment, start, months=2, closed_on=None):
        self.store.put_period(
            self.account,
            apartment["id"],
            {"start": start, "months": months, "closed_on": closed_on, "last_reminded_on": None},
        )

    def meter(self, apartment, serial, meter_type, readings, display_choice=None, retired_at=None):
        self.store.put_meter(
            self.account,
            {
                "serial": serial,
                "apartment_id": apartment["id"],
                "type": meter_type,
                "display_choice": display_choice,
                "label_hint": None,
                "retired_at": retired_at,
            },
        )
        for r in readings:
            self.store.put_reading(self.account, serial, r)

    def send_statement(self, result=None, note=None):
        return self.send_photo(UNKNOWN, note, statement() if result is None else result)

    def tariff(self, effective_date, electricity, water):
        self.store.put_tariff(
            self.account,
            {
                "effective_date": effective_date,
                "electricity_per_kwh": electricity,
                "water_per_m3": water,
                "source_image_key": None,
            },
        )

    def tariffs(self):
        return self.store.tariffs(self.account)

    def lookup(self, **fields) -> str:
        """What the last classification's find_records would answer for the fields given, the
        rest null or false."""
        empty = {"apartment": None, "meter_type": None, "reading_date": None}
        query = RecordsQuery(**{**empty, "tariffs": False, "delegates": False, **fields})
        return self.model.finds[-1](query)

    def readings(self, serial):
        return self.store.readings(self.account, serial)

    def periods(self, apartment):
        return self.store.periods(self.account, apartment["id"])


def edits(*changes, target="card") -> IntentResult:
    """The model's answer to a message changing each (field, value) pair of the target."""
    operations = [
        Operation(action="set", target=target, field=field, value=value) for field, value in changes
    ]
    return IntentResult(intent="edit", operations=operations)


def edit(field, value, target="card") -> IntentResult:
    return edits((field, value), target=target)


def choice(card, option) -> Action:
    return Action(choice=Choice(card["pending"], card["version"], option))


def options(card):
    return [o["id"] for o in card["options"]]
