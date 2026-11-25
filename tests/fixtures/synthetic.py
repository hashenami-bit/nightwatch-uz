"""SYNTHETIC TEST DATA ONLY.

Builds fake responses shaped like the Open-Meteo forecast API. These values are
invented for tests. They are never used in the analysis or the results.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from nightwatch import config


def make_payload(received_at: datetime, temp: float = 10.0, overrides: dict | None = None) -> list:
    """One item per monitored point, hourly from (hour - PAST_HOURS) to (hour + 23h)."""
    overrides = overrides or {}
    hour = received_at.replace(minute=0, second=0, microsecond=0)
    times = [hour + timedelta(hours=h) for h in range(-config.PAST_HOURS, config.FORECAST_HOURS)]
    items = []
    for location_id, _, lat, lon in config.LOCATIONS:
        hourly = {"time": [int(t.timestamp()) for t in times],
                  "temperature_2m": [temp - 0.1 * i for i in range(len(times))],
                  "relative_humidity_2m": [60] * len(times),
                  "dew_point_2m": [temp - 6] * len(times),
                  "wind_speed_10m": [5.0] * len(times),
                  "cloud_cover": [20] * len(times),
                  "precipitation": [0.0] * len(times),
                  "pressure_msl": [1018.0] * len(times)}
        current = {"time": int((received_at - timedelta(minutes=received_at.minute % 15)).timestamp()),
                   "interval": 900, "temperature_2m": temp, "relative_humidity_2m": 60,
                   "dew_point_2m": temp - 6, "wind_speed_10m": 5.0, "cloud_cover": 20,
                   "precipitation": 0.0, "pressure_msl": 1018.0}
        item = {"latitude": lat + 0.01, "longitude": lon - 0.01, "elevation": 400.0,
                "utc_offset_seconds": 0, "hourly": hourly, "current": current}
        for key, value in overrides.get(location_id, {}).items():
            block, var, index = key
            if index is None:
                item[block][var] = value
            else:
                item[block][var][index] = value
        items.append(item)
    return items


class FakeResponse:
    def __init__(self, status_code: int, text: str):
        self.status_code, self.text = status_code, text


class FakeSession:
    """Stands in for requests.Session; returns prepared responses in order."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        return self.responses.pop(0)


def ok_session(received_hint: datetime, **kw) -> FakeSession:
    return FakeSession(FakeResponse(200, json.dumps(make_payload(received_hint, **kw))))


def clock_at(start, step_seconds=2):
    """Fake clock: first call = start (request sent), later calls a little later."""
    state = {"t": start - timedelta(seconds=step_seconds)}

    def tick():
        state["t"] += timedelta(seconds=step_seconds)
        return state["t"]
    return tick


UTC = timezone.utc


# ---------- synthetic location-nights for the modelling tests (D015) ----------

def synthetic_nights(dates, seed: int = 0) -> list[dict]:
    """Made-up location-nights with a known pattern, for the feature, model and CV tests.

    The night minimum is the snapshot temperature minus a drop that is larger for a dry, clear
    evening, plus a fixed amount per point and some noise. So the candidates can learn something
    that persistence (P0) misses. These values are invented; they are never real data.
    """
    import numpy as np

    rng = np.random.default_rng(seed)
    point_offset = rng.normal(0, 2, size=len(config.LOCATIONS))
    point_drop = rng.normal(0, 0.8, size=len(config.LOCATIONS))
    rows = []
    for i, day in enumerate(sorted(dates)):
        for j, (location_id, *_rest) in enumerate(config.LOCATIONS):
            temp = 12 - 0.4 * i + point_offset[j] + rng.normal(0, 1.5)
            gap = rng.uniform(1, 12)
            cloud = rng.uniform(0, 100)
            wind = rng.uniform(0, 20)
            trend = rng.uniform(-4, 0)
            drop = 1 + 0.45 * gap * (1 - cloud / 100) - 0.3 * trend + point_drop[j] + rng.normal(0, 0.4)
            target = temp - drop
            rows.append({
                "night_date": day, "location_id": location_id,
                "temperature_c": round(temp, 2), "dew_point_c": round(temp - gap, 2),
                "relative_humidity_pct": round(float(np.clip(100 - 6 * gap, 5, 100)), 2),
                "wind_speed_kmh": round(wind, 2), "cloud_cover_pct": round(cloud, 2),
                "snapshot_trend_3h_c": round(trend, 2),
                "day_max_temp_c": round(temp + rng.uniform(2, 6), 2),
                "morning_min_temp_c": round(temp - rng.uniform(3, 8), 2),
                "afternoon_cloud_pct": round(float(np.clip(cloud + rng.normal(0, 10), 0, 100)), 2),
                "provider_forecast_min_c": round(target + rng.normal(0, 1), 2),
                "target_min_c": round(target, 2),
            })
    return rows


def store_synthetic_nights(conn, rows: list[dict], incomplete=(), calendar_incomplete=()) -> None:
    """Write synthetic rows to the TEST database: one fake run, snapshot and night row each.

    incomplete:          (night_date, location_id) pairs stored as incomplete location-nights
    calendar_incomplete: night dates whose calendar night is stored as not complete
    """
    from nightwatch.nights import windows

    for row in rows:
        w = windows(row["night_date"])
        received = w.cutoff - timedelta(minutes=20)              # 17:40 Tashkent
        run_id = conn.execute(
            """INSERT INTO collection_runs (trigger, request_sent_at, response_received_at,
                                            request_url, code_version, points_ok)
               VALUES ('test', %s, %s, 'synthetic', 'synthetic000', 10) RETURNING run_id""",
            (received - timedelta(seconds=2), received)).fetchone()[0]
        conn.execute(
            """INSERT INTO current_conditions (location_id, run_id, valid_time, response_received_at,
                   temperature_c, relative_humidity_pct, dew_point_c, wind_speed_kmh, cloud_cover_pct)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (row["location_id"], run_id, received - timedelta(minutes=10), received,
             row["temperature_c"], row["relative_humidity_pct"], row["dew_point_c"],
             row["wind_speed_kmh"], row["cloud_cover_pct"]))
        complete = (row["night_date"], row["location_id"]) not in set(incomplete)
        conn.execute(
            """INSERT INTO nights (location_id, night_date, features_cutoff_utc, snapshot_run_id,
                   snapshot_received_at, snapshot_trend_3h_c, day_max_temp_c, morning_min_temp_c,
                   afternoon_cloud_pct, target_min_c, n_live_hours, n_forecast_hours,
                   provider_forecast_min_c, is_complete, calendar_night_complete)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 12, %s, %s, %s)""",
            (row["location_id"], row["night_date"], w.cutoff, run_id, received,
             row["snapshot_trend_3h_c"], row["day_max_temp_c"], row["morning_min_temp_c"],
             row["afternoon_cloud_pct"], row["target_min_c"] if complete else None,
             12 if complete else 11, row["provider_forecast_min_c"], complete,
             row["night_date"] not in set(calendar_incomplete)))
    conn.commit()


def synthetic_table(dates, seed: int = 0):
    """The feature table for synthetic nights, built without a database."""
    import pandas as pd

    from nightwatch import features

    raw = pd.DataFrame(synthetic_nights(dates, seed))
    raw["night_id"] = range(1, len(raw) + 1)
    raw["features_cutoff_utc"] = pd.Timestamp("2026-11-01 13:00", tz="UTC")
    raw["snapshot_received_at"] = raw["features_cutoff_utc"] - pd.Timedelta(minutes=20)
    table, _ = features.build_table(raw[features.RAW_COLUMNS])
    return table
