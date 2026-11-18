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
