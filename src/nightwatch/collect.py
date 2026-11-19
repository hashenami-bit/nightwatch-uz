"""Command line entry point: `python -m nightwatch.collect`.

Real collection is switched OFF until the Module Leader's written approval.
It only runs when the environment variable COLLECTION_ENABLED is exactly "true".
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import requests

from . import collector, config


def collection_closed(now: datetime) -> bool:
    """True after the fixed final collection time; nothing is stored after it."""
    return now > config.FINAL_COLLECTION_UTC


def dry_run() -> int:
    """Fetch once and validate, print counts only. Writes nothing anywhere."""
    status, text, received_at, attempts, error = collector.fetch(requests.Session(), collector.build_params())
    if status != 200:
        print(f"FAILED: status={status} error={error}")
        return 1
    items = json.loads(text)
    items = items if isinstance(items, list) else [items]
    print(f"received {received_at:%Y-%m-%d %H:%M:%S}Z, {len(items)} locations, attempts={attempts}")
    for item, location in zip(items, config.LOCATIONS):
        r = collector.parse_point(item, location, received_at)
        print(f"  {location[1]:<10} ok={r.ok} live_hours={len(r.observations)} "
              f"forecast_hours={len(r.forecasts)} issues={len(r.issues)}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="NightWatch UZ collector")
    parser.add_argument("--trigger", default="schedule", choices=["schedule", "backup", "manual"])
    parser.add_argument("--dry-run", action="store_true", help="fetch and validate only, store nothing")
    args = parser.parse_args(argv)

    if args.dry_run:
        return dry_run()
    if os.environ.get("COLLECTION_ENABLED") != "true":
        print("Collection is switched off (COLLECTION_ENABLED is not 'true').")
        return 0
    if collection_closed(datetime.now(timezone.utc)):
        print(f"Collection closed at the data cut-off {config.FINAL_COLLECTION_UTC:%Y-%m-%d %H:%M}Z (D013).")
        return 0
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is not set.", file=sys.stderr)
        return 2

    from . import db
    with db.connect(dsn) as conn:
        db.apply_schema(conn)   # safe to repeat: creates the tables on the first real run only
        run_id = collector.run_once(conn, trigger=args.trigger)
        ok, failed, status, attempts, error = conn.execute(
            "SELECT points_ok, points_failed, http_status, attempts, error_message "
            "FROM collection_runs WHERE run_id = %s", (run_id,)).fetchone()
    print(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}Z run {run_id}: "
          f"status={status} points_ok={ok} points_failed={failed} attempts={attempts}")
    if error:
        print(f"  error: {error[:300]}")   # same text as collection_runs.error_message
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
