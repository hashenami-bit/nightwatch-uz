"""Database access for the collector. All SQL is parameterised."""
from __future__ import annotations

from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

SCHEMA_FILE = Path(__file__).resolve().parents[2] / "db" / "schema.sql"
MEASURES = ("temperature_c", "relative_humidity_pct", "dew_point_c", "wind_speed_kmh",
            "cloud_cover_pct", "precipitation_mm", "pressure_hpa")


def connect(dsn: str) -> psycopg.Connection:
    return psycopg.connect(dsn)


def apply_schema(conn) -> None:
    conn.execute(SCHEMA_FILE.read_text())
    conn.commit()


def start_run(conn, *, trigger, scheduled_for, request_sent_at, delay_seconds, request_url,
              code_version) -> int:
    """Write the run row first and commit, so even a crash later leaves a trace."""
    run_id = conn.execute(
        """INSERT INTO collection_runs (trigger, scheduled_for, request_sent_at, delay_seconds,
                                        request_url, code_version)
           VALUES (%s, %s, %s, %s, %s, %s) RETURNING run_id""",
        (trigger, scheduled_for, request_sent_at, delay_seconds, request_url, code_version),
    ).fetchone()[0]
    conn.commit()
    return run_id


def finish_run(conn, run_id, *, http_status, response_received_at, attempts, points_ok,
               points_failed, error_message) -> None:
    conn.execute(
        """UPDATE collection_runs
              SET http_status = %s, response_received_at = %s, attempts = %s, points_ok = %s,
                  points_failed = %s, error_message = %s, finished_at = now()
            WHERE run_id = %s""",
        (http_status, response_received_at, attempts, points_ok, points_failed, error_message, run_id),
    )
    conn.commit()


def save_raw(conn, run_id, sha256, payload) -> None:
    conn.execute("INSERT INTO raw_responses (run_id, payload_sha256, payload) VALUES (%s, %s, %s)",
                 (run_id, sha256, Jsonb(payload)))


def log_issue(conn, run_id, location_id, rule_name, action, detail) -> None:
    conn.execute(
        """INSERT INTO data_quality_log (run_id, location_id, rule_name, action, detail)
           VALUES (%s, %s, %s, %s, %s)""",
        (run_id, location_id, rule_name, action, detail),
    )


def save_point(conn, run_id, result, received_at, sha256) -> dict:
    """Store one point's rows. Returns counts of new rows (duplicates are skipped)."""
    cols = ", ".join(MEASURES)
    marks = ", ".join(["%s"] * len(MEASURES))
    counts = {"observations": 0, "forecasts": 0, "duplicates": 0}

    if result.current.get("elevation_m") is not None:
        conn.execute("UPDATE locations SET elevation_m = %s WHERE location_id = %s AND elevation_m IS NULL",
                     (result.current["elevation_m"], result.location_id))
    conn.execute(
        f"""INSERT INTO current_conditions (location_id, run_id, valid_time, response_received_at, {cols})
            VALUES (%s, %s, %s, %s, {marks})""",
        (result.location_id, run_id, result.current["valid_time"], received_at,
         *[result.current[m] for m in MEASURES]),
    )
    for obs in result.observations:
        cur = conn.execute(
            f"""INSERT INTO observations (location_id, run_id, observed_at, response_received_at,
                                          is_live, payload_sha256, {cols})
                VALUES (%s, %s, %s, %s, true, %s, {marks})
                ON CONFLICT (location_id, observed_at) DO NOTHING""",
            (result.location_id, run_id, obs["observed_at"], received_at, sha256,
             *[obs[m] for m in MEASURES]),
        )
        if cur.rowcount:
            counts["observations"] += 1
        else:
            counts["duplicates"] += 1
    for fc in result.forecasts:
        cur = conn.execute(
            """INSERT INTO provider_forecasts (location_id, run_id, retrieved_at, target_time,
                                               forecast_temp_c)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (location_id, run_id, target_time) DO NOTHING""",
            (result.location_id, run_id, received_at, fc["target_time"], fc["forecast_temp_c"]),
        )
        counts["forecasts"] += cur.rowcount
    return counts
