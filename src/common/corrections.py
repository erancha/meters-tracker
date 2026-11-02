"""Plans a correction of saved records with no I/O: the model's operations run on a copy of the
account, and the plan says what that copy changes before anything is written.

Core pieces:
- plan: takes the loaded account, the operations and the acting user's role, and returns a Plan:
  the changed copy, each field's old and new value, the figures that differ, and what is refused
  or warned about. An operation that cannot be applied raises UnknownTarget, UnknownField or
  InvalidValue. Deletes apply after plain sets, so a merge's moves empty what it deletes, and
  operations on a record's key apply last, so an id in one message names the record as the model
  read it
- REWRITES: per (record, field), what setting a field does when it reaches past its own attribute:
  a meter's serial, apartment or retirement, a reading's date or meter, a period's start, closing
  reading, close or vacancy, a tariff's date
- DELETES: per record kind, what deleting a record does; a period and a delegate have none
- check: compares the two copies for refusals (no confirmation possible) and warnings (card lines),
  so a rewrite only moves records and the rules that forbid a result live in one place. A plan with
  a refusal compares no figures, since its copy may hold what the figures cannot be read from
- label: how a card names the record an operation's target names
- address_refusal / access_refusals: the one rule that reads the store, what a delegate's new
  address may already hold, which the flow applies to a plan

The figures compared are every meter's in every period of its apartment, closed periods included,
so a change reaches the card wherever its effect lands, whatever the operation touched. A renamed
meter and a moved period are compared with their own earlier selves; a meter that changes apartment
leaves figures behind in one and gains them in the other, so both are listed.
"""

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from itertools import pairwise

from common import account, texts
from common.access import OWNER
from common.account import Account
from common.dates import next_day, parse_iso
from common.fields import (
    DISPLAY_POSITIONS,
    FIELDS,
    InvalidValue,
    Target,
    UnknownField,
    UnknownTarget,
    field_for,
    parse,
    parse_target,
    resolve,
)
from common.model import Operation
from common.periods import (
    PRICES,
    Figures,
    figures,
    flags_in_order,
    joining_opens,
    new_period,
    open_period,
    period_readings,
)
from common.store import MAX_TRANSACTION_ITEMS, reading_key


@dataclass(frozen=True)
class Change:
    """One field's change; old and new are worded as the card shows them."""

    record_label: str
    field_label: str
    old: str
    new: str

    @property
    def line(self) -> str:
        return texts.change_line(self.record_label, self.field_label, self.old, self.new)


@dataclass(frozen=True)
class Deletion:
    """One record a delete operation removes; line is the card's wording of it."""

    line: str


@dataclass(frozen=True)
class FigureChange:
    """One meter's figures in one period, before and after the change. before_type is the meter's
    type before it, which a corrected type makes differ from meter_type."""

    property: str
    apartment: str
    meter_type: str
    serial: str
    start: str
    before_type: str
    before: Figures | None
    after: Figures | None

    @property
    def line(self) -> str:
        return texts.figure_line(
            self.property,
            self.apartment,
            self.meter_type,
            self.serial,
            parse_iso(self.start),
            self.before_type,
            self.before,
            self.after,
        )


@dataclass(frozen=True)
class Plan:
    """What the operations would do; after is the changed copy, written only on confirmation."""

    after: Account
    changes: list[Change | Deletion]
    figures: list[FigureChange]
    refusals: list[str]
    warnings: list[str]


class _Refused(Exception):
    """A rewrite that cannot reach a state check could compare; its message is the refusal."""


# The stored attribute of each field a correction may set. A field missing here is one no
# correction reaches, even when the field table knows it. A reading's serial, a period's closing
# reading and its vacancy are none: the meter a reading is stored under, a flag on a reading, and
# what a close with no next period amounts to.
_ATTRIBUTES = {
    ("property", "name"): "name",
    ("apartment", "name"): "name",
    ("apartment", "months"): "period_months",
    ("apartment", "property"): "property_id",
    ("reading", "value"): "value",
    ("reading", "resets"): "resets",
    ("reading", "date"): "date",
    ("reading", "serial"): None,
    ("meter", "serial"): "serial",
    ("meter", "type"): "type",
    ("meter", "apartment"): "apartment_id",
    ("meter", "display"): "display_choice",
    ("meter", "retired_at"): "retired_at",
    ("period", "months"): "months",
    ("period", "start"): "start",
    ("period", "closing"): None,
    ("period", "closed_on"): "closed_on",
    ("period", "vacant"): None,
    ("tariff", "electricity_price"): PRICES["electricity"],
    ("tariff", "water_price"): PRICES["water"],
    ("tariff", "effective_date"): "effective_date",
    ("delegate", "address"): "email",
}

# The record kind whose id each referring field's value must be.
_REFERENCES = {
    ("apartment", "property"): "property",
    ("reading", "serial"): "meter",
    ("meter", "apartment"): "apartment",
    ("period", "closing"): "reading",
}

# The meter type with one counter, so a display position means nothing on it.
_SINGLE_COUNTER = "water"


def _record(state: Account, target: Target) -> dict:
    return account.records(state, target.record)[target.id]


def _apartment_name(state: Account, apartment_id) -> str:
    return account.records(state, "apartment")[apartment_id]["name"]


def _meter(state: Account, serial) -> dict:
    return account.records(state, "meter")[serial]


def _sort_readings(readings: list):
    readings.sort(key=lambda r: reading_key(r["date"], r["seq"]))


def _set_display(state: Account, target: Target, value: str):
    _record(state, target)["display_choice"] = DISPLAY_POSITIONS.index(value)


def _set_apartment_months(state: Account, target: Target, value: int):
    """A close gives the next period the open period's length, so the open period takes the new
    length as well, or the apartment's would reach only a vacant apartment's next period."""
    _record(state, target)["period_months"] = value
    current = open_period(state.periods[target.id])
    if current is not None:
        current["months"] = value


def _remember_serials(meter, serials: list[str]):
    """A meter's serial is never among its former ones, so renaming it back leaves no loop."""
    former = [*meter["former_serials"], *serials]
    meter["former_serials"] = [serial for serial in former if serial != meter["serial"]]


def _keep_first_flag(readings, flag):
    seen = set()
    for r in readings:
        if r[flag] in seen:
            r[flag] = None
        else:
            seen.add(r[flag])


def _keep_one_flag_per_period(readings):
    """A period closes on its latest closing reading. It opens on its earliest opening reading,
    unless a reading that closes the period before opens it: that row stands for the meter between
    the two, so an opening before it would count the same units in both."""
    _keep_first_flag(reversed(readings), "closes_period")
    closing_rows_first = sorted(
        readings, key=lambda r: r["closes_period"] in (None, r["opens_period"])
    )
    _keep_first_flag(closing_rows_first, "opens_period")


def _merge_meter(state: Account, gone, kept):
    """Moves gone's readings to kept and removes gone. The meter stays live if either was, and
    keeps gone's display choice and link when it has none."""
    state.meters.remove(gone)
    readings = state.readings[kept["serial"]]
    readings.extend(state.readings.pop(gone["serial"]))
    _sort_readings(readings)
    _keep_one_flag_per_period(readings)
    retired = (gone["retired_at"], kept["retired_at"])
    kept["retired_at"] = None if None in retired else max(retired)
    if kept["display_choice"] is None:
        kept["display_choice"] = gone["display_choice"]
    if kept["replaces"] is None:
        kept["replaces"] = gone["replaces"]
    _remember_serials(kept, [*gone["former_serials"], gone["serial"]])


