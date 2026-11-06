"""Sums the api Lambda's model_call log lines into the spend by day and by kind of call. The logic
behind scripts/aws-helper.sh --costs, which pipes the output of aws logs tail --format short
into it.

Each input line is a timestamp, a space, and the Lambda's JSON log record; the record's message is
the JSON line common.model logs per call. The text filter that selects the lines can also match
another line that mentions model_call, so only messages that open with the model_call event
count. Days are UTC, as the record's timestamp is. A line that names no cached tokens, as a
client that asks for no caching logs, counts them as none.
"""

import json
import sys
from collections import defaultdict
from dataclasses import dataclass

EVENT_PREFIX = '{"event": "model_call"'


@dataclass
class Total:
    """Calls, tokens and dollars summed over one group of model calls."""

    calls: int = 0
    input_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0

    def add(self, call: dict) -> None:
        self.calls += 1
        self.input_tokens += call["input_tokens"]
        self.cache_read_tokens += call.get("cache_read_tokens", 0)
        self.cache_write_tokens += call.get("cache_write_tokens", 0)
        self.output_tokens += call["output_tokens"]
        self.cost += call["cost"]


def model_calls(lines):
    """The (UTC day, call) of every model_call line among the tailed lines."""
    for line in lines:
        _, record_json = line.split(" ", 1)
        record = json.loads(record_json)
        if record["message"].startswith(EVENT_PREFIX):
            yield record["timestamp"][:10], json.loads(record["message"])


def summarize(lines) -> str:
    by_day: dict[str, Total] = defaultdict(Total)
    by_kind: dict[str, Total] = defaultdict(Total)
    total = Total()
    for day, call in model_calls(lines):
        by_day[day].add(call)
        by_kind[call["kind"]].add(call)
        total.add(call)
    header = (
        f"{'':<16}{'calls':>8}{'tokens in':>12}{'cache read':>12}{'cache write':>12}"
        f"{'tokens out':>12}{'cost $':>10}"
    )

    def row(label: str, t: Total) -> str:
        return (
            f"{label:<16}{t.calls:>8}{t.input_tokens:>12}{t.cache_read_tokens:>12}"
            f"{t.cache_write_tokens:>12}{t.output_tokens:>12}{t.cost:>10.4f}"
        )

    out = ["by day (UTC)", header, *(row(d, by_day[d]) for d in sorted(by_day))]
    out += ["", "by kind", header, *(row(k, by_kind[k]) for k in sorted(by_kind))]
    out += ["", row("total", total)]
    return "\n".join(out)


if __name__ == "__main__":
    print(summarize(sys.stdin))
