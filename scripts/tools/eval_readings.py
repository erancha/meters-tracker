"""Runs every photo the deployed model failed on through the model named by LLM_MODEL and scores
it against the reading the user ended up saving. The logic behind scripts/tools/eval-readings.sh, which
passes the app stack's name; the tables and the files bucket are resolved from that stack.

- score: one result against the saved serial and value
- evaluate: yields one row per failed photo as soon as the model answers, with its wall-clock
  time, up to an optional limit
- photo_line / report: one photo's printed line; the totals and the count per failed model
- main: prints each photo's line as it arrives, so an error mid-run keeps the lines already
  paid for on screen, then the report"""

import argparse
import sys
import time
from collections import Counter
from collections.abc import Iterator
from itertools import islice
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
# app_stack.py stays in scripts/, beside deploy.sh, which uses it too.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app_stack

from common.files import Files
from common.flows import readable
from common.flows.meter_photo import meter_identity
from common.model import MeterReadingResult, ModelRefused, model_from_env
from common.store import Store


def score(expected_serial: str, expected_value: int, result: MeterReadingResult) -> dict:
    values = [c.value for c in result.candidates if readable(c.model_dump())]
    read = meter_identity(result)
    return {
        "serial_ok": not read["serial_unreadable"] and read["serial"] == expected_serial,
        "value_ok": expected_value in values,
        "unreadable": not values,
    }


# A refused photo counts as neither right nor unreadable; its refusal is a field of its own.
REFUSED_SCORE = {"serial_ok": False, "value_ok": False, "unreadable": False}


def _failed_photos(store) -> Iterator[tuple[dict, str]]:
    for reading in store.failed_photo_readings():
        for key in reading["failed_photos"]:
            yield reading, key


def evaluate(store, files, model, limit: int | None = None) -> Iterator[dict]:
    """Rows for the first limit failed photos, or for all of them when limit is None; each photo
    is read from the bucket and sent to the model only when its row is requested."""
    for reading, key in islice(_failed_photos(store), limit):
        image, media_type = files.get_image(key)
        started = time.perf_counter()
        try:
            result = model.read_meter(image, media_type)
        except ModelRefused:
            scored = {**REFUSED_SCORE, "refused": True}
        else:
            scored = {**score(reading["serial"], reading["value"], result), "refused": False}
        yield {
            "account": reading["account"],
            "serial": reading["serial"],
            "date": reading["date"],
            "key": key,
            "failed_model": reading["model"],
            **scored,
            "seconds": time.perf_counter() - started,
        }


def _mark(ok: bool) -> str:
    return "ok" if ok else "NO"


def photo_line(r: dict) -> str:
    if r["refused"]:
        outcome = "refused"
    else:
        outcome = f"serial {_mark(r['serial_ok'])} value {_mark(r['value_ok'])}"
        if r["unreadable"]:
            outcome += " unreadable"
    return f"{r['account']} {r['serial']} {r['date']} {r['key']}: {outcome} {r['seconds']:.1f}s"


COUNTED = {
    "serial ok": lambda r: r["serial_ok"],
    "value ok": lambda r: r["value_ok"],
    "both ok": lambda r: r["serial_ok"] and r["value_ok"],
    "unreadable": lambda r: r["unreadable"],
    "refused": lambda r: r["refused"],
}


def report(rows: list[dict]) -> str:
    total = len(rows)
    counts = (f"{name}: {sum(1 for r in rows if hit(r))}/{total}" for name, hit in COUNTED.items())
    lines = [", ".join([f"photos: {total}", *counts])]
    for failed_model, n in sorted(Counter(r["failed_model"] for r in rows).items()):
        lines.append(f"failed by {failed_model}: {n}")
    return "\n".join(lines)


def _stack_resources(stack: str) -> tuple[Store, Files]:
    """The Store and Files over the stack's tables and bucket."""
    physical = app_stack.physical_ids(stack)
    store = Store(app_stack.tables(physical), boto3.resource("dynamodb"))
    return store, Files(physical["FilesBucket"])


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    sys.exit(2)


def _positive(text: str) -> int:
    if not text.isdigit() or int(text) == 0:
        raise argparse.ArgumentTypeError(f"not a positive whole number: {text}")
    return int(text)


def main() -> None:
    parser = argparse.ArgumentParser(prog="eval-readings.sh", usage="%(prog)s [--limit N]")
    parser.add_argument("stack")
    parser.add_argument("--limit", type=_positive)
    args = parser.parse_args()
    try:
        model = model_from_env()
    except ValueError as error:
        fail(str(error))
    store, files = _stack_resources(args.stack)
    rows = []
    for row in evaluate(store, files, model, args.limit):
        print(photo_line(row), flush=True)
        rows.append(row)
    print(report(rows))


if __name__ == "__main__":
    main()