def _set_serial(state: Account, target: Target, value: str):
    """A serial that names another meter merges into it, when the two share a type and apartment
    by now: plain sets run first, so that is where the plan leaves them."""
    meters = account.records(state, "meter")
    meter = meters[target.id]
    if value in meters:
        kept = meters[value]
        if (meter["type"], meter["apartment_id"]) != (kept["type"], kept["apartment_id"]):
            raise _Refused(texts.serial_taken(value))
        _merge_meter(state, meter, kept)
        return
    state.readings[value] = state.readings.pop(target.id)
    meter["serial"] = value
    _remember_serials(meter, [target.id])


def _set_type(state: Account, target: Target, value: str):
    meter = _record(state, target)
    meter["type"] = value
    if value == _SINGLE_COUNTER:
        meter["display_choice"] = None


def _covers(period, day: str) -> bool:
    return period["start"] <= day and (period["closed_on"] is None or day <= period["closed_on"])


def _copy_periods(state: Account, source, apartment_id):
    """Gives the apartment copies of the source's periods; none when one would overlap the
    apartment's own first period, which check refuses, since a skipped period would lose the
    meter's consumption there."""
    own = state.periods[apartment_id]
    periods = state.periods[source]
    if own and any(p["closed_on"] is None or p["closed_on"] >= own[0]["start"] for p in periods):
        return
    state.periods[apartment_id] = [*[dict(p) for p in periods], *own]


def _rebuild_flags(readings, periods):
    """Flags the readings from the periods' dates, as a close would have: a period's first reading
    opens it and a closed period's last reading closes it. A closing reading also opens a period
    that starts straight after, since no consumption falls between the two. A closed period that
    holds fewer than two readings gets no flags, since it would count to the meter's last reading
    as if open; its reading stands for the meter when the next period starts."""
    for r in readings:
        r.update(opens_period=None, closes_period=None)
    standing, ended = None, None
    for period in periods:
        counted = [r for r in readings if _covers(period, r["date"])]
        if standing is not None and period["start"] <= next_day(ended):
            counted = [standing, *[r for r in counted if r is not standing]]
        closed = period["closed_on"] is not None
        standing, ended = None, period["closed_on"]
        if closed and len(counted) < 2:
            standing = next(iter(counted), None)
        elif counted:
            counted[0]["opens_period"] = period["start"]
            if closed:
                standing = counted[-1]
                standing["closes_period"] = period["start"]


def _closing_rows(state: Account, apartment_id, start) -> list:
    return [
        r for r in account.apartment_readings(state, apartment_id) if r["closes_period"] == start
    ]


def _follow_closing_readings(state: Account, apartment_id, period):
    """Realigns the period when its closing readings moved its close. A date that stands moves
    nothing, and with none left closed_on stays, since nothing says when the period ended. Only
    the period straight after moves with it: a closing reading carried over a later period opens
    a period that has dates of its own."""
    closing = _closing_rows(state, apartment_id, period["start"])
    if closing and max(r["date"] for r in closing) != period["closed_on"]:
        periods = state.periods[apartment_id]
        following = periods[periods.index(period) + 1 : periods.index(period) + 2]
        opens = closing[0]["opens_period"]
        next_start = opens if [p["start"] for p in following] == [opens] else None
        account.realign(state, apartment_id, period["start"], next_start)


def _set_apartment(state: Account, target: Target, value: str):
    """Moves a meter to another apartment, whose periods then decide its reading flags, while the
    source's periods it closed and the target's periods it now closes follow their closing
    readings. Check refuses an apartment that has a live meter of the type."""
    meter = _record(state, target)
    source = meter["apartment_id"]
    meter["apartment_id"] = value
    readings = state.readings[meter["serial"]]
    closed_starts = {r["closes_period"] for r in readings}
    left = [p for p in state.periods[source] if p["start"] in closed_starts]
    if readings and not any(_covers(p, readings[0]["date"]) for p in state.periods[value]):
        _copy_periods(state, source, value)
    _rebuild_flags(readings, state.periods[value])
    for period in left:
        _follow_closing_readings(state, source, period)
    for period in list(state.periods[value]):
        if any(r["closes_period"] == period["start"] for r in readings):
            _follow_closing_readings(state, value, period)


def _set_retired_at(state: Account, target: Target, value: str | None):
    meter = _record(state, target)
    meter["retired_at"] = value
    if value is None:
        _rebuild_restored(state, meter)


def _set_reading_date(state: Account, target: Target, value: str):
    serial = target.parts[0]
    reading = _record(state, target)
    reading["date"] = value
    _sort_readings(state.readings[serial])
    if reading["closes_period"] is not None:
        apartment_id = account.records(state, "meter")[serial]["apartment_id"]
        account.realign(state, apartment_id, reading["closes_period"], reading["opens_period"])


def _refuse_open_elsewhere(state: Account, apartment_id, kept: list):
    """Refuses while a period of the apartment outside kept is open, since the caller is about to
    open one and two open periods are illegal. An earlier operation of the message leaves such a
    period when it moves a closed period past the open one."""
    others = [p for p in state.periods[apartment_id] if not any(p is k for k in kept)]
    if any(p["closed_on"] is None for p in others):
        raise _Refused(texts.two_open_periods(_apartment_name(state, apartment_id)))


def _reopen(state: Account, apartment_id, start):
    """Clears a period's close, and the open period after it merges back: its row goes and its
    opening readings open the reopened period for a meter that has none there. A live meter still
    with no opening there opens it on its latest reading at or before the close, when that reading
    lies inside the period or stands over from the period straight before, which ended no earlier
    than the day before this one started, as a rebuild carries it: the units from it to the
    meter's next photo then count here and nowhere else. Across a vacancy gap nothing stands for
    the meter, since the units there are nobody's, so its next photo opens the period. A closed
    period after it is refused, as is an open period anywhere else."""
    periods = state.periods[apartment_id]
    index = [p["start"] for p in periods].index(start)
    following = periods[index + 1 : index + 2]
    if following and following[0]["closed_on"] is not None:
        raise _Refused(texts.reopen_refused(_apartment_name(state, apartment_id), parse_iso(start)))
    _refuse_open_elsewhere(state, apartment_id, [periods[index], *following])
    merged = [p["start"] for p in following]
    ended = periods[index]["closed_on"]
    carried = index > 0 and start <= next_day(periods[index - 1]["closed_on"])
    periods[index]["closed_on"] = None
    if merged:
        periods.remove(following[0])
    for meter in state.meters:
        if meter["apartment_id"] != apartment_id:
            continue
        readings = state.readings[meter["serial"]]
        opener = None if any(r["opens_period"] == start for r in readings) else start
        for r in readings:
            if r["closes_period"] == start:
                r["closes_period"] = None
            if r["opens_period"] in merged:
                r["opens_period"] = opener
        if meter["retired_at"] is None and not any(r["opens_period"] == start for r in readings):
            standing = [r for r in readings if r["date"] <= ended]
            if standing and (standing[-1]["date"] >= start or carried):
                standing[-1]["opens_period"] = start


