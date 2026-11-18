"""Missing-run check: `python -m nightwatch.slots`.

The timer should start the collector at fixed times (slots): minutes 07 and 37 of every hour,
plus 12:10, 12:25 and 12:40 UTC (17:10, 17:25, 17:40 Tashkent). That is 51 slots a day.
This module compares that list with the table `collection_runs` (proposal v3 section 6) and puts
every slot in one of three groups:

- data     at least one run for the slot stored at least one point;
- no data  a run started for the slot, but no try stored anything (the failed runs are kept);
- no run   nothing started for the slot at all. This is a missing run. It is written once to
           `data_quality_log` (rule `missing_run`).

Slots are counted from 00:00 Tashkent time on the first counted day (D007) to the final
collection time (D006). Manual runs belong to no slot. Details are in DECISIONS.md D011.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from . import config, db
from .nights import TZ, at

DATA, NO_DATA, NO_RUN = "data", "no data", "no run"
GRACE = timedelta(minutes=45)   # a slot is judged only when its run and both retries must be over


def first_slot_time() -> datetime:
    return at(config.FIRST_NIGHT, 0)


def expected_slots(start: datetime, end: datetime) -> list[datetime]:
    """Every schedule slot from start to end (both included), in UTC, in time order."""
    slots = []
    hour = start.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    while hour <= end:
        minutes = set(config.HOURLY_MINUTES) | {m for h, m in config.EXTRA_SLOTS_UTC if h == hour.hour}
        slots += [s for s in (hour.replace(minute=m) for m in sorted(minutes)) if start <= s <= end]
        hour += timedelta(hours=1)
    return slots


def classify(slots: list[datetime], runs: dict) -> list[tuple[datetime, str]]:
    """runs: {slot: (number of runs, number of runs that stored at least one point)}."""
    result = []
    for slot in slots:
        started, with_data = runs.get(slot, (0, 0))
        result.append((slot, DATA if with_data else NO_DATA if started else NO_RUN))
    return result


def label(slot: datetime) -> str:
    return f"slot {slot:%Y-%m-%d %H:%M}Z"


def check(conn, now: datetime, save: bool = True) -> list[tuple[datetime, str]]:
    """Compare the schedule with collection_runs. Logs each missing run once when save is true."""
    end = min(now - GRACE, config.FINAL_COLLECTION_UTC)
    slots = expected_slots(first_slot_time(), end)
    runs = {slot: (started, with_data) for slot, started, with_data in conn.execute(
        """SELECT scheduled_for, count(*), count(*) FILTER (WHERE points_ok > 0)
             FROM collection_runs
            WHERE scheduled_for IS NOT NULL AND trigger IN ('schedule', 'backup')
            GROUP BY scheduled_for""")}
    result = classify(slots, runs)
    if save:
        logged = {detail for (detail,) in conn.execute(
            "SELECT detail FROM data_quality_log WHERE rule_name = 'missing_run'")}
        new = [(f"{label(slot)}: no run started",) for slot, state in result
               if state == NO_RUN and f"{label(slot)}: no run started" not in logged]
        if new:
            with conn.cursor() as cur:
                cur.executemany("INSERT INTO data_quality_log (rule_name, action, detail) "
                                "VALUES ('missing_run', 'flagged', %s)", new)
        conn.commit()
    return result


def slot_report(result: list[tuple[datetime, str]]) -> str:
    """One line per Tashkent day. The newest day can be part of a day."""
    by_day = defaultdict(Counter)
    for slot, state in result:
        by_day[slot.astimezone(TZ).date()][state] += 1
    lines = ["day         slots  data  no data  no run",
             "----------  -----  ----  -------  ------"]
    for day in sorted(by_day):
        c = by_day[day]
        lines.append(f"{day}  {sum(c.values()):>5}  {c[DATA]:>4}  {c[NO_DATA]:>7}  {c[NO_RUN]:>6}")
    total = Counter(state for _, state in result)
    lines.append("")
    lines.append(f"All slots: {len(result)}. With data: {total[DATA]}. "
                 f"Run but no data: {total[NO_DATA]}. No run (missing): {total[NO_RUN]}.")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="NightWatch UZ missing-run check")
    parser.add_argument("--report-only", action="store_true", help="print the report, store nothing")
    args = parser.parse_args(argv)
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is not set.", file=sys.stderr)
        return 2
    now = datetime.now(timezone.utc)
    with db.connect(dsn) as conn:
        result = check(conn, now, save=not args.report_only)
    print(f"Schedule slots checked at {now:%Y-%m-%d %H:%M}Z against collection_runs.\n")
    print(slot_report(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
