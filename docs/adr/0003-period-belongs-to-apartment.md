# 0003. The billing period belongs to the apartment, not the meter

- Status: Accepted
- Date: 2026-10-28

## Context

The owner bills each apartment for the water and electricity it used in a billing period,
two months by default. An apartment has one meter of each kind, photographed on the same round,
and a tenant moves in or out on one date for both. A period closes on the first reading at or
after its end, and the next one starts the day after with the same length. In practice the owner
also closes early, leaves an apartment vacant, or agrees a start date with a new tenant, so fixed
arithmetic alone would force workarounds.

## Decision

- Periods are rows of their own, one per apartment and start date, with a length in months and,
  once closed, the date they closed on. Both meters of an apartment share them; at most one is
  open at a time.
- These rules are the default: a reading on or after the open period's end proposes closing it,
  and a close opens the next period the day after, with the same length, its opening readings
  being the closing ones.
- The owner overrides by free text, each change shown on a card and written only on
  confirmation: close now with each meter's latest reading; vacate, which closes without opening
  the next; and a new start date or length for the open period, or a new period on a vacant
  apartment.
- A reading carries flags naming the period it opens and the period it closes, so moving a
  period re-points its readings instead of recomputing them from dates.
- Consumption, forecasts and charges are computed from readings and period rows on every
  request; nothing derived is stored.

## Consequences

- One close card covers both meters, and a close waits for every live meter to have a reading.
- A late or back-dated photo near a closing reading has to be handled explicitly (re-close or
  swap) rather than silently changing a closed period.
- A correction to a reading or a period changes every figure computed from it with no migration.