def _detach(state: Account, serial, reading) -> str | None:
    """Takes the reading off its meter and returns the period it opened. A closing reading gives
    its flags to the previous reading after the opening, as a close would, and the periods follow
    it; with none, the period reopens once the reading is gone, so the reopen sees the readings
    that stay. An opening alone passes to the meter's next reading in that period. A closing
    reading that already opens the next period cannot take it, so that is refused: the period's
    units would have no opening to count from. A period whose flags cannot be read is refused as
    check would refuse it, since the copy may be half changed by an earlier operation of the
    message."""
    apartment_id = _meter(state, serial)["apartment_id"]
    readings = state.readings[serial]
    periods = {p["start"]: p for p in state.periods[apartment_id]}
    opens, closes = reading["opens_period"], reading["closes_period"]
    for start in (opens, closes):
        if start is not None and not flags_in_order(readings, periods[start]):
            raise _Refused(texts.closing_before_opening(serial, parse_iso(start)))
    next_ones = [] if opens is None else period_readings(readings, periods[opens])[1:]
    previous = [] if closes is None else period_readings(readings, periods[closes])[1:-1]
    reading.update(opens_period=None, closes_period=None)
    readings.remove(reading)
    if closes is None:
        if next_ones and next_ones[0]["opens_period"] is None:
            next_ones[0]["opens_period"] = opens
        elif next_ones and next_ones[0]["closes_period"] == opens:
            raise _Refused(texts.period_loses_opening(serial, parse_iso(opens)))
    elif previous:
        previous[-1].update(opens_period=opens, closes_period=closes)
        account.realign(state, apartment_id, closes, opens)
    else:
        _reopen(state, apartment_id, closes)
    return opens


def _drop_empty_place(state: Account, apartment_id):
    """Removes an apartment left with no meter and no period, then its property if it has no other
    apartment."""
    if state.periods[apartment_id] or any(m["apartment_id"] == apartment_id for m in state.meters):
        return
    apartment = account.records(state, "apartment")[apartment_id]
    state.apartments.remove(apartment)
    del state.periods[apartment_id]
    if not any(a["property_id"] == apartment["property_id"] for a in state.apartments):
        state.properties.remove(account.records(state, "property")[apartment["property_id"]])


def _replaced_meter(state: Account, meter) -> dict | None:
    """The retired meter of meter's place that its link replaced, under its serial or a former
    one. None when the link replaced none, or that meter has since left the place or come back."""
    place = (meter["apartment_id"], meter["type"])
    for other in state.meters:
        same_place = (other["apartment_id"], other["type"]) == place
        linked = meter["replaces"] in (other["serial"], *other["former_serials"])
        if same_place and linked and other["retired_at"] is not None:
            return other
    return None


def _rebuild_restored(state: Account, meter):
    """A retired meter took no part in the closes after it left, so back live it takes its flags
    from the periods. Left as they were, its opening in a closed period would have no closing, and
    its next photos would count there too."""
    _rebuild_flags(state.readings[meter["serial"]], state.periods[meter["apartment_id"]])


def _drop_if_emptied(state: Account, meter, opened: set):
    """The rule of a meter's last reading, undoing its link, once that reading has left it. A live
    meter's replacement of another is undone, found through the link rather than by date, since a
    corrected reading date leaves retired_at behind. The periods in opened, the starts its readings
    opened, go when no reading opens them now, and then the apartment and property when that
    empties them."""
    if state.readings[meter["serial"]]:
        return
    apartment_id = meter["apartment_id"]
    replaced = _replaced_meter(state, meter) if meter["retired_at"] is None else None
    if replaced is not None:
        replaced["retired_at"] = None
    state.meters.remove(meter)
    del state.readings[meter["serial"]]
    opening = {r["opens_period"] for r in account.apartment_readings(state, apartment_id)}
    state.periods[apartment_id] = [
        p for p in state.periods[apartment_id] if p["start"] not in opened or p["start"] in opening
    ]
    if replaced is not None:
        _rebuild_restored(state, replaced)
    _drop_empty_place(state, apartment_id)


def _takes_opening(periods: list, joined: list, reading) -> bool:
    """True when reading falls before the meter's first reading, inside the period that one opens.
    A first reading that closes the period before keeps its opening: that row stands for the meter
    between the two, so an opening before it would count the same units in both."""
    first = next(iter(joined), None)
    if first is None or first["opens_period"] is None or reading["date"] >= first["date"]:
        return False
    period = next(p for p in periods if p["start"] == first["opens_period"])
    return first["closes_period"] in (None, period["start"]) and _covers(period, reading["date"])


def _set_reading_serial(state: Account, target: Target, value: str):
    """Moves a reading to another meter. Dated before a meter of its type's first reading, inside
    the period that reading opens, it takes the opening. Otherwise it joins as a saved reading
    would when it is dated inside the open period, and an older reading opens nothing, since a
    period it does not fall in is not its to open. A meter left with no reading follows the rule of
    a meter's last reading."""
    serial = target.parts[0]
    reading = _record(state, target)
    meter = _meter(state, serial)
    kept = _meter(state, value)
    opened = {_detach(state, serial, reading)}
    joined = state.readings[value]
    periods = state.periods[kept["apartment_id"]]
    current = open_period(periods)
    if kept["type"] == meter["type"] and _takes_opening(periods, joined, reading):
        reading["opens_period"] = joined[0]["opens_period"]
        joined[0]["opens_period"] = None
    elif current is not None and _covers(current, reading["date"]):
        reading["opens_period"] = joining_opens(current, joined)
    else:
        reading["opens_period"] = None
    joined.append(reading)
    _sort_readings(joined)
    _drop_if_emptied(state, meter, opened)


def _set_period_start(state: Account, target: Target, value: str):
    account.move_period(state, target.parts[0], _record(state, target), value)


def _set_closing(state: Account, target: Target, value: str):
    """The re-close rule: the chosen reading takes both flags of its meter's closing reading in the
    period, and the periods follow it. A meter with none there has nothing to replace, and a
    reading that opens or closes another period would lose that flag."""
    apartment_id, start = target.parts
    serial = value.partition("|")[0]
    meter = account.records(state, "meter")[serial]
    closing = [r for r in state.readings[serial] if r["closes_period"] == start]
    if meter["apartment_id"] != apartment_id or not closing:
        raise _Refused(texts.no_closing_reading(serial, parse_iso(start)))
    chosen = account.records(state, "reading")[value]
    for other in (chosen["closes_period"], chosen["opens_period"]):
        if other not in (None, start):
            raise _Refused(texts.closing_reading_taken(chosen, parse_iso(other)))
    [old] = closing
    opens = old["opens_period"]
    old.update(opens_period=None, closes_period=None)
    chosen.update(opens_period=opens, closes_period=start)
    account.realign(state, apartment_id, start, opens)


def _set_tariff_date(state: Account, target: Target, value: str):
    _record(state, target)["effective_date"] = value
    state.tariffs.sort(key=lambda t: t["effective_date"])


def _set_closed_on(state: Account, target: Target, value: str | None):
    """Clearing reopens the period. A date is refused, since a close's date is its latest closing
    reading's: it moves with that reading's date or with another choice of closing reading."""
    apartment_id, start = target.parts
    if value is not None:
        name = _apartment_name(state, apartment_id)
        raise _Refused(texts.closed_on_follows_readings(name, parse_iso(start)))
    _reopen(state, apartment_id, start)


def _is_vacant(state: Account, apartment_id, period) -> bool:
    """A vacate is stored as nothing more than a close with no next period."""
    return period["closed_on"] is not None and state.periods[apartment_id][-1] is period


def _undo_vacate(state: Account, apartment_id, period):
    """Turns the close into the ordinary one: the next period starts the day after with the same
    length, opened by the closing readings. A row that also opens the closed period is refused,
    since one row has room for one opening, as is an open period anywhere in the apartment."""
    closing = _closing_rows(state, apartment_id, period["start"])
    if any(r["opens_period"] == period["start"] for r in closing):
        name = _apartment_name(state, apartment_id)
        raise _Refused(texts.closing_row_opens_its_period(name, parse_iso(period["start"])))
    _refuse_open_elsewhere(state, apartment_id, [])
    start = next_day(period["closed_on"])
    state.periods[apartment_id].append(new_period(start, period["months"]))
    for r in closing:
        r["opens_period"] = start


