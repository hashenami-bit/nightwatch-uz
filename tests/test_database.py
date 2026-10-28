"""Integration tests against a throw-away PostgreSQL database. All data is synthetic.

Uses TEST_DATABASE_URL (default: local database nightwatch_test). The database is
dropped and recreated at the start, so never point this at real data.
"""
import json
import os
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from nightwatch import collector, config, db
from tests.fixtures.synthetic import FakeResponse, FakeSession, make_payload

UTC = timezone.utc


def clock_at(start, step_seconds=2):
    """Fake clock: first call = start (request sent), later calls a little later."""
    state = {"t": start - timedelta(seconds=step_seconds)}

    def tick():
        state["t"] += timedelta(seconds=step_seconds)
        return state["t"]
    return tick


TEST_DSN = os.environ.get("TEST_DATABASE_URL", "postgresql:///nightwatch_test")


@pytest.fixture()
def conn():
    name = psycopg.conninfo.conninfo_to_dict(TEST_DSN).get("dbname", "")
    assert "test" in name, "refusing to reset a database whose name does not contain 'test'"
    admin = TEST_DSN.rsplit("/", 1)[0] + "/postgres"
    try:
        with psycopg.connect(admin, autocommit=True) as a:
            a.execute(f'DROP DATABASE IF EXISTS "{name}"')
            a.execute(f'CREATE DATABASE "{name}"')
    except psycopg.OperationalError as exc:
        pytest.skip(f"PostgreSQL not available: {exc}")
    with db.connect(TEST_DSN) as c:
        db.apply_schema(c)
        yield c


def count(conn, table):
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_successful_run_stores_run_raw_observations_and_forecasts(conn):
    sent = datetime(2026, 11, 2, 10, 7, tzinfo=UTC)
    session = FakeSession(FakeResponse(200, json.dumps(make_payload(sent))))
    run_id = collector.run_once(conn, trigger="test", session=session, clock=clock_at(sent))

    ok, failed, status = conn.execute(
        "SELECT points_ok, points_failed, http_status FROM collection_runs WHERE run_id = %s", (run_id,)).fetchone()
    assert (ok, failed, status) == (10, 0, 200)
    assert count(conn, "raw_responses") == 1
    assert count(conn, "current_conditions") == 10
    assert count(conn, "observations") > 0
    assert count(conn, "provider_forecasts") > 0


def test_second_run_does_not_duplicate_live_hours(conn):
    first = datetime(2026, 11, 2, 10, 7, tzinfo=UTC)
    collector.run_once(conn, trigger="test", session=FakeSession(FakeResponse(200, json.dumps(make_payload(first)))), clock=clock_at(first))
    before = count(conn, "observations")
    second = datetime(2026, 11, 2, 10, 37, tzinfo=UTC)
    collector.run_once(conn, trigger="test", session=FakeSession(FakeResponse(200, json.dumps(make_payload(second)))), clock=clock_at(second))
    assert count(conn, "observations") == before            # hour 10:00 already stored; earliest wins
    assert count(conn, "collection_runs") == 2


def test_failed_request_is_still_logged(conn, monkeypatch):
    monkeypatch.setattr(collector.config, "RETRY_WAIT_S", 0)
    sent = datetime(2026, 11, 2, 10, 7, tzinfo=UTC)
    session = FakeSession(FakeResponse(503, "busy"), FakeResponse(503, "busy"))
    run_id = collector.run_once(conn, trigger="test", session=session, clock=clock_at(sent))
    row = conn.execute("SELECT http_status, attempts, points_failed, response_received_at, error_message "
                       "FROM collection_runs WHERE run_id = %s", (run_id,)).fetchone()
    assert row[:4] == (503, 2, 10, None)
    assert "503" in row[4]
    assert count(conn, "observations") == 0


def test_invalid_value_goes_to_quality_log_not_observations(conn):
    sent = datetime(2026, 11, 2, 10, 7, tzinfo=UTC)
    bad = {2: {("hourly", "temperature_2m", config.PAST_HOURS): -80.0}}   # hour 10:00 for Samarkand, impossible
    collector.run_once(conn, trigger="test", session=FakeSession(FakeResponse(200, json.dumps(make_payload(sent, overrides=bad)))), clock=clock_at(sent))
    issues = conn.execute("SELECT location_id, rule_name, action FROM data_quality_log").fetchall()
    assert (2, "hourly_values", "rejected") in issues
    stored = conn.execute("SELECT count(*) FROM observations WHERE location_id = 2 AND observed_at = %s",
                          (datetime(2026, 11, 2, 10, 0, tzinfo=UTC),)).fetchone()[0]
    assert stored == 0


def test_database_refuses_a_live_value_outside_the_window(conn):
    run_id = db.start_run(conn, trigger="test", scheduled_for=None,
                          request_sent_at=datetime(2026, 11, 2, 12, 0, tzinfo=UTC), delay_seconds=None,
                          request_url="synthetic", code_version="test")
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("""INSERT INTO observations (location_id, run_id, observed_at, response_received_at,
                                                  is_live, temperature_c, payload_sha256)
                        VALUES (1, %s, '2026-11-02 09:00+00', '2026-11-02 12:00+00', true, 5, repeat('0', 64))""",
                     (run_id,))
    conn.rollback()


def test_schema_can_be_applied_again_without_changes(conn):
    db.apply_schema(conn)              # the collector applies it at the start of every real run
    assert count(conn, "locations") == 10
