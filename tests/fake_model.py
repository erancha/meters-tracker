"""In-memory stand-in for the Model interface: meter readings are looked up by the sha256 of the
image bytes, intents are returned in the order they were queued, and every classify call is
recorded as (text, context) in classified."""

import hashlib

from common.model import IntentResult, MeterReadingResult


class FakeModel:
    def __init__(self, readings: dict[str, MeterReadingResult], intents: list[IntentResult]):
        self._readings = readings
        self._intents = list(intents)
        self.classified: list[tuple[str, str]] = []

    def read_meter(self, image: bytes, media_type: str) -> MeterReadingResult:
        return self._readings[hashlib.sha256(image).hexdigest()]

    def classify(self, text: str, context: str) -> IntentResult:
        self.classified.append((text, context))
        return self._intents.pop(0)