def _add_vacate(state: Account, apartment_id, period):
    """Turns the close into a vacate: the next period goes and the closing readings stop opening
    it. An open period has no close to turn, and a next period that is closed or holds readings of
    its own is refused, since those readings would stop counting."""
    name = _apartment_name(state, apartment_id)
    if period["closed_on"] is None:
        raise _Refused(texts.vacate_open_period(name))
    periods = state.periods[apartment_id]
    following = periods[periods.index(period) + 1]
    next_start = parse_iso(following["start"])
    if following["closed_on"] is not None:
        raise _Refused(texts.vacate_next_closed(name, next_start))
    held = [
        r
        for m in state.meters
        if m["apartment_id"] == apartment_id
        for r in period_readings(state.readings[m["serial"]], following)
    ]
    if any(r["closes_period"] != period["start"] for r in held):
        raise _Refused(texts.vacate_next_holds_readings(name, next_start))
    for r in held:
        r["opens_period"] = None
    periods.remove(following)


def _set_vacant(state: Account, target: Target, value: bool):
    change = _add_vacate if value else _undo_vacate
    change(state, target.parts[0], _record(state, target))


# (record, field) -> sets a field whose change is more than its value in its own attribute. Every
# other field in _ATTRIBUTES takes the typed value as it is.
REWRITES: dict[tuple[str, str], Callable[[Account, Target, object], None]] = {
    ("apartment", "months"): _set_apartment_months,
    ("meter", "display"): _set_display,
    ("meter", "serial"): _set_serial,
    ("meter", "type"): _set_type,
    ("meter", "apartment"): _set_apartment,
    ("meter", "retired_at"): _set_retired_at,
    ("reading", "date"): _set_reading_date,
    ("reading", "serial"): _set_reading_serial,
    ("period", "start"): _set_period_start,
    ("period", "closing"): _set_closing,
    ("period", "closed_on"): _set_closed_on,
    ("period", "vacant"): _set_vacant,
    ("tariff", "effective_date"): _set_tariff_date,
}


def _remove_reading(state: Account, serial, reading):
    meter = _meter(state, serial)
    _drop_if_emptied(state, meter, {_detach(state, serial, reading)})


def _delete_reading(state: Account, target: Target):
    """A reset passes to the next reading, whose count would otherwise run back across it. A
    reading moved to another meter takes its reset along, since it never happened on this one."""
    serial = target.parts[0]
    reading = _record(state, target)
    readings = state.readings[serial]
    following = readings[readings.index(reading) + 1 : readings.index(reading) + 2]
    if reading["resets"] and following:
        following[0]["resets"] = True
    _remove_reading(state, serial, reading)


def _delete_meter(state: Account, target: Target):
    """Its rows go at once, so another meter's history stands. Each period it closed, latest first,
    reopens with no closing reading left, and follows those left when its date was this meter's.
    The meter then goes by the rule of a meter's last reading, with every period it opened."""
    meter = _record(state, target)
    apartment_id = meter["apartment_id"]
    readings = state.readings[target.id]
    opened = {r["opens_period"] for r in readings}
    closed = [r["closes_period"] for r in reversed(readings) if r["closes_period"] is not None]
    readings.clear()
    for start in closed:
        if _closing_rows(state, apartment_id, start):
            period = account.records(state, "period")[account.period_id(apartment_id, start)]
            _follow_closing_readings(state, apartment_id, period)
        else:
            _reopen(state, apartment_id, start)
    _drop_if_emptied(state, meter, opened)


def _delete_apartment(state: Account, target: Target):
    """Refused while it holds a meter; with none, its periods hold no reading and go with it."""
    apartment = _record(state, target)
    held = [
        texts.meter_name(m["type"], m["serial"])
        for m in state.meters
        if m["apartment_id"] == target.id
    ]
    if held:
        raise _Refused(texts.apartment_holds_meters(apartment["name"], held))
    state.apartments.remove(apartment)
    del state.periods[target.id]


def _delete_property(state: Account, target: Target):
    prop = _record(state, target)
    held = [a["name"] for a in state.apartments if a["property_id"] == target.id]
    if held:
        raise _Refused(texts.property_holds_apartments(prop["name"], held))
    state.properties.remove(prop)


def _delete_tariff(state: Account, target: Target):
    state.tariffs.remove(_record(state, target))


# Record kind -> deletes a record of that kind. A period has no entry: readings' flags name its
# row, so it goes only by the reading rule or a reopen.
DELETES: dict[str, Callable[[Account, Target], None]] = {
    "property": _delete_property,
    "apartment": _delete_apartment,
    "meter": _delete_meter,
    "reading": _delete_reading,
    "tariff": _delete_tariff,
}


def _closing_id(state: Account, target: Target, raw: str | None) -> str | None:
    """The id of the period's closing reading on the meter that raw, a reading's id, belongs to;
    None when raw names no meter or that meter has none there."""
    serial = None if raw is None else raw.partition("|")[0]
    if serial not in state.readings:
        return None
    closing = [r for r in state.readings[serial] if r["closes_period"] == target.parts[1]]
    return account.reading_id(serial, closing[0]) if closing else None


def _typed(state: Account, target: Target, field, record, raw: str | None):
    """The field's value on record, in the form the field's parser returns; raw is the operation's
    value. An apartment's length is its open period's while it has one, since that is the length
    its next close carries. A period closes on one reading per meter, so its closing reading is
    the one of the meter the new value names."""
    if field.kind == "display":
        return account.display_position(record)
    if (field.record, field.name) == ("apartment", "months"):
        current = open_period(state.periods[record["id"]])
        if current is not None:
            return current["months"]
    if (field.record, field.name) == ("reading", "serial"):
        return target.parts[0]
    if (field.record, field.name) == ("period", "closing"):
        return _closing_id(state, target, raw)
    if (field.record, field.name) == ("period", "vacant"):
        return _is_vacant(state, target.parts[0], record)
    return record[_ATTRIBUTES[(field.record, field.name)]]


def _worded(state: Account, field, value) -> str:
    """The value as the card shows it: a reference by the name of the record it holds the id of, a
    reading by its value and date."""
    if field.kind == "ref" and value is not None:
        reference = _REFERENCES[(field.record, field.name)]
        record = account.records(state, reference)[value]
        value = texts.dated_value(record) if reference == "reading" else record["name"]
    return texts.field_value(field.kind, value)


def _record_label(state: Account, target: Target, record) -> str:
    if target.record == "property":
        return texts.property_label(record["name"])
    if target.record == "apartment":
        return texts.apartment_label(record["name"])
    if target.record == "meter":
        return texts.meter_name(record["type"], record["serial"])
    if target.record == "reading":
        serial = target.parts[0]
        meter = account.records(state, "meter")[serial]
        return texts.reading_label(meter["type"], serial, parse_iso(record["date"]))
    if target.record == "period":
        apartment = account.records(state, "apartment")[target.parts[0]]
        return texts.period_label(apartment["name"], parse_iso(record["start"]))
    if target.record == "delegate":
        return texts.delegate_label(record["email"])
    return texts.tariff_label(parse_iso(record["effective_date"]))


def label(state: Account, raw_target: str) -> str:
    """How a card names the record raw_target names. Raises UnknownTarget for one the account
    lacks."""
    target = parse_target(raw_target)
    if target.record not in account.RECORD_KINDS:
        raise UnknownTarget(raw_target)
    known = account.records(state, target.record)
    resolve(target, known)
    return _record_label(state, target, known[target.id])


