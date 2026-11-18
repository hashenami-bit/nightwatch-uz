"""Tests for the missing-run check. All data here is synthetic (made up for the tests)."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from nightwatch import collector, config, slots
from tests.fixtures.synthetic import FakeResponse, FakeSession, clock_at, make_payload

UTC = timezone.utc
START = slots.first_slot_time()                     # 28 Oct 2026 19:00 UTC = 29 Oct 00:00 Tashkent


def utc(day, hour, minute):
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


# ---------- no database ----------

def test_a_full_day_has_51_slots():
    day = slots.expected_slots(utc(29, 0, 0), utc(29, 23, 59))
    assert len(day) == 51 and day == sorted(day)
    assert day[0] == utc(29, 0, 7) and day[-1] == utc(29, 23, 37)
    assert [s.minute for s in day if s.hour == 12] == [7, 10, 25, 37, 40]     # 17:10, 17:25, 17:40 Tashkent


def test_slots_respect_both_ends():
    assert slots.expected_slots(utc(29, 0, 7), utc(29, 0, 37)) == [utc(29, 0, 7), utc(29, 0, 37)]
    assert slots.expected_slots(utc(29, 0, 8), utc(29, 0, 36)) == []


def test_counting_starts_at_midnight_tashkent_on_the_first_counted_day():
    assert START == utc(28, 19, 0)


def test_three_groups():
    a, b, c = utc(29, 0, 7), utc(29, 0, 37), utc(29, 1, 7)
    result = slots.classify([a, b, c], {a: (1, 1), b: (3, 0)})
    assert result == [(a, slots.DATA), (b, slots.NO_DATA), (c, slots.NO_RUN)]
    assert slots.classify([a], {a: (3, 1)}) == [(a, slots.DATA)]              # one good retry is enough


# ---------- with the test database ----------

@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", "synthetic000")
    monkeypatch.setattr(collector.config, "RETRY_WAIT_S", 0)


def run(conn, sent, ok=True, trigger="backup"):
    good = FakeResponse(200, json.dumps(make_payload(sent)))
    session = FakeSession(good) if ok else FakeSession(*[FakeResponse(503, "busy")] * config.MAX_ATTEMPTS)
    collector.run_once(conn, trigger=trigger, session=session, clock=clock_at(sent))


def missing_log(conn):
    return [d for (d,) in conn.execute(
        "SELECT detail FROM data_quality_log WHERE rule_name = 'missing_run' ORDER BY detail")]


def test_missing_and_failed_slots_are_found_and_logged_once(conn):
    s1, s2, s3, s4 = (START + timedelta(minutes=m) for m in (7, 37, 67, 97))   # 19:07 ... 20:37 UTC
    run(conn, s1 + timedelta(seconds=3))                       # data
    run(conn, s2 + timedelta(seconds=3), ok=False)             # the first try fails ...
    run(conn, s2 + timedelta(seconds=40))                      # ... the retry on a fresh machine works
    run(conn, s3 + timedelta(seconds=3), ok=False)             # run, but no data
    run(conn, s4 + timedelta(minutes=5), trigger="manual")     # a manual run never fills a slot
    now = s4 + slots.GRACE

    result = slots.check(conn, now)
    assert result == [(s1, slots.DATA), (s2, slots.DATA), (s3, slots.NO_DATA), (s4, slots.NO_RUN)]
    assert missing_log(conn) == ["slot 2026-10-28 20:37Z: no run started"]

    slots.check(conn, now)                                      # check again: nothing is logged twice
    assert len(missing_log(conn)) == 1
    assert "No run (missing): 1" in slots.slot_report(result)


def test_a_fresh_slot_is_not_judged_yet_and_report_only_stores_nothing(conn):
    s1, s2 = START + timedelta(minutes=7), START + timedelta(minutes=37)
    result = slots.check(conn, s2 + slots.GRACE - timedelta(minutes=1), save=False)
    assert result == [(s1, slots.NO_RUN)]                       # s2 is younger than the waiting time
    assert missing_log(conn) == []


def test_no_slot_is_expected_after_the_final_collection_time(conn):
    result = slots.check(conn, config.FINAL_COLLECTION_UTC + timedelta(days=3), save=False)
    assert result[-1][0] == datetime(2026, 12, 1, 3, 7, tzinfo=UTC)          # 08:07 Tashkent, 1 Dec
