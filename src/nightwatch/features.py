"""Feature table for the models (proposal v3 section 8; details in DECISIONS.md D015).

One row per location-night of the primary analysis set: a complete location-night
(`nights.is_complete`) inside a complete calendar night (`nights.calendar_night_complete`).

The fixed features, all from data received by 18:00 on day D:
- from the snapshot (its current-conditions block, `current_conditions` of the snapshot run):
  temperature, dew-point gap (temperature minus dew point), relative humidity, wind speed and
  cloud cover; plus the 3-hour temperature trend before the snapshot (`nights.snapshot_trend_3h_c`,
  worked out from the snapshot's raw response by the nights builder);
- from the day: daytime maximum, morning minimum and afternoon cloud cover;
- the monitored point (`location_id`).

The provider forecast is kept in the table only for the provider comparison (Q3). It is never a
model feature.

Which nights may be read (protocol C1):
- `development`: only the first 13 complete calendar nights. Later nights are never selected, so
  their outcomes are never read. This is the only mode allowed before the data cut-off.
- `final`: all complete calendar nights up to the last included night. Refused before the final
  collection time (3 Dec 2026, 08:15 Tashkent, D013).
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd

from . import config
from .nights import last_included_night

DEVELOPMENT_NIGHTS = 13        # protocol C1: the first 13 complete calendar nights

NUMERIC = [
    "snap_temp_c",            # snapshot temperature
    "snap_dewpoint_gap_c",    # snapshot temperature minus snapshot dew point
    "snap_rh_pct",            # snapshot relative humidity
    "snap_wind_kmh",          # snapshot wind speed
    "snap_cloud_pct",         # snapshot cloud cover
    "snap_trend_3h_c",        # temperature at the snapshot hour minus 3 hours earlier
    "day_max_temp_c",         # highest live temperature 07:00-16:00
    "morning_min_temp_c",     # lowest live temperature 00:00-07:00
    "afternoon_cloud_pct",    # mean live cloud cover 12:00-16:00
]
CATEGORICAL = ["location_id"]
FEATURES = CATEGORICAL + NUMERIC
TARGET = "target_min_c"
PROVIDER = "provider_forecast_min_c"   # Q3 comparison only, never a feature

QUERY = """
SELECT n.night_id, n.night_date, n.location_id, n.features_cutoff_utc,
       c.response_received_at AS snapshot_received_at,
       c.temperature_c, c.dew_point_c, c.relative_humidity_pct, c.wind_speed_kmh, c.cloud_cover_pct,
       n.snapshot_trend_3h_c, n.day_max_temp_c, n.morning_min_temp_c, n.afternoon_cloud_pct,
       n.provider_forecast_min_c, n.target_min_c
  FROM nights n
  JOIN current_conditions c ON c.location_id = n.location_id AND c.run_id = n.snapshot_run_id
 WHERE n.is_complete AND n.calendar_night_complete AND n.night_date <= %s
 ORDER BY n.night_date, n.location_id
"""
RAW_COLUMNS = ["night_id", "night_date", "location_id", "features_cutoff_utc", "snapshot_received_at",
               "temperature_c", "dew_point_c", "relative_humidity_pct", "wind_speed_kmh",
               "cloud_cover_pct", "snapshot_trend_3h_c", "day_max_temp_c", "morning_min_temp_c",
               "afternoon_cloud_pct", "provider_forecast_min_c", "target_min_c"]


def complete_calendar_nights(conn) -> list[date]:
    """Dates of the complete calendar nights, in order. Reads no temperatures."""
    return [d for (d,) in conn.execute(
        "SELECT DISTINCT night_date FROM nights WHERE calendar_night_complete ORDER BY night_date")]


def last_readable_night(conn, purpose: str, now: datetime | None = None) -> date | None:
    """The latest night whose outcome may be read for this purpose (protocol C1)."""
    now = now or datetime.now(timezone.utc)
    if purpose == "development":
        dates = complete_calendar_nights(conn)[:DEVELOPMENT_NIGHTS]
        return dates[-1] if dates else None
    if purpose == "final":
        if now < config.FINAL_COLLECTION_UTC:
            raise PermissionError("the final feature table is locked until the final collection time "
                                  f"{config.FINAL_COLLECTION_UTC:%Y-%m-%d %H:%M} UTC (D013)")
        return last_included_night()
    raise ValueError(f"unknown purpose {purpose!r}")


def build_table(raw: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Turn the joined database rows into the feature table. Reads no database.

    Returns the table and a list of reasons for location-nights left out. A location-night whose
    snapshot lacks one of the measures is left out, never filled in (proposal v3 section 3).
    Raises if a snapshot was received after the 18:00 cut-off: that would be a leak.
    """
    late = raw[raw["snapshot_received_at"] > raw["features_cutoff_utc"]]
    if len(late):
        raise ValueError(f"{len(late)} snapshot(s) received after the 18:00 cut-off: leak")
    table = pd.DataFrame({
        "night_id": raw["night_id"].astype("int64"),
        "night_date": raw["night_date"],
        "location_id": raw["location_id"].astype("int64"),
    })
    num = raw.drop(columns=["night_id", "night_date", "location_id", "features_cutoff_utc",
                            "snapshot_received_at"]).astype("float64")
    table["snap_temp_c"] = num["temperature_c"]
    table["snap_dewpoint_gap_c"] = num["temperature_c"] - num["dew_point_c"]
    table["snap_rh_pct"] = num["relative_humidity_pct"]
    table["snap_wind_kmh"] = num["wind_speed_kmh"]
    table["snap_cloud_pct"] = num["cloud_cover_pct"]
    table["snap_trend_3h_c"] = num["snapshot_trend_3h_c"]
    for column in ("day_max_temp_c", "morning_min_temp_c", "afternoon_cloud_pct", PROVIDER, TARGET):
        table[column] = num[column]

    missing = table[NUMERIC + [TARGET]].isna().any(axis=1)
    reasons = [f"night {r.night_date} point {r.location_id}: a feature value is missing, left out"
               for r in table[missing].itertuples()]
    table = table[~missing].reset_index(drop=True)
    return table, reasons


def load(conn, purpose: str = "development", now: datetime | None = None) -> tuple[pd.DataFrame, list[str]]:
    """The feature table for one purpose. In development mode later nights are never selected."""
    last = last_readable_night(conn, purpose, now)
    if last is None:
        return pd.DataFrame(columns=["night_id", "night_date"] + FEATURES + [PROVIDER, TARGET]), []
    raw = pd.DataFrame(conn.execute(QUERY, (last,)).fetchall(), columns=RAW_COLUMNS)
    return build_table(raw)


def final_split(dates: list[date], now: datetime | None = None) -> tuple[list[date], list[date]]:
    """Protocol C1 step 1: k = max(5, round(0.2 N)); the last k complete nights are test.

    Locked until the final collection time. Used once, at the cut-off.
    """
    now = now or datetime.now(timezone.utc)
    if now < config.FINAL_COLLECTION_UTC:
        raise PermissionError("the final split is made only after the final collection time (C1, D013)")
    dates = sorted(set(dates))
    k = max(5, round(len(dates) / 5))
    if len(dates) <= k:
        raise ValueError(f"{len(dates)} complete nights leave no training nights")
    return dates[:-k], dates[-k:]