def _located(state: Account, operation: Operation) -> tuple[Target, dict]:
    """A delegate is named by an address, stored lowercase, which the model may write in any case.
    It is the one id the owner types, so one missing is answered with a next step."""
    target = parse_target(operation.target)
    if target.record not in account.RECORD_KINDS:
        raise UnknownTarget(operation.target)
    known = account.records(state, target.record)
    if target.record == "delegate":
        target = parse_target(operation.target.lower())
        if target.id not in known:
            raise _Refused(texts.unknown_delegate(target.id))
    return resolve(target, known), known[target.id]


def _parsed(state: Account, field, operation: Operation, today: date, old):
    """The operation's value as the field's type, with a reference naming a record that exists.
    Raises InvalidValue with the value on record."""
    reference = _REFERENCES.get((field.record, field.name))
    try:
        value = parse(field, operation.value, today)
        if reference is not None and value not in account.records(state, reference):
            raise InvalidValue(field, operation.value)
    except InvalidValue as error:
        current = _worded(state, field, old)
        raise InvalidValue(field, error.raw, current, operation.target) from error
    return value


def _deletion_line(state: Account, target: Target, record) -> str:
    if target.record == "property":
        return texts.property_removed(record["name"])
    if target.record == "apartment":
        return texts.apartment_removed(record["name"])
    if target.record == "meter":
        return texts.meter_removed(record["type"], record["serial"])
    label = _record_label(state, target, record)
    if target.record == "reading":
        return texts.reading_removed(label, record["value"])
    return texts.tariff_removed(label)


def _deleted(before: Account, state: Account, operation: Operation) -> Deletion:
    """Deletes the record the model named. One an earlier operation of the plan already removed
    counts as done and keeps its line, as a message naming a meter and its reading means both."""
    target, record = _located(before, operation)
    if target.record == "delegate":
        raise _Refused(texts.delegate_not_removable(target.id))
    if target.record not in DELETES:
        name = _apartment_name(state, target.parts[0])
        raise _Refused(texts.period_not_deletable(name, parse_iso(record["start"])))
    if target.id in account.records(state, target.record):
        DELETES[target.record](state, target)
    return Deletion(_deletion_line(before, target, record))


def _applied(
    before: Account, state: Account, operation: Operation, today: date
) -> Change | Deletion | None:
    """Applies the operation to state; None for a set whose field already holds the value."""
    if operation.action == "delete":
        return _deleted(before, state, operation)
    target, record = _located(state, operation)
    field = field_for(target.record, operation.field)
    if (field.record, field.name) not in _ATTRIBUTES:
        raise UnknownField(f"{field.record}.{field.name}")
    old = _typed(state, target, field, record, operation.value)
    value = _parsed(state, field, operation, today, old)
    if value == old:
        return None
    label = _record_label(state, target, record)
    worded_old = _worded(state, field, old)
    rewrite = REWRITES.get((field.record, field.name))
    if rewrite is None:
        record[_ATTRIBUTES[(field.record, field.name)]] = value
    else:
        rewrite(state, target, value)
    return Change(label, field.label, worded_old, _worded(state, field, value))


# The record kinds only the owner deletes, the widest deletes in the app. A delegate is not told of
# a step that is the owner's to take.
_OWNER_ONLY_DELETES = ("apartment", "property", "delegate")

# The order deletes run in, so a record is emptied before what holds it is deleted.
_DELETE_ORDER = ("reading", "meter", "tariff", "period", "apartment", "property")


def _application_rank(operation: Operation) -> tuple[int, int]:
    """Plain sets, then deletes by kind, then operations that change their target's id. A field or
    a kind outside the tables ranks anywhere, and fails when it is applied."""
    kind = operation.target.partition("|")[0]
    if operation.action == "delete":
        return 1, _DELETE_ORDER.index(kind) if kind in _DELETE_ORDER else 0
    field = FIELDS.get((kind, operation.field))
    return (2, 0) if field is not None and field.key else (0, 0)


def _in_application_order(operations: list[Operation]) -> list[Operation]:
    """Deletes of one kind run from the highest id down, so a meter's readings go latest first and
    the message's order of its deletes changes nothing. Sets keep the message's order."""
    deletes = [o for o in operations if o.action == "delete"]
    sets = [o for o in operations if o.action != "delete"]
    latest_first = sorted(deletes, key=lambda o: o.target, reverse=True)
    return sorted([*sets, *latest_first], key=_application_rank)


def _earlier_row(earlier: dict, meter) -> dict:
    """The meter's row before the change, from the rows by serial; a renamed meter's is under its
    latest former serial that had a row."""
    serials = [meter["serial"], *reversed(meter["former_serials"])]
    return earlier[next(serial for serial in serials if serial in earlier)]


def _starts_of_copy(before: Account, after: Account) -> dict:
    """Key: the id() of a period row of the untouched copy after
    Value: (that row, its start). The start stays the period's name once a rewrite moves it in
    place; holding the row keeps its id from going to a period the plan creates after removing it"""
    return {
        id(row): (row, period["start"])
        for apartment_id, periods in before.periods.items()
        for period, row in zip(periods, after.periods[apartment_id], strict=True)
    }


def _earlier_start(starts: dict, period) -> str:
    """The period's start before the plan; a row the plan created has only its own."""
    return starts[id(period)][1] if id(period) in starts else period["start"]


def _all_figures(state: Account, today: date, key_of) -> dict:
    """Key: key_of(meter, period)
    Value: the (meter, period start, Figures or None) of that meter in that period"""
    return {
        key_of(m, p): (
            m,
            p["start"],
            figures(m["type"], state.readings[m["serial"]], p, state.tariffs, today),
        )
        for m in state.meters
        for p in state.periods[m["apartment_id"]]
    }


def _shown_figures(f: Figures | None) -> tuple | None:
    """The figures as the card compares them, so two that bill the same are not a change: a zero
    charge bills what no charge does, as a period just opened with no use yet shows."""
    if f is None:
        return None
    billed = None if f.amount == 0 else f.amount
    return f.consumption.units, billed


def _compared(before: Account, after: Account, starts: dict, today: date) -> list[FigureChange]:
    """Figures are matched by the apartment, period and meter as before named them, so a rename
    or a moved period is compared with itself. A pair only one side has reads as no figures on
    the other."""
    earlier = account.records(before, "meter")

    def named_as_before(meter, period) -> tuple:
        start = _earlier_start(starts, period)
        return meter["apartment_id"], start, _earlier_row(earlier, meter)["serial"]

    old = _all_figures(before, today, lambda m, p: (m["apartment_id"], p["start"], m["serial"]))
    new = _all_figures(after, today, named_as_before)

    def change(state: Account, meter, start, before_type, was, now) -> FigureChange:
        apartment = account.records(state, "apartment")[meter["apartment_id"]]
        prop = account.records(state, "property")[apartment["property_id"]]
        serial = meter["serial"]
        return FigureChange(
            prop["name"], apartment["name"], meter["type"], serial, start, before_type, was, now
        )

    changes = []
    for key, (meter, start, now) in new.items():
        before_type, was = meter["type"], None
        if key in old:
            before_type, was = old[key][0]["type"], old[key][2]
        if _shown_figures(was) != _shown_figures(now):
            changes.append(change(after, meter, start, before_type, was, now))
    for key, (meter, start, was) in old.items():
        if key not in new and was is not None:
            changes.append(change(before, meter, start, meter["type"], was, None))
    return changes


def repriced(before: Account, after: Account, today: date) -> list[FigureChange]:
    """The figures that differ in after, a copy of before whose tariffs alone changed."""
    return _compared(before, after, _starts_of_copy(before, after), today)


