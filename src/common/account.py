"""An account's saved records held in memory, and the writes that turn one copy into another.

A typed correction is applied to a copy of the loaded account; comparing the two copies yields
the exact rows to write, which the store applies in one transaction.

Core pieces:
- Account: properties, apartments, meters, tariffs and delegates as lists, readings by meter
  serial and periods by apartment id; copy() is a deep copy
- load: reads every record of an account from the store, each meter with its former_serials
  and replaces, the serial of the meter its link retired; the delegates only for an owner
- Writes: per table, the rows to put and the rows to delete, each as a (scope, record) pair,
  where scope is the apartment id for a period, the meter serial for a reading, the user's
  address for a pending item, None elsewhere;
  the store derives the pk and sk from the pair. Keyed by table name, so a further table joins
  without a change of shape. size() counts the rows.
- diff: the Writes from one copy to another; a record whose key changed is deleted under the old
  key and put under the new one
- RECORD_KINDS / records / reading_id / period_id: an account's records of one kind by their id,
  the part of an operation's target after the kind's name
- apartment_readings / move_period / realign: an apartment's readings and the rules that keep
  periods and their reading flags in step on a copy
- display_position: a meter's stored display choice as the field's value
- context: the records block the classifier is given, one line per record, then per waiting card
  and per dropped photo, so an operation can name each by its id
"""

import copy
from dataclasses import dataclass, field

from common import texts
from common.access import OWNER
from common.dates import next_day
from common.fields import DISPLAY_POSITIONS, PHOTO_KINDS
from common.periods import PRICES
from common.store import reading_key, row_key

# Tables that hold one list of records for the whole account.
_FLAT_TABLES = ("properties", "apartments", "meters", "tariffs")


@dataclass
class Account:
    properties: list
    apartments: list
    meters: list
    readings: dict  # Key: meter serial; value: that meter's readings, ordered by date and seq
    periods: dict  # Key: apartment id; value: that apartment's periods, ordered by start
    tariffs: list
    # The account's delegate rows of the access table, ordered by address; held only for an owner
    delegates: list

    def copy(self) -> "Account":
        return copy.deepcopy(self)


@dataclass
class Writes:
    # Key: table name; value: (scope, record) pairs
    puts: dict = field(default_factory=dict)
    deletes: dict = field(default_factory=dict)

    def size(self) -> int:
        """The number of rows written."""
        return sum(len(rows) for writes in (self.puts, self.deletes) for rows in writes.values())


def load(store, account_id, role) -> Account:
    """Every record of the account; the delegate rows only for an owner, since a delegate has no
    need of the other addresses and reading them is a scan of the access table."""
    apartments = store.apartments(account_id)
    # A row gains former_serials with its first renamed serial, and a meter linked before the
    # link flow recorded replacements has no replaces, so a stored row may lack these two.
    meters = [{"former_serials": [], "replaces": None, **m} for m in store.meters(account_id)]
    return Account(
        properties=store.properties(account_id),
        apartments=apartments,
        meters=meters,
        readings={m["serial"]: store.readings(account_id, m["serial"]) for m in meters},
        periods={a["id"]: store.periods(account_id, a["id"]) for a in apartments},
        tariffs=store.tariffs(account_id),
        delegates=store.delegates(account_id) if role == OWNER else [],
    )


def _rows(account: Account) -> dict:
    """Every record by table, as (scope, record) pairs."""
    rows = {table: [(None, record) for record in getattr(account, table)] for table in _FLAT_TABLES}
    rows["access"] = [(None, delegate) for delegate in account.delegates]
    rows["periods"] = [(a, p) for a, periods in account.periods.items() for p in periods]
    rows["readings"] = [(s, r) for s, readings in account.readings.items() for r in readings]
    return rows


def _by_key(table, rows) -> dict:
    keyed = {}
    for scope, record in rows:
        key = row_key(table, "", scope, record)
        if key in keyed:
            raise ValueError(f"{table} holds two records with the key {key}")
        keyed[key] = (scope, record)
    return keyed


def diff(before: Account, after: Account) -> Writes:
    """The rows to put and delete so the stored account matches after instead of before. Raises
    ValueError when a table holds two records with one key."""
    writes = Writes()
    before_rows, after_rows = _rows(before), _rows(after)
    for table in before_rows:
        old = _by_key(table, before_rows[table])
        new = _by_key(table, after_rows[table])
        puts = [row for key, row in new.items() if old.get(key) != row]
        deletes = [row for key, row in old.items() if key not in new]
        if puts:
            writes.puts[table] = puts
        if deletes:
            writes.deletes[table] = deletes
    return writes


def reading_id(serial, reading) -> str:
    return f"{serial}|{reading_key(reading['date'], reading['seq'])}"


def period_id(apartment_id, start) -> str:
    return f"{apartment_id}|{start}"


# Per record kind, the (id, record) pairs of an account.
_RECORD_PAIRS = {
    "property": lambda state: [(p["id"], p) for p in state.properties],
    "apartment": lambda state: [(a["id"], a) for a in state.apartments],
    "meter": lambda state: [(m["serial"], m) for m in state.meters],
    "reading": lambda state: [
        (reading_id(serial, r), r) for serial, readings in state.readings.items() for r in readings
    ],
    "period": lambda state: [
        (period_id(apartment_id, p["start"]), p)
        for apartment_id, periods in state.periods.items()
        for p in periods
    ],
    "tariff": lambda state: [(t["effective_date"], t) for t in state.tariffs],
    "delegate": lambda state: [(d["email"], d) for d in state.delegates],
}
RECORD_KINDS = frozenset(_RECORD_PAIRS)


