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
- record_lines / listed_context: the lines the classifier reads, one per record, and one per
  waiting card and per dropped photo, so an operation can name each by its id
- lookup: the record lines a find_records call asks for, by apartment, meter type and date
"""

import copy
import re
from dataclasses import dataclass, field

from common import prompts, texts
from common.access import OWNER
from common.dates import next_day
from common.fields import DISPLAY_POSITIONS, PHOTO_KINDS
from common.model import RecordsQuery
from common.periods import PRICES
from common.store import reading_key, row_key

_UNQUOTED = str.maketrans("", "", texts.QUOTE_MARKS)
# What splits a name into words besides spaces: slash, hyphen, the Hebrew hyphen (maqaf) and
# punctuation.
_WORD_BREAKS = re.compile(r"[\s/\-\u05be,.;:()]+")

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


def _reading_lines(state, meter, limit, latest, on_date=None) -> list[str]:
    """The meter's latest limit readings, with the account's latest saved reading wherever it
    sorts, since a back-dated reading can be the one just saved, and every reading of on_date."""
    readings = state.readings[meter["serial"]]
    lines = []
    for index, r in enumerate(readings):
        is_latest = r is latest
        if index < len(readings) - limit and not is_latest and r["date"] != on_date:
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


def _latest_reading(state: Account):
    """The reading saved last across meters: a seq is a time stamp, so the highest one."""
    readings = [r for rows in state.readings.values() for r in rows]
    return max(readings, key=lambda r: r["seq"], default=None)


def _property_line(p) -> str:
    return _line("property", p["id"], {"name": p["name"]})


def _apartment_line(a, properties) -> str:
    fields = {
        "name": a["name"],
        "property": properties[a["property_id"]]["name"],
        "months": a["period_months"],
    }
    return _line("apartment", a["id"], fields)


def _meter_line(m, apartments) -> str:
    fields = {
        "type": m["type"],
        "apartment": apartments[m["apartment_id"]]["name"],
        "display": display_position(m),
        "retired_at": m["retired_at"],
    }
    return _line("meter", m["serial"], fields)


def _period_lines(state: Account, apartment_id, apartments) -> list[str]:
    lines = []
    for p in state.periods.get(apartment_id, []):
        fields = {
            "apartment": apartments[apartment_id]["name"],
            "months": p["months"],
            "closed_on": p["closed_on"],
        }
        lines.append(_line("period", period_id(apartment_id, p["start"]), fields))
    return lines


def _tariff_lines(state: Account) -> list[str]:
    return [
        _line(
            "tariff",
            t["effective_date"],
            {"electricity_price": t[PRICES["electricity"]], "water_price": t[PRICES["water"]]},
        )
        for t in state.tariffs
    ]


def _delegate_lines(state: Account) -> list[str]:
    return [_line("delegate", d["email"], {"address": d["email"]}) for d in state.delegates]


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


def record_lines(state: Account, limit: int) -> list[str]:
    """Every saved record of the account as the classifier reads it, each by its id and its
    fields' names; a meter lists only its latest limit readings."""
    properties = records(state, "property")
    apartments = records(state, "apartment")
    latest = _latest_reading(state)
    lines = [_property_line(p) for p in state.properties]
    lines.extend(_apartment_line(a, properties) for a in state.apartments)
    for m in state.meters:
        lines.append(_meter_line(m, apartments))
        lines.extend(_reading_lines(state, m, limit, latest))
    for apartment_id in state.periods:
        lines.extend(_period_lines(state, apartment_id, apartments))
    return [*lines, *_tariff_lines(state), *_delegate_lines(state)]


def listed_context(cards: dict, photos: dict) -> str:
    """The waiting cards, each one's form by its seq, and the dropped photos, each one's reading
    tag by its message's at, under the records title; empty when there is neither."""
    lines = [_line("card", seq, form) for seq, form in cards.items()]
    lines.extend(_line("photo", at, _photo_fields(tag)) for at, tag in photos.items())
    return "\n".join([f"{texts.RECORDS_TITLE}:", *lines]) if lines else ""


def _words(text: str) -> set[str]:
    """The words of a name as a lookup compares them: quotes dropped, case folded, and a slash,
    hyphen or punctuation mark splitting words as a space does, so "Flat 3/1" and the
    "property / apartment" form a no-match answer lists both match."""
    return {w for w in _WORD_BREAKS.split(text.translate(_UNQUOTED).casefold()) if w}


def lookup(state: Account, query: RecordsQuery, limit: int) -> str:
    """The records a find_records call asks for. A named apartment, matched when every word of
    the query is a word of its property and apartment names, brings its property, its meters of
    the named type with their latest limit readings and those of the named date, and its periods;
    a property matched by its own name comes with no apartment. No name, or a blank one, brings
    every property, apartment and meter without readings. Every answer carries the reading saved
    last, which an undo names, and tariff and delegate lines join when asked for. A name nothing
    matches answers with every apartment's name."""
    properties = records(state, "property")
    apartments = records(state, "apartment")
    wanted = set() if query.apartment is None else _words(query.apartment)

    def place(a) -> str:
        return f"{properties[a['property_id']]['name']} / {a['name']}"

    if wanted:
        chosen = [a for a in state.apartments if wanted <= _words(place(a))]
        named = [p for p in state.properties if wanted <= _words(p["name"])]
        if not chosen and not named:
            names = ", ".join(place(a) for a in state.apartments)
            lines = [prompts.NO_APARTMENT_MATCH.format(query=query.apartment, names=names)]
            return "\n".join(lines + _extra_lines(state, query, lines))
        shown = {a["property_id"] for a in chosen} | {p["id"] for p in named}
    else:
        chosen, shown = state.apartments, {p["id"] for p in state.properties}
    chosen_ids = {a["id"] for a in chosen}
    lines = [_property_line(p) for p in state.properties if p["id"] in shown]
    lines.extend(_apartment_line(a, properties) for a in chosen)
    latest = _latest_reading(state)
    for m in state.meters:
        if m["apartment_id"] not in chosen_ids:
            continue
        if query.meter_type is not None and m["type"] != query.meter_type:
            continue
        lines.append(_meter_line(m, apartments))
        if wanted:
            lines.extend(_reading_lines(state, m, limit, latest, query.reading_date))
    if wanted:
        for a in chosen:
            lines.extend(_period_lines(state, a["id"], apartments))
    return "\n".join(lines + _extra_lines(state, query, lines))


def _extra_lines(state: Account, query: RecordsQuery, shown: list[str]) -> list[str]:
    """The asked-for tariff and delegate lines, after the reading saved last when shown lacks it."""
    latest = _latest_reading(state)
    lines = []
    if latest is not None:
        serial = next(s for s, rows in state.readings.items() if any(r is latest for r in rows))
        line = _latest_line(state, serial, latest)
        if line not in shown:
            lines.append(line)
    if query.tariffs:
        lines.extend(_tariff_lines(state))
    if query.delegates:
        lines.extend(_delegate_lines(state))
    return lines


def _latest_line(state: Account, serial, latest) -> str:
    meter = next(m for m in state.meters if m["serial"] == serial)
    # A limit of 0 keeps no reading but the one marked latest.
    [line] = _reading_lines(state, meter, 0, latest)
    return line