def _addresses(state: Account) -> set[str]:
    return {d["email"] for d in state.delegates}


def _repeated_delegates(state: Account) -> set[str]:
    addresses = Counter(d["email"] for d in state.delegates)
    return {address for address, count in addresses.items() if count > 1}


def lost_and_gained(before: Account, after: Account) -> tuple[list[str], list[str]]:
    """The addresses that leave the account and those that join it, judged by the two sets, so that
    a message moving one delegate onto an address another leaves names only the two affected."""
    was, now = _addresses(before), _addresses(after)
    return sorted(was - now), sorted(now - was)


def _access_lines(before: Account, after: Account) -> list[str]:
    lost, gained = lost_and_gained(before, after)
    return [
        *[texts.access_lost(address) for address in lost],
        *[texts.access_gained(address) for address in gained],
    ]


def _clashing_names(state: Account) -> set[tuple]:
    """(property id or None, name) pairs held by two records: a name repeats neither among the
    properties nor among one property's apartments."""
    names = Counter((None, p["name"]) for p in state.properties)
    names.update((a["property_id"], a["name"]) for a in state.apartments)
    return {key for key, count in names.items() if count > 1}


def _name_refusals(before: Account, after: Account) -> list[str]:
    properties = account.records(after, "property")
    refusals = []
    for parent, name in sorted(
        _clashing_names(after) - _clashing_names(before), key=lambda clash: clash[1]
    ):
        if parent is None:
            refusals.append(texts.duplicate_property(name))
        else:
            refusals.append(texts.duplicate_apartment(name, properties[parent]["name"]))
    return refusals


def _out_of_order(readings: list) -> list[tuple[dict, dict]]:
    """Neighboring readings whose value falls with no reset between them."""
    return [
        (earlier, later)
        for earlier, later in pairwise(readings)
        if later["value"] < earlier["value"] and not later["resets"]
    ]


def _order_warnings(before: Account, after: Account) -> list[str]:
    """Only pairs the change brought about, since an earlier fall is not this change's doing."""
    known = [pair for readings in before.readings.values() for pair in _out_of_order(readings)]
    return [
        texts.out_of_order(serial, earlier, later)
        for serial, readings in after.readings.items()
        for earlier, later in _out_of_order(readings)
        if (earlier, later) not in known
    ]


def _display_checks(before: Account, after: Account) -> tuple[list[str], list[str]]:
    """A photo's number is looked up by the meter's display choice, so a single counter, which
    has no such position, must keep none. On a two-number display, the other number was never
    stored, so the card lists the readings the change leaves as they are. A merge is no such
    change: the card only says which of two different choices stays."""
    refusals, warnings = [], []
    earlier = account.records(before, "meter")
    merged = _merged(before, after)
    for meter in after.meters:
        serial = meter["serial"]
        taken = [gone["display_choice"] for gone, kept in merged if kept["serial"] == serial]
        if meter["display_choice"] in [_earlier_row(earlier, meter)["display_choice"], *taken]:
            continue
        if meter["type"] != _SINGLE_COUNTER:
            warnings.extend(
                texts.display_past_readings(meter["type"], serial, after.readings[serial])
            )
        elif meter["display_choice"] is not None:
            refusals.append(texts.single_counter_display(serial))
    for gone, kept in merged:
        if None not in (gone["display_choice"], kept["display_choice"]) and (
            gone["display_choice"] != kept["display_choice"]
        ):
            warnings.append(
                texts.merged_display_differs(
                    kept["type"], kept["serial"], account.display_position(kept)
                )
            )
    return refusals, warnings


def _same_day_clashes(state: Account) -> set[tuple]:
    """(serial, date) pairs a meter holds two readings on."""
    days = Counter(
        (serial, r["date"]) for serial, readings in state.readings.items() for r in readings
    )
    return {day for day, count in days.items() if count > 1}


def _repeated_tariffs(state: Account) -> set[str]:
    dates = Counter(t["effective_date"] for t in state.tariffs)
    return {effective for effective, count in dates.items() if count > 1}


def _overlaps(state: Account) -> set[tuple]:
    """(apartment id, start) pairs of periods that run into the next one, or that close before
    they start. A period may start on the day its neighbor closed, since a closing reading can
    stand for both."""
    found = set()
    for apartment_id, periods in state.periods.items():
        for p in periods:
            if p["closed_on"] is not None and p["closed_on"] < p["start"]:
                found.add((apartment_id, p["start"]))
        for p, following in pairwise(periods):
            starts_with_it = following["start"] <= p["start"]
            if starts_with_it or p["closed_on"] is None or following["start"] < p["closed_on"]:
                found.add((apartment_id, p["start"]))
    return found


def _misordered_flags(state: Account) -> set[tuple]:
    """(serial, period start) pairs whose flags period_readings could not read."""
    return {
        (m["serial"], p["start"])
        for m in state.meters
        for p in state.periods[m["apartment_id"]]
        if not flags_in_order(state.readings[m["serial"]], p)
    }


def _unclosed_openings(state: Account) -> dict:
    """An opening followed by the opening of another period with no closing between them. A period
    with no closing runs to the meter's last reading, so it would count the next one's units too.
    A row that opens and closes one period leaves nothing running.
    Key: (start, the opening row's seq), which a renamed meter keeps
    Value: the meter's serial"""
    found = {}
    for serial, readings in state.readings.items():
        running = None
        for r in readings:
            if r["closes_period"] is not None:
                running = None
            opens = r["opens_period"]
            if opens is None or opens == r["closes_period"]:
                continue
            if running is None:
                running = (opens, r["seq"])
            elif running[0] != opens:
                found[running] = serial
                running = (opens, r["seq"])
    return found


def _new_faults(find, before: Account, after: Account) -> list[tuple]:
    """The ((start, seq), serial) pairs find holds of after and not of before, matched by the
    opening row, so renaming a meter does not make an old fault new."""
    earlier = find(before)
    return [(key, serial) for key, serial in sorted(find(after).items()) if key not in earlier]


def _opening_refusals(before: Account, after: Account) -> list[str]:
    """Only an unclosed opening the change brought about, on a meter whose flags can be read."""
    unreadable = {serial for serial, _ in _misordered_flags(after)}
    return [
        texts.opening_without_closing(serial, parse_iso(start))
        for (start, _), serial in _new_faults(_unclosed_openings, before, after)
        if serial not in unreadable
    ]


def _unclosed_live_meters(state: Account) -> dict:
    """A live meter's opening in a closed period with no closing of its own there. Its next
    readings would count in that period as well as in the open one.
    Key: (start, the opening row's seq)
    Value: the meter's serial"""
    found = {}
    for meter in state.meters:
        if meter["retired_at"] is not None:
            continue
        readings = state.readings[meter["serial"]]
        closing = {r["closes_period"] for r in readings}
        for p in state.periods[meter["apartment_id"]]:
            if p["closed_on"] is not None and p["start"] not in closing:
                for r in readings:
                    if r["opens_period"] == p["start"]:
                        found[(p["start"], r["seq"])] = meter["serial"]
    return found


def _counted_rows(state: Account) -> list[tuple]:
    """(serial, reading) for each reading some period of its meter's apartment counts."""
    counted = []
    for meter in state.meters:
        readings = state.readings[meter["serial"]]
        inside = [
            r
            for p in state.periods[meter["apartment_id"]]
            if flags_in_order(readings, p)
            for r in period_readings(readings, p)
        ]
        counted.extend((meter["serial"], r) for r in readings if any(r is i for i in inside))
    return counted


