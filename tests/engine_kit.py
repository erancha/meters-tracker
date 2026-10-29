"""Shared scaffolding for the engine tests: the example meters, a fake model whose photos are
registered on the fly, an Engine over the moto store with a settable clock, and seeding helpers
for properties, apartments, periods, meters and readings."""

import hashlib
import json
import os
from datetime import date, timedelta

from fake_model import FakeModel

from common.access import User
from common.engine import Action, Choice, Engine
from common.files import Files
from common.model import Candidate, MeterReadingResult
from common.store import Store

ACCOUNT = "owner@x.com"
USER = User(email=ACCOUNT, account=ACCOUNT, role="owner")


def candidate(value, position, unit, unreadable=False):
    return Candidate(
        value=value, position=position, unit=unit, unreadable_digits=unreadable, note=""
    )


def electricity(top=392691, bottom=392693, serial="E-392"):
    return MeterReadingResult(
        meter_type="electricity",
        serial=serial,
        serial_unreadable=False,
        candidates=[candidate(top, "top", "kWh"), candidate(bottom, "bottom", "kWh")],
        handwritten_label=None,
    )


def water(value=1250, serial="W-1250", unreadable=False):
    return MeterReadingResult(
        meter_type="water",
        serial=serial,
        serial_unreadable=False,
        candidates=[candidate(value, "single", "m3", unreadable)],
        handwritten_label=None,
    )


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
    def __init__(self, intents=(), today=date(2026, 10, 29)):
        self.today = today
        self.store = Store.from_env()
        self._photos = {}
        self.model = FakeModel(self._photos, list(intents))
        with open("config/app.json") as f:
            config = json.load(f)
        self.engine = Engine(
            self.store,
            self.model,
            Files("files"),
            config,
            timedelta(seconds=int(os.environ["LAMBDA_TIMEOUT_SECONDS"])),
            os.environ["LLM_MODEL"],
            clock=lambda: self.today,
        )

    def send_photo(self, result, note=None):
        image = f"photo-{len(self._photos)}".encode()
        self._photos[hashlib.sha256(image).hexdigest()] = result
        return self.engine.turn(USER, Action(image=image, media_type="image/jpeg", note=note))

    def choose(self, option):
        return self.choose_on(self.card(), option)

    def choose_on(self, card, option):
        """Answers the given card, which may be one the queue has since moved past."""
        return self.engine.turn(USER, choice(card, option))

    def say(self, text):
        return self.engine.turn(USER, Action(text=text))

    def card(self):
        return self.engine.snapshot(USER).card

    def photo_keys(self):
        """Keys of the user's photo messages, in order sent."""
        messages, _ = self.engine.load(USER)
        return [m["image_key"] for m in messages if m["role"] == "user" and m["image_key"]]

    def apartment(
        self, name="עורף", property_name="הגפן 7", start="2026-09-01", months=2, closed_on=None
    ):
        prop = self.store.put_property(ACCOUNT, property_name)
        apartment = self.store.put_apartment(ACCOUNT, prop["id"], name, start, months)
        self.period(apartment, start, months, closed_on)
        return apartment

    def period(self, apartment, start, months=2, closed_on=None):
        self.store.put_period(
            ACCOUNT,
            apartment["id"],
            {"start": start, "months": months, "closed_on": closed_on, "last_reminded_on": None},
        )

    def meter(self, apartment, serial, meter_type, readings, display_choice=None, retired_at=None):
        self.store.put_meter(
            ACCOUNT,
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
            self.store.put_reading(ACCOUNT, serial, r)

    def readings(self, serial):
        return self.store.readings(ACCOUNT, serial)

    def periods(self, apartment):
        return self.store.periods(ACCOUNT, apartment["id"])


def choice(card, option) -> Action:
    return Action(choice=Choice(card["pending"], card["version"], option))


def options(card):
    return [o["id"] for o in card["options"]]