def records(state: Account, kind: str) -> dict:
    """The account's records of a kind in RECORD_KINDS, by id."""
    return dict(_RECORD_PAIRS[kind](state))


def apartment_readings(state: Account, apartment_id) -> list:
    """Over every meter the apartment ever had, retired ones included."""
    return [
        r
        for meter in state.meters
        if meter["apartment_id"] == apartment_id
        for r in state.readings[meter["serial"]]
    ]


def move_period(state: Account, apartment_id, period, start):
    """Moves a period of the apartment to a new start, with the reading flags that name it."""
    for r in apartment_readings(state, apartment_id):
        for flag in ("opens_period", "closes_period"):
            if r[flag] == period["start"]:
                r[flag] = start
    period["start"] = start
    state.periods[apartment_id].sort(key=lambda p: p["start"])


def realign(state: Account, apartment_id, closed_start, next_start):
    """After a closing reading moved: closed_on becomes the latest closing reading's date again,
    and the next period, when there is one, moves to start the day after it."""
    periods = {p["start"]: p for p in state.periods[apartment_id]}
    closed_on = max(
        r["date"]
        for r in apartment_readings(state, apartment_id)
        if r["closes_period"] == closed_start
    )
    periods[closed_start]["closed_on"] = closed_on
    if next_start is not None and next_day(closed_on) != next_start:
        move_period(state, apartment_id, periods[next_start], next_day(closed_on))


def display_position(meter) -> str | None:
    """Which number of the meter's display is its reading; None until a reading has chosen one."""
    choice = meter["display_choice"]
    return None if choice is None else DISPLAY_POSITIONS[choice]


def _shown(value) -> str:
    """A value in the words the model writes it back in: a field's flag is true or false."""
    if value is None:
        return texts.EMPTY_VALUE
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def _line(kind, record_id, fields: dict) -> str:
    shown = "; ".join(f"{name}={_shown(value)}" for name, value in fields.items())
    return f"{kind}|{record_id}: {shown}"


def _reading_lines(state, meter, limit, latest) -> list[str]:
    """The meter's latest limit readings, with the account's latest saved reading wherever it
    sorts, since a back-dated reading can be the one just saved."""
    readings = state.readings[meter["serial"]]
    lines = []
    for index, r in enumerate(readings):
        is_latest = r is latest
        if index < len(readings) - limit and not is_latest:
            continue
        fields = {
            "value": r["value"],
            "date": r["date"],
            "resets": r["resets"],
            "opens_period": r["opens_period"],
            "closes_period": r["closes_period"],
        }
        if is_latest:
            fields["latest"] = True
        lines.append(_line("reading", reading_id(meter["serial"], r), fields))
    return lines


def photo_kind(tag) -> str:
    """How a dropped photo was read, from its reading tag. The meter photo flow sets the meter
    type as soon as the model has read a meter, so a photo with none was read as a statement."""
    statement, meter = PHOTO_KINDS
    return statement if tag["meter_type"] is None else meter


def _photo_fields(tag) -> dict:
    """A dropped photo's reading tag as the classifier reads it."""
    return {
        "kind": photo_kind(tag),
        **{key: tag[key] for key in ("meter_type", "apartment", "property")},
    }


def context(state: Account, limit: int, cards: dict, photos: dict) -> str:
    """The account's records as the classifier reads them, each by its id and its fields' names,
    then cards, each waiting card's form by its seq, and photos, each dropped photo's reading tag
    by its message's at; empty when there is none of these. A meter lists only its latest limit
    readings, and a seq is a time stamp, so the highest one across meters marks the reading saved
    last."""
    properties = records(state, "property")
    apartments = records(state, "apartment")
    readings = [r for rows in state.readings.values() for r in rows]
    latest = max(readings, key=lambda r: r["seq"], default=None)
    lines = [_line("property", p["id"], {"name": p["name"]}) for p in state.properties]
    for a in state.apartments:
        fields = {
            "name": a["name"],
            "property": properties[a["property_id"]]["name"],
            "months": a["period_months"],
        }
        lines.append(_line("apartment", a["id"], fields))
    for m in state.meters:
        fields = {
            "type": m["type"],
            "apartment": apartments[m["apartment_id"]]["name"],
            "display": display_position(m),
            "retired_at": m["retired_at"],
        }
        lines.append(_line("meter", m["serial"], fields))
        lines.extend(_reading_lines(state, m, limit, latest))
    for apartment_id, periods in state.periods.items():
        for p in periods:
            fields = {
                "apartment": apartments[apartment_id]["name"],
                "months": p["months"],
                "closed_on": p["closed_on"],
            }
            lines.append(_line("period", period_id(apartment_id, p["start"]), fields))
    for t in state.tariffs:
        fields = {
            "electricity_price": t[PRICES["electricity"]],
            "water_price": t[PRICES["water"]],
        }
        lines.append(_line("tariff", t["effective_date"], fields))
    lines.extend(_line("delegate", d["email"], {"address": d["email"]}) for d in state.delegates)
    lines.extend(_line("card", seq, form) for seq, form in cards.items())
    lines.extend(_line("photo", at, _photo_fields(tag)) for at, tag in photos.items())
    return "\n".join([f"{texts.RECORDS_TITLE}:", *lines]) if lines else ""