def _stranded_readings(before: Account, after: Account) -> list[tuple]:
    """(serial, reading) of each reading of after that a period counted before and none counts
    now. A row is followed by its seq, a stamp set when it is saved, so it is found wherever a
    rename, merge or move left it; a seq two meters share counts once per meter."""
    held = Counter(r["seq"] for rows in before.readings.values() for r in rows)
    held.subtract(r["seq"] for rows in after.readings.values() for r in rows)
    lost = Counter(r["seq"] for _, r in _counted_rows(before))
    lost.subtract(r["seq"] for _, r in _counted_rows(after))
    stranded = {seq for seq, count in lost.items() if count > held[seq]}
    counted = {(serial, id(r)) for serial, r in _counted_rows(after)}
    return [
        (serial, r)
        for serial, rows in after.readings.items()
        for r in rows
        if r["seq"] in stranded and (serial, id(r)) not in counted
    ]


def _holders_by_seq(state: Account) -> dict:
    """Key: a reading's seq
    Value: the serials of the meters holding a reading of that seq"""
    holders = {}
    for serial, readings in state.readings.items():
        for r in readings:
            holders.setdefault(r["seq"], set()).add(serial)
    return holders


def _dangling_flags(state: Account) -> list[tuple]:
    """(serial, reading, start) for each flag on a row naming no period of its meter's apartment.
    Such a row's period cannot be read, and the units it bounded have no period to count in."""
    found = []
    for meter in state.meters:
        starts = {p["start"] for p in state.periods[meter["apartment_id"]]}
        for r in state.readings[meter["serial"]]:
            named = {r["opens_period"], r["closes_period"]} - {None}
            found.extend((meter["serial"], r, start) for start in sorted(named - starts))
    return found


def _guard_refusals(before: Account, after: Account) -> list[str]:
    """The billing failures, judged on the result alone, so no route to them gets past: a reading
    that stops counting, a live meter that counts a closed period's successor twice, and a flag
    naming a period its apartment no longer has. A reading on another meter than before is told to
    change its date or go, since no reopen frees it. For any other stranded reading, only the
    apartment's last period can be reopened alone, and a reading stranded past a close joins the
    open period that makes, so that is the period named."""
    meters = account.records(after, "meter")
    holders = _holders_by_seq(before)

    def stranded_line(serial, r) -> str:
        meter = meters[serial]
        day = parse_iso(r["date"])
        if holders[r["seq"]].isdisjoint({serial, *meter["former_serials"]}):
            return texts.moved_reading_stops_counting(serial, day)
        return texts.reading_stops_counting(
            serial, day, _apartment_name(after, meter["apartment_id"])
        )

    return [
        *[stranded_line(serial, r) for serial, r in _stranded_readings(before, after)],
        *[
            texts.meter_left_unclosed(serial, parse_iso(start))
            for (start, _), serial in _new_faults(_unclosed_live_meters, before, after)
        ],
        *[
            texts.reading_names_missing_period(
                serial,
                parse_iso(r["date"]),
                parse_iso(start),
                _apartment_name(after, meters[serial]["apartment_id"]),
            )
            for serial, r, start in _dangling_flags(after)
        ],
    ]


def _new(find, before: Account, after: Account) -> list:
    """What find sees in after and not in before, since a fault already on record is not this
    change's doing and must not block it."""
    return sorted(find(after) - find(before))


def _live_meter_refusals(before: Account, after: Account) -> list[str]:
    """A live meter that comes to another apartment or type, or back from retirement, where
    another live meter of that type stands. A retired meter holds no such place, so it moves
    freely."""
    earlier = account.records(before, "meter")
    apartments = account.records(after, "apartment")
    live = [m for m in after.meters if m["retired_at"] is None]
    refusals = []
    for meter in live:
        place = (meter["apartment_id"], meter["type"])
        was = _earlier_row(earlier, meter)
        if was["retired_at"] is None and place == (was["apartment_id"], was["type"]):
            continue
        if place in [(m["apartment_id"], m["type"]) for m in live if m is not meter]:
            refusals.append(texts.live_meter_exists(meter["type"], apartments[place[0]]["name"]))
    return refusals


def _took_copies(before: Account, after: Account, source, apartment_id, starts: dict) -> bool:
    """True when the apartment holds a period the plan made from one of the source's."""
    source_starts = {p["start"] for p in before.periods[source]}
    return any(
        id(p) not in starts and p["start"] in source_starts for p in after.periods[apartment_id]
    )


def _arrivals(before: Account, after: Account, meter) -> list[tuple]:
    """(source apartment, the dates of the rows that came from it) for the meter of after: all its
    rows when it moved, and a merged-in meter's rows when that meter came from elsewhere."""
    apartment_id = meter["apartment_id"]
    rows = after.readings[meter["serial"]]
    source = _earlier_row(account.records(before, "meter"), meter)["apartment_id"]
    if source != apartment_id:
        return [(source, [r["date"] for r in rows])]
    arrivals = []
    for gone, kept in _merged(before, after):
        if kept["serial"] == meter["serial"] and gone["apartment_id"] != apartment_id:
            brought = {r["seq"] for r in before.readings[gone["serial"]]}
            days = [r["date"] for r in rows if r["seq"] in brought]
            arrivals.append((gone["apartment_id"], days))
    return arrivals


def _uncovered_refusals(before: Account, after: Account, starts: dict) -> list[str]:
    """Rows that came from another apartment, on a meter that kept the target's own periods, took
    no copies, so a row no period covers, before them or after them, would stop counting."""
    apartments = account.records(after, "apartment")
    refusals = []
    for meter in after.meters:
        apartment_id = meter["apartment_id"]
        periods = after.periods[apartment_id]
        for source, days in _arrivals(before, after, meter):
            if not periods or _took_copies(before, after, source, apartment_id, starts):
                continue
            uncovered = [d for d in days if not any(_covers(p, d) for p in periods)]
            if uncovered:
                name = apartments[apartment_id]["name"]
                refusals.append(texts.reassign_uncovered(name, [parse_iso(d) for d in uncovered]))
    return refusals


def _merged(before: Account, after: Account) -> list[tuple[dict, dict]]:
    """(gone row, kept row), both from before, for each meter that took another's readings: one
    that gained a former serial still in use before."""
    earlier = account.records(before, "meter")
    pairs = []
    for meter in after.meters:
        if meter["serial"] in earlier:
            kept = earlier[meter["serial"]]
            new_serials = set(meter["former_serials"]) - set(kept["former_serials"])
            pairs.extend((earlier[s], kept) for s in sorted(new_serials & earlier.keys()))
    return pairs


def _period_lines(name, periods: list, after_periods: list, starts: dict) -> list[str]:
    """The apartment's periods the change removes or reopens, matched by their starts before it,
    so a moved period is compared with itself."""
    left = {_earlier_start(starts, p): p for p in after_periods}
    lines = []
    for p in periods:
        start = parse_iso(p["start"])
        if p["start"] not in left:
            lines.append(texts.period_removed(name, start))
        elif p["closed_on"] is not None and left[p["start"]]["closed_on"] is None:
            lines.append(texts.period_reopened(name, start))
    return lines


