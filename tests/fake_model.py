"""In-memory stand-in for the Model interface: meter readings and statements are looked up by
the sha256 of the image bytes, intents are returned in the order they were queued (at
construction or by queue), and every classify call is recorded as (text, context) in
classified."""

import hashlib
from datetime import date

from common.model import IntentResult, MeterReadingResult, StatementResult


class FakeModel:
    def __init__(
        self,
        readings: dict[str, MeterReadingResult],
        intents: list[IntentResult],
        statements: dict[str, StatementResult] | None = None,
    ):
        self._readings = readings
        self._statements = {} if statements is None else statements
        self._intents = list(intents)
        self.classified: list[tuple[str, str]] = []

    def queue(self, intent: IntentResult):
        self._intents.append(intent)

    def read_meter(self, image: bytes, media_type: str) -> MeterReadingResult:
        return self._readings[hashlib.sha256(image).hexdigest()]

    def read_statement(self, image: bytes, media_type: str) -> StatementResult:
        return self._statements[hashlib.sha256(image).hexdigest()]

    def classify(self, text: str, context: str, today: date) -> IntentResult:
        self.classified.append((text, context))
        return self._intents.pop(0)
