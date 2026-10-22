"""One collector run: request Open-Meteo for all ten points, validate, store.

Timing rules come from proposal v3 section 3:
- an hourly value for hour H is "live" when received in [H, H + 75 min];
- forecasts are stored with the time this collector received them;
- every run, including failed ones, is written to collection_runs.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import requests

from . import config, db
from .validation import check_row

COORD_TOLERANCE = 0.1   # degrees; the provider snaps to its model grid


@dataclass
class PointResult:
    location_id: int
    ok: bool
    observations: list[dict] = field(default_factory=list)
    current: dict | None = None
    forecasts: list[dict] = field(default_factory=list)
    issues: list[tuple[str, str, str]] = field(default_factory=list)  # (rule, action, detail)


def build_params(locations=config.LOCATIONS) -> dict:
    variables = ",".join(config.VARIABLES)
    return {
        "latitude": ",".join(f"{lat:.4f}" for _, _, lat, _ in locations),
        "longitude": ",".join(f"{lon:.4f}" for _, _, _, lon in locations),
        "hourly": variables,
        "current": variables,
        "past_hours": config.PAST_HOURS,
        "forecast_hours": config.FORECAST_HOURS,
        "timezone": "GMT",
        "timeformat": "unixtime",
        "wind_speed_unit": "kmh",
    }


def scheduled_slot(sent_at: datetime) -> datetime:
    """Latest schedule slot at or before sent_at (UTC)."""
    sent_at = sent_at.astimezone(timezone.utc)
    candidates = []
    for back in (0, 1):
        hour = (sent_at - timedelta(hours=back)).replace(minute=0, second=0, microsecond=0)
        candidates += [hour.replace(minute=m) for m in config.HOURLY_MINUTES]
        candidates += [hour.replace(minute=m) for h, m in config.EXTRA_SLOTS_UTC if hour.hour == h]
    return max(c for c in candidates if c <= sent_at)


def code_version() -> str:
    if os.environ.get("GITHUB_SHA"):
        return os.environ["GITHUB_SHA"][:12]
    try:
        return subprocess.run(["git", "rev-parse", "--short=12", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _utc(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def _values(block: dict, index: int | None = None) -> dict:
    row = {}
    for api_name, column in config.VARIABLES.items():
        value = block.get(api_name)
        row[column] = value[index] if index is not None and value is not None else value
    return row


def parse_point(item: dict, location, received_at: datetime) -> PointResult:
    """Turn one location's part of the response into rows, or reasons it was rejected."""
    location_id, name, lat, lon = location
    result = PointResult(location_id=location_id, ok=False)

    if abs(item.get("latitude", 999) - lat) > COORD_TOLERANCE or \
       abs(item.get("longitude", 999) - lon) > COORD_TOLERANCE:
        result.issues.append(("coordinate_mismatch", "rejected",
                              f"{name}: got {item.get('latitude')},{item.get('longitude')}"))
        return result
    hourly, current = item.get("hourly"), item.get("current")
    if not isinstance(hourly, dict) or not isinstance(current, dict) or "time" not in hourly:
        result.issues.append(("structure", "rejected", f"{name}: hourly or current block missing"))
        return result
    times = hourly["time"]
    for api_name in config.VARIABLES:
        if api_name not in hourly or len(hourly[api_name]) != len(times):
            result.issues.append(("structure", "rejected", f"{name}: hourly {api_name} missing or wrong length"))
            return result

    current_row = _values(current)
    problems = check_row(current_row)
    if problems or "time" not in current:
        result.issues.append(("current_values", "rejected", f"{name}: {'; '.join(problems) or 'no time'}"))
        return result
    result.current = {**current_row, "valid_time": _utc(current["time"])}

    for i, ts in enumerate(times):
        valid_time = _utc(ts)
        row = _values(hourly, i)
        problems = check_row(row)
        if problems:
            result.issues.append(("hourly_values", "rejected",
                                  f"{name} {valid_time:%Y-%m-%d %H:%M}Z: {'; '.join(problems)}"))
            continue
        if valid_time <= received_at <= valid_time + config.LIVE_WINDOW:
            result.observations.append({**row, "observed_at": valid_time})
        elif valid_time > received_at:
            result.forecasts.append({"target_time": valid_time, "forecast_temp_c": row["temperature_c"]})

    if item.get("elevation") is not None:
        result.current["elevation_m"] = item["elevation"]
    result.ok = True
    return result


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def fetch(session, params: dict, clock=_utc_now):
    """Request the API with one retry. Returns (status, text, received_at, attempts, error)."""
    error = None
    for attempt in range(1, config.MAX_ATTEMPTS + 1):
        try:
            response = session.get(config.API_URL, params=params, timeout=config.REQUEST_TIMEOUT_S)
            received_at = clock()
            if response.status_code < 500 or attempt == config.MAX_ATTEMPTS:
                return response.status_code, response.text, received_at, attempt, None
            error = f"HTTP {response.status_code}"
        except requests.RequestException as exc:
            error = f"{type(exc).__name__}: {exc}"
        if attempt < config.MAX_ATTEMPTS:
            time.sleep(config.RETRY_WAIT_S)
    return None, None, None, config.MAX_ATTEMPTS, error


def run_once(conn, trigger: str = "schedule", session=None, clock=_utc_now) -> int:
    """Do one full run and return its run_id. Never raises for provider problems.

    clock() gives the current UTC time; tests pass a fake clock.
    """
    session = session or requests.Session()
    params = build_params()
    request_url = requests.Request("GET", config.API_URL, params=params).prepare().url
    sent_at = clock()
    slot = scheduled_slot(sent_at) if trigger in ("schedule", "backup") else None
    run_id = db.start_run(conn, trigger=trigger, scheduled_for=slot, request_sent_at=sent_at,
                          delay_seconds=int((sent_at - slot).total_seconds()) if slot else None,
                          request_url=request_url, code_version=code_version())

    status, text, received_at, attempts, error = fetch(session, params, clock)
    if status != 200:
        db.finish_run(conn, run_id, http_status=status, response_received_at=None, attempts=attempts,
                      points_ok=0, points_failed=len(config.LOCATIONS),
                      error_message=error or f"HTTP {status}: {(text or '')[:300]}")
        return run_id

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        db.finish_run(conn, run_id, http_status=status, response_received_at=None, attempts=attempts,
                      points_ok=0, points_failed=len(config.LOCATIONS), error_message=f"bad JSON: {exc}")
        return run_id
    items = payload if isinstance(payload, list) else [payload]
    sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    db.save_raw(conn, run_id, sha256, payload)

    ok = failed = 0
    if len(items) != len(config.LOCATIONS):
        db.log_issue(conn, run_id, None, "structure", "rejected",
                     f"expected {len(config.LOCATIONS)} locations, got {len(items)}")
        failed = len(config.LOCATIONS)
    else:
        for item, location in zip(items, config.LOCATIONS):
            result = parse_point(item, location, received_at)
            for rule, action, detail in result.issues:
                db.log_issue(conn, run_id, result.location_id, rule, action, detail)
            if result.ok:
                db.save_point(conn, run_id, result, received_at, sha256)
                ok += 1
            else:
                failed += 1
    db.finish_run(conn, run_id, http_status=status, response_received_at=received_at,
                  attempts=attempts, points_ok=ok, points_failed=failed, error_message=None)
    return run_id