def _removal_lines(before: Account, after: Account, starts: dict) -> list[str]:
    """Every record the change removes rather than renames, every period it reopens and every
    replacement it undoes."""
    earlier = account.records(before, "meter")
    apartments = account.records(before, "apartment")
    known = {s for m in after.meters for s in (m["serial"], *m["former_serials"])}
    lines = [
        texts.meter_removed(m["type"], m["serial"])
        for m in before.meters
        if m["serial"] not in known
    ]
    for meter in after.meters:
        was = earlier.get(meter["serial"])
        if (
            was is not None
            and was["retired_at"] is not None
            and meter["retired_at"] is None
            and was["former_serials"] == meter["former_serials"]
        ):
            lines.append(texts.replacement_undone(meter["type"], meter["serial"]))
    for apartment_id, periods in before.periods.items():
        name = apartments[apartment_id]["name"]
        lines.extend(_period_lines(name, periods, after.periods.get(apartment_id, []), starts))
    apartments_left = account.records(after, "apartment")
    properties_left = account.records(after, "property")
    lines.extend(
        texts.apartment_removed(a["name"])
        for a in before.apartments
        if a["id"] not in apartments_left
    )
    lines.extend(
        texts.property_removed(p["name"])
        for p in before.properties
        if p["id"] not in properties_left
    )
    return lines


def _moved_openings(before: Account, after: Account) -> list[str]:
    """The readings that come to open another period, on their own meter or on one they moved to.
    A row is followed by its seq, so a renamed meter's openings read as they were."""
    earlier = {}
    for readings in before.readings.values():
        for r in readings:
            earlier.setdefault(r["seq"], set()).add(r["opens_period"])
    meters = account.records(after, "meter")
    return [
        texts.opening_moved(
            meters[serial]["type"],
            serial,
            r["value"],
            parse_iso(r["date"]),
            parse_iso(r["opens_period"]),
        )
        for serial, readings in after.readings.items()
        for r in readings
        if r["opens_period"] is not None and r["opens_period"] not in earlier[r["seq"]]
    ]


def check(before: Account, after: Account, starts: dict) -> tuple[list[str], list[str]]:
    """The refusals and warnings of the change from before to after; starts is _starts_of_copy's
    map. A value out of order is a warning, because fixing two wrong readings passes through an
    out-of-order state. The guard, _guard_refusals' check of the result as a whole, speaks only
    where no rule did, since a rule's reason is the sharper one."""
    display_refusals, display_warnings = _display_checks(before, after)
    apartments = account.records(after, "apartment")
    refusals = [
        *_name_refusals(before, after),
        *display_refusals,
        *[
            texts.same_day_clash(serial, parse_iso(day))
            for serial, day in _new(_same_day_clashes, before, after)
        ],
        *_live_meter_refusals(before, after),
        *_uncovered_refusals(before, after, starts),
        *[texts.duplicate_tariff(parse_iso(d)) for d in _new(_repeated_tariffs, before, after)],
        *[
            texts.periods_overlap(apartments[apartment_id]["name"], parse_iso(start))
            for apartment_id, start in _new(_overlaps, before, after)
        ],
        *[
            texts.closing_before_opening(serial, parse_iso(start))
            for serial, start in _new(_misordered_flags, before, after)
        ],
        *_opening_refusals(before, after),
        *[texts.delegate_exists(a) for a in _new(_repeated_delegates, before, after)],
    ]
    refusals = refusals or _guard_refusals(before, after)
    warnings = [
        *_order_warnings(before, after),
        *display_warnings,
        *_moved_openings(before, after),
        *_removal_lines(before, after, starts),
        *_access_lines(before, after),
    ]
    return refusals, warnings


def _refuse_crossing_serials(before: Account, operations: list[Operation]):
    """Refuses a rename onto a serial that exists when the same operations rename the meter holding
    it: chains through it, rotations and swaps read differently in each order. A chain through a
    new serial holds no such meter."""
    renames = [
        (o.target.partition("|")[2], o.value.strip())
        for o in operations
        if o.target.startswith("meter|") and o.field == "serial" and o.value is not None
    ]
    existing = account.records(before, "meter")
    renamed = {old for old, new in renames if old != new}
    if any(new in existing and new in renamed for _, new in renames):
        raise _Refused(texts.one_serial_at_a_time())


def address_refusal(store, address: str) -> str | None:
    """Why the address cannot become a delegate, or None. One access row cannot hold two roles, so
    an address keeps a delegate row it has elsewhere and an account that holds records or delegates
    of its own. An owner row of an empty account, which any sign-in leaves, is free to replace. One
    text answers both, so an owner learns nothing of what another account holds.

    This is a read, not a condition of the write: a transaction cannot test that an account holds
    nothing. Records that arrive between it and the write stay under that address, recoverable."""
    row = store.access_get(address.lower())
    if row is None:
        return None
    if row["role"] != OWNER:
        return texts.address_taken()
    reads = (store.properties, store.apartments, store.meters, store.tariffs, store.delegates)
    return texts.address_taken() if any(read(row["account"]) for read in reads) else None


def access_refusals(store, before: Account, after: Account) -> list[str]:
    """address_refusal of each address the change gives the account to."""
    found = [address_refusal(store, a) for a in lost_and_gained(before, after)[1]]
    return [refusal for refusal in found if refusal is not None]


def _needs_owner(operation: Operation) -> bool:
    kind = operation.target.partition("|")[0]
    if operation.action == "delete":
        return kind in _OWNER_ONLY_DELETES
    field = FIELDS.get((kind, operation.field))
    return field is not None and field.owner_only


def _refuse_for_role(operations: list[Operation], role: str):
    if role != OWNER and any(_needs_owner(o) for o in operations):
        raise _Refused(texts.owner_only())


def _removes_history(before: Account, after: Account) -> bool:
    """True when after lacks a property, an apartment or a meter that held more than one reading.
    A renamed or merged meter stays known by its former serial. One reading is a link its maker
    undoes, and the place made for it goes with it, by the rule of a meter's last reading."""
    known = {s for m in after.meters for s in (m["serial"], *m["former_serials"])}
    apartments_left = account.records(after, "apartment")
    properties_left = account.records(after, "property")
    held = {a["id"]: len(account.apartment_readings(before, a["id"])) for a in before.apartments}
    counts = [len(before.readings[m["serial"]]) for m in before.meters if m["serial"] not in known]
    counts.extend(count for a, count in held.items() if a not in apartments_left)
    counts.extend(
        sum(held[a["id"]] for a in before.apartments if a["property_id"] == p["id"])
        for p in before.properties
        if p["id"] not in properties_left
    )
    return any(count > 1 for count in counts)


def _refuse_removal_for_role(before: Account, after: Account, role: str):
    """Judged on the result, so no wider delete or cascade gets past a delegate."""
    if role != OWNER and _removes_history(before, after):
        raise _Refused(texts.owner_only())


def plan(before: Account, operations: list[Operation], today: date, role: str) -> Plan:
    """Applies the operations to a copy of before, by _in_application_order, for a user of the
    role. Raises UnknownTarget, UnknownField or InvalidValue for an operation that cannot be
    applied; before is never changed. A change over one transaction's rows is a refusal naming its
    size."""
    after = before.copy()
    starts = _starts_of_copy(before, after)
    ordered = _in_application_order(operations)
    try:
        _refuse_for_role(ordered, role)
        _refuse_crossing_serials(before, ordered)
        applied = [_applied(before, after, operation, today) for operation in ordered]
        _refuse_removal_for_role(before, after, role)
    except _Refused as refused:
        return Plan(after, [], [], [str(refused)], [])
    changes = [change for change in applied if change is not None]
    refusals, warnings = check(before, after, starts)
    if refusals:
        return Plan(after, changes, [], refusals, warnings)
    size = account.diff(before, after).size()
    if size > MAX_TRANSACTION_ITEMS:
        return Plan(after, changes, [], [texts.correction_too_large(size)], warnings)
    return Plan(after, changes, _compared(before, after, starts, today), [], warnings)
