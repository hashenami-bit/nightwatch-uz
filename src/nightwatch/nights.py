"""Nights builder: `python -m nightwatch.nights`.

Turns the stored hourly rows into one row per monitored point per night (table `nights`).
All rules come from proposal v3 section 3; the details fixed here are in DECISIONS.md D010.

For day D (all times Asia/Tashkent):
- snapshot      = the latest stored response received from 16:00 to 18:00 on D;
- day features  = live values of D received before 18:00: maximum 07:00-16:00 (at least 7 of
                  10 hours), minimum 00:00-07:00 (at least 6 of 8), mean cloud cover 12:00-16:00
                  (at least 4 of 5);
- night         = the 12 hourly valid times from 20:00 on D to 07:00 on D+1;
- target        = the minimum of the 12 live night values; fewer than 12 means no target;
- provider forecast = the forecast for the same 12 hours, from the snapshot response only.

A point's night is complete when all of this is present. A calendar night is complete when at
least 8 of the 10 points are complete. Nothing is ever filled in or estimated. A night is built
only after it has ended, and building it again from the same stored data gives the same row.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config, db
from .validation import check_row

TZ = ZoneInfo("Asia/Tashkent")

MIN_DAY_HOURS = 7          # of the 10 hours 07:00-16:00
MIN_MORNING_HOURS = 6      # of the 8 hours 00:00-07:00
MIN_CLOUD_HOURS = 4        # of the 5 hours 12:00-16:00
NIGHT_HOURS = 12           # 20:00 on D to 07:00 on D+1, every one needed
MIN_COMPLETE_POINTS = 8    # of 10 points, for a complete calendar night
TREND_HOURS = 3
SETTLE = timedelta(minutes=15)   # wait after the last live window, so the last run has been saved

COLUMNS = ("location_id", "night_date", "features_cutoff_utc", "snapshot_run_id",
           "snapshot_received_at", "snapshot_trend_3h_c", "day_max_temp_c", "morning_min_temp_c",
           "afternoon_cloud_pct", "target_min_c", "n_live_hours", "n_forecast_hours",
           "provider_forecast_min_c", "is_complete", "calendar_night_complete")


def at(day: date, hour: int) -> datetime:
    """A local clock time on a day, as a UTC timestamp."""
    return datetime.combine(day, time(hour), tzinfo=TZ).astimezone(timezone.utc)


@dataclass(frozen=True)
class Windows:
    """The time windows that belong to the night of one day."""
    night_date: date
    morning: tuple[datetime, ...]     # 00:00-07:00 on D
    daytime: tuple[datetime, ...]     # 07:00-16:00 on D
    afternoon: tuple[datetime, ...]   # 12:00-16:00 on D
    snapshot_from: datetime           # 16:00 on D
    cutoff: datetime                  # 18:00 on D
    night: tuple[datetime, ...]       # 20:00 on D to 07:00 on D+1
    final_at: datetime                # 08:15 on D+1: no live night value can arrive after this


def windows(night_date: date) -> Windows:
    def hours(day, first, last):
        return tuple(at(day, h) for h in range(first, last + 1))
    night = hours(night_date, 20, 23) + hours(night_date + timedelta(days=1), 0, 7)
    return Windows(night_date=night_date, morning=hours(night_date, 0, 7),
                   daytime=hours(night_date, 7, 16), afternoon=hours(night_date, 12, 16),
                   snapshot_from=at(night_date, 16), cutoff=at(night_date, 18), night=night,
                   final_at=night[-1] + config.LIVE_WINDOW)


def last_included_night() -> date:
    """The night before the final collection time (DECISIONS.md D006, D013)."""
    return config.FINAL_COLLECTION_UTC.astimezone(TZ).date() - timedelta(days=1)


def trend_3h(item: dict, current_valid: datetime) -> float | None:
    """Temperature change over the 3 hours before the snapshot, from the snapshot response itself.

    H0 is the latest hourly valid time that is not later than the snapshot's current time.
    Trend = temperature at H0 minus temperature at H0 - 3 hours. None if either is missing.
    """
    hourly = item.get("hourly") if isinstance(item, dict) else None
    if not isinstance(hourly, dict):
        return None
    temps = dict(zip(hourly.get("time") or [], hourly.get("temperature_2m") or []))
    usable = [t for t in temps if isinstance(t, (int, float)) and t <= current_valid.timestamp()]
    if not usable:
        return None
    h0 = max(usable)
    now_temp, earlier_temp = temps[h0], temps.get(h0 - TREND_HOURS * 3600)
    if check_row({"temperature_c": now_temp}) or check_row({"temperature_c": earlier_temp}):
        return None
    return round(now_temp - earlier_temp, 2)


def summarise(w: Windows, location_id: int, live: dict, snapshot: tuple | None,
              trend: float | None, forecast: dict) -> dict:
    """Apply the rules to one point's stored values for one night. Reads no database.

    live:     {valid time: (temperature, cloud cover, time received)}, live values only
    snapshot: (run_id, time received) of the snapshot response, or None
    forecast: {target time: forecast temperature} from the snapshot response
    """
    before_cutoff = {t: v for t, v in live.items() if v[2] < w.cutoff}
    morning = [before_cutoff[h][0] for h in w.morning if h in before_cutoff]
    daytime = [before_cutoff[h][0] for h in w.daytime if h in before_cutoff]
    clouds = [before_cutoff[h][1] for h in w.afternoon
              if h in before_cutoff and before_cutoff[h][1] is not None]
    night = [live[h][0] for h in w.night if h in live]
    provider = [forecast[h] for h in w.night if h in forecast] if snapshot else []

    missing = []
    if snapshot is None:
        missing.append("no snapshot received 16:00-18:00")
    else:
        if trend is None:
            missing.append("snapshot has no 3-hour trend")
        if len(provider) < NIGHT_HOURS:
            missing.append(f"provider forecast {len(provider)} of {NIGHT_HOURS} night hours")
    if len(daytime) < MIN_DAY_HOURS:
        missing.append(f"day maximum {len(daytime)} of {len(w.daytime)} hours (need {MIN_DAY_HOURS})")
    if len(morning) < MIN_MORNING_HOURS:
        missing.append(f"morning minimum {len(morning)} of {len(w.morning)} hours (need {MIN_MORNING_HOURS})")
    if len(clouds) < MIN_CLOUD_HOURS:
        missing.append(f"afternoon cloud {len(clouds)} of {len(w.afternoon)} hours (need {MIN_CLOUD_HOURS})")
    if len(night) < NIGHT_HOURS:
        missing.append(f"night hours {len(night)} of {NIGHT_HOURS}")

    return {
        "location_id": location_id,
        "night_date": w.night_date,
        "features_cutoff_utc": w.cutoff,
        "snapshot_run_id": snapshot[0] if snapshot else None,
        "snapshot_received_at": snapshot[1] if snapshot else None,
        "snapshot_trend_3h_c": trend if snapshot else None,
        "day_max_temp_c": max(daytime) if len(daytime) >= MIN_DAY_HOURS else None,
        "morning_min_temp_c": min(morning) if len(morning) >= MIN_MORNING_HOURS else None,
        "afternoon_cloud_pct": round(sum(clouds) / len(clouds), 2) if len(clouds) >= MIN_CLOUD_HOURS else None,
        "target_min_c": min(night) if len(night) == NIGHT_HOURS else None,
        "n_live_hours": len(night),
        "n_forecast_hours": len(provider),
        "provider_forecast_min_c": min(provider) if len(provider) == NIGHT_HOURS else None,
        "is_complete": not missing,
        "missing": missing,
    }


def is_calendar_complete(rows: list[dict]) -> bool:
    return sum(r["is_complete"] for r in rows) >= MIN_COMPLETE_POINTS


def build_night(conn, night_date: date) -> list[dict]:
    """Read the stored rows for one night and return ten summaries, one per point."""
    w = windows(night_date)

    live = defaultdict(dict)
    for loc, observed_at, received_at, temp, cloud in conn.execute(
            """SELECT location_id, observed_at, response_received_at, temperature_c, cloud_cover_pct
                 FROM observations
                WHERE is_live AND NOT is_backfill AND observed_at BETWEEN %s AND %s""",
            (w.morning[0], w.night[-1])):
        live[loc][observed_at] = (temp, cloud, received_at)

    # The cut-off rule: only a response RECEIVED from 16:00 to 18:00 can be the snapshot.
    snapshots = {loc: (run_id, valid_time, received_at) for loc, run_id, valid_time, received_at in conn.execute(
        """SELECT DISTINCT ON (location_id) location_id, run_id, valid_time, response_received_at
             FROM current_conditions
            WHERE response_received_at BETWEEN %s AND %s
            ORDER BY location_id, response_received_at DESC, run_id DESC""",
        (w.snapshot_from, w.cutoff))}
    run_ids = sorted({run_id for run_id, _, _ in snapshots.values()})

    payloads = dict(conn.execute("SELECT run_id, payload FROM raw_responses WHERE run_id = ANY(%s)",
                                 (run_ids,)).fetchall())
    forecasts = defaultdict(dict)
    for loc, run_id, target_time, temp in conn.execute(
            """SELECT location_id, run_id, target_time, forecast_temp_c
                 FROM provider_forecasts
                WHERE run_id = ANY(%s) AND target_time BETWEEN %s AND %s""",
            (run_ids, w.night[0], w.night[-1])):
        if loc in snapshots and snapshots[loc][0] == run_id:   # only from this point's own snapshot
            forecasts[loc][target_time] = temp

    rows = []
    for index, (loc, *_rest) in enumerate(config.LOCATIONS):
        snapshot = snapshots.get(loc)
        trend = None
        if snapshot:
            items = payloads.get(snapshot[0])
            if isinstance(items, list) and len(items) == len(config.LOCATIONS):
                trend = trend_3h(items[index], snapshot[1])
        rows.append(summarise(w, loc, live[loc], (snapshot[0], snapshot[2]) if snapshot else None,
                              trend, forecasts[loc]))
    calendar = is_calendar_complete(rows)
    for row in rows:
        row["calendar_night_complete"] = calendar
    return rows


def store(conn, rows: list[dict]) -> None:
    """Insert or refresh the night rows. The split column is never touched here.

    A row that already has its split (set once, at the data cut-off) is frozen and not changed.
    """
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in COLUMNS[2:])
    for row in rows:
        conn.execute(
            f"""INSERT INTO nights ({", ".join(COLUMNS)}) VALUES ({", ".join(["%s"] * len(COLUMNS))})
                ON CONFLICT (location_id, night_date) DO UPDATE SET {updates}
                 WHERE nights.split IS NULL""",
            [row[c] for c in COLUMNS])
        if not row["is_complete"]:
            label = f"night {row['night_date']}: "          # logged once per point and night
            conn.execute(
                """INSERT INTO data_quality_log (location_id, rule_name, action, detail)
                   SELECT %s, 'night_incomplete', 'flagged', %s
                    WHERE NOT EXISTS (SELECT 1 FROM data_quality_log
                                       WHERE location_id = %s AND rule_name = 'night_incomplete'
                                         AND starts_with(detail, %s))""",
                (row["location_id"], label + "; ".join(row["missing"]), row["location_id"], label))
    conn.commit()


def finished_nights(now: datetime, first: date | None = None, last: date | None = None) -> list[date]:
    """Nights that have ended: the last live window is closed and the last run has been saved."""
    day = first or config.FIRST_NIGHT
    last = last or last_included_night()
    days = []
    while day <= last and now >= windows(day).final_at + SETTLE:
        days.append(day)
        day += timedelta(days=1)
    return days


def build_all(conn, now: datetime, first: date | None = None, last: date | None = None,
              save: bool = True) -> list[dict]:
    rows = []
    for day in finished_nights(now, first, last):
        night_rows = build_night(conn, day)
        if save:
            store(conn, night_rows)
        rows += night_rows
    return rows


def coverage_report(rows: list[dict]) -> str:
    """Counts only. No temperatures are printed, so test nights stay unopened (protocol C1)."""
    by_night = defaultdict(list)
    for row in rows:
        by_night[row["night_date"]].append(row)
    lines = ["night       points  calendar night  why points are incomplete",
             "----------  ------  --------------  -------------------------"]
    complete_nights = 0
    for day in sorted(by_night):
        night_rows = by_night[day]
        done = sum(r["is_complete"] for r in night_rows)
        calendar = night_rows[0]["calendar_night_complete"]
        complete_nights += calendar
        reasons = Counter(reason for r in night_rows for reason in r["missing"])
        why = "; ".join(f"{reason} ({n} points)" for reason, n in reasons.most_common())
        lines.append(f"{day}  {done:>2}/{len(night_rows)}   {'COMPLETE' if calendar else 'incomplete':<14}  {why}")
    left = (last_included_night() - max(by_night)).days if by_night else None
    lines.append("")
    lines.append(f"Complete calendar nights: {complete_nights} of {len(by_night)} finished nights "
                 f"(approved minimum 18).")
    if left is not None:
        lines.append(f"Nights not finished yet, up to {last_included_night()}: {left}. "
                     f"At most {complete_nights + left} complete nights if none of them is lost.")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="NightWatch UZ nights builder and coverage report")
    parser.add_argument("--report-only", action="store_true", help="print the report, store nothing")
    args = parser.parse_args(argv)
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is not set.", file=sys.stderr)
        return 2
    now = datetime.now(timezone.utc)
    with db.connect(dsn) as conn:
        if not args.report_only:
            db.apply_schema(conn)
        rows = build_all(conn, now, save=not args.report_only)
    print(f"Nights built at {now:%Y-%m-%d %H:%M}Z from the stored collector data.\n")
    print(coverage_report(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
