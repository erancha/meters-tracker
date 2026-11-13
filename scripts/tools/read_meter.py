"""Reads one meter photo through the real model and prints the MeterReadingResult as JSON. The
logic behind scripts/tools/read-meter.sh, which runs it inside .venv; the model comes from LLM_MODEL
and the provider's key from LLM_API_KEY."""

import json
import mimetypes
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from common.files import EXTENSIONS
from common.model import call_cost, model_from_env


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    sys.exit(2)


if len(sys.argv) != 2:
    fail("usage: read-meter.sh <image>")
path = Path(sys.argv[1])
if not path.is_file():
    fail(f"{path} is not a file; pass the path of one meter photo")
media_type = mimetypes.guess_type(path.name)[0]
if media_type not in EXTENSIONS:
    fail(f"{path.name} is not a JPEG or PNG; the model reads only those")
try:
    model = model_from_env()
except ValueError as error:
    fail(str(error))


def without_noise(value):
    """The result with every false or null field left out, so a flag shows only when it is
    raised and a label only when there is one."""
    if isinstance(value, dict):
        return {k: without_noise(v) for k, v in value.items() if v is not None and v is not False}
    if isinstance(value, list):
        return [without_noise(v) for v in value]
    return value


started = time.perf_counter()
result = model.read_meter(path.read_bytes(), media_type)
seconds = time.perf_counter() - started
print(json.dumps(without_noise(result.model_dump()), indent=2, ensure_ascii=False))
usage = model.last_usage
cost = call_cost(model.model_id, usage)
print(
    f"{model.model_id}: {seconds:.1f} s, {usage.input_tokens} tokens in, "
    f"{usage.output_tokens} out, estimated cost ${cost:.4f}",
    file=sys.stderr,
)
