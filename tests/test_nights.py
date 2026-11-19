"""Tests for the nights builder. All data here is synthetic (made up for the tests).

The first part needs no database. The second part runs the real collector with fake
responses for a whole day into the throw-away test database, then builds the night.
"""
import json
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import pytest

from nightwatch import collector, config, nights
from tests.fixtures.synthetic import FakeResponse, FakeSession, clock_at, make_payload

UTC = timezone.utc
LOCAL = timezone(timedelta(hours=5))          # Asia/Tashkent, no summer time
DAY = date(2026, 11, 12)
W = nights.windows(DAY)
AFTER_THE_NIGHT = datetime(2026, 11, 13, 9, 0, tzinfo=LOCAL)


def local(hour, minute=0, second=0, day=DAY):
    return datetime.combine(day, time(hour, minute, second), tzinfo=LOCAL)


# ---------- no database ----------

def full_live(temp=5.0, cloud=20.0):
    """Every hour from 00:00 on the day to 07:00 the next day, received 7 minutes later."""
    hours = [local(0) + timedelta(hours=h) for h in range(32)]
    return {h: (temp, cloud, h + timedelta(minutes=7)) for h in hours}


def full_forecast(temp=4.0):
    return {h: temp for h in W.night}


SNAPSHOT = (1, local(17, 40))


def test_windows_follow_the_proposal():
    assert len(W.night) == 12 and len(W.morning) == 8 and len(W.daytime) == 10 and len(W.afternoon) == 5
    assert W.night[0] == datetime(2026, 11, 12, 15, 0, tzinfo=UTC)      # 20:00 Tashkent
    assert W.night[-1] == datetime(2026, 11, 13, 2, 0, tzinfo=UTC)      # 07:00 next day
    assert W.cutoff == datetime(2026, 11, 12, 13, 0, tzinfo=UTC)        # 18:00 Tashkent
    assert W.final_at == datetime(2026, 11, 13, 3, 15, tzinfo=UTC)      # 08:15 next day
    assert nights.last_included_night() == date(2026, 12, 2)            # D013


def test_complete_night_from_full_data():
    live = full_live()
    live[local(4, day=DAY + timedelta(days=1))] = (-1.5, 20.0, local(4, 7, day=DAY + timedelta(days=1)))
    row = nights.summarise(W, 1, live, SNAPSHOT, -0.3, full_forecast())
    assert row["is_complete"] and row["missing"] == []
    assert row["target_min_c"] == -1.5 and row["n_live_hours"] == 12
    assert row["provider_forecast_min_c"] == 4.0 and row["n_forecast_hours"] == 12


def test_eleven_night_hours_give_no_target():
    live = full_live()
    del live[local(23)]
    row = nights.summarise(W, 1, live, SNAPSHOT, -0.3, full_forecast())
    assert row["n_live_hours"] == 11 and row["target_min_c"] is None and not row["is_complete"]
    assert row["missing"] == ["night hours 11 of 12"]


def test_day_maximum_needs_seven_of_ten_hours():
    live = full_live()
    for hour in (8, 9, 10):
        del live[local(hour)]
    assert nights.summarise(W, 1, live, SNAPSHOT, -0.3, full_forecast())["day_max_temp_c"] == 5.0   # 7 hours left
    del live[local(11)]
    row = nights.summarise(W, 1, live, SNAPSHOT, -0.3, full_forecast())                              # 6 hours left
    assert row["day_max_temp_c"] is None and not row["is_complete"]


def test_value_received_after_1800_is_not_a_day_feature():
    """Cut-off rule: a hot 16:00 value that arrived after 18:00 must not reach the features."""
    live = full_live()
    live[local(16)] = (30.0, 90.0, local(18, 5))
    row = nights.summarise(W, 1, live, SNAPSHOT, -0.3, full_forecast())
    assert row["day_max_temp_c"] == 5.0 and row["afternoon_cloud_pct"] == 20.0


def test_no_snapshot_means_incomplete_and_no_forecast():
    row = nights.summarise(W, 1, full_live(), None, None, full_forecast())
    assert not row["is_complete"] and row["missing"] == ["no snapshot received 16:00-18:00"]
    assert row["n_forecast_hours"] == 0 and row["provider_forecast_min_c"] is None


def test_calendar_night_needs_eight_of_ten_points():
    def rows(n_complete):
        return [{"is_complete": i < n_complete} for i in range(10)]
    assert nights.is_calendar_complete(rows(8))
    assert not nights.is_calendar_complete(rows(7))


def test_trend_uses_only_hours_up_to_the_snapshot_time():
    hours = [int(local(h).timestamp()) for h in range(13, 20)]             # 13:00 ... 19:00
    item = {"hourly": {"time": hours, "temperature_2m": [15.0, 14.0, 13.0, 12.0, 11.0, 99.0, 99.0]}}
    assert nights.trend_3h(item, local(17, 30)) == -3.0                    # 17:00 minus 14:00
    item["hourly"]["temperature_2m"][1] = None                             # 14:00 missing
    assert nights.trend_3h(item, local(17, 30)) is None
    assert nights.trend_3h({}, local(17, 30)) is None


def test_night_is_built_only_after_it_has_ended():
    next_day = DAY + timedelta(days=1)
    assert nights.finished_nights(local(8, 20, day=next_day), first=DAY, last=DAY) == []
    assert nights.finished_nights(local(8, 30, day=next_day), first=DAY, last=DAY) == [DAY]
    assert nights.finished_nights(local(8, 30, day=next_day), first=DAY, last=DAY + timedelta(days=5)) == [DAY]


# ---------- with the test database ----------

# The made-up live temperature for each local hour: the day, then the next morning.
DAY_CURVE = [4.0, 3.5, 3.0, 2.5, 2.0, 1.5, 1.0, 2.0, 4.0, 6.0, 8.0, 10.0,
             12.0, 13.0, 14.0, 13.5, 13.0, 12.0, 10.0, 9.0, 8.0, 7.0, 6.0, 5.0]
NEXT_MORNING = [4.0, 3.0, 2.0, 1.0, 0.5, 0.0, -0.5, 1.0, 3.0]


def curve(t):
    return DAY_CURVE[t.hour] if t.date() == DAY else NEXT_MORNING[t.hour]


def run_times():
    """The timer slots from 00:07 on the day to 08:37 the next day, plus one extra run at 17:40."""
    times = [local(0) + timedelta(hours=h, minutes=m) for h in range(33) for m in (7, 37)]
    return sorted(times + [local(17, 40)])


def seed_day(conn, skip=lambda t: False, overrides=lambda t: None, extra=()):
    """Run the real collector once per slot. The live value of hour H becomes curve(H)."""
    for t in sorted([*run_times(), *extra]):
        if skip(t):
            continue
        # make_payload gives the run's own hour the value temp - 0.1 * PAST_HOURS
        payload = make_payload(t.astimezone(UTC), temp=curve(t) + 0.1 * config.PAST_HOURS,
                               overrides=overrides(t))
        collector.run_once(conn, trigger="test", clock=clock_at(t.astimezone(UTC)),
                           session=FakeSession(FakeResponse(200, json.dumps(payload))))


@pytest.fixture(autouse=True)
def fixed_code_version(monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", "synthetic000")     # no git call for every fake run


def stored(conn, columns="*"):
    cur = conn.execute(f"SELECT {columns} FROM nights ORDER BY location_id")
    names = [c.name for c in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def incomplete_log(conn):
    return conn.execute("SELECT location_id, detail FROM data_quality_log "
                        "WHERE rule_name = 'night_incomplete' ORDER BY location_id").fetchall()


def test_full_day_gives_ten_complete_points(conn):
    seed_day(conn)
    built = nights.build_all(conn, AFTER_THE_NIGHT, first=DAY, last=DAY)
    rows = stored(conn)
    assert len(built) == len(rows) == 10
    for row in rows:
        assert row["night_date"] == DAY and row["is_complete"] and row["calendar_night_complete"]
        assert row["n_live_hours"] == 12 and row["n_forecast_hours"] == 12
        assert row["target_min_c"] == Decimal("-0.50")            # 06:00 the next morning
        assert row["day_max_temp_c"] == Decimal("14.00")          # 14:00
        assert row["morning_min_temp_c"] == Decimal("1.00")       # 06:00 on the day
        assert row["afternoon_cloud_pct"] == Decimal("20.00")
        assert row["snapshot_received_at"] == local(17, 40, 2)    # the latest one before 18:00
        assert row["snapshot_trend_3h_c"] == Decimal("-0.30")
        assert row["provider_forecast_min_c"] == Decimal("10.60")   # 12.4 - 0.1 * 18 in that response
        assert row["features_cutoff_utc"] == local(18)
        assert row["split"] is None                               # set once, at the data cut-off
    assert incomplete_log(conn) == []
    assert "COMPLETE" in nights.coverage_report(built)


def test_response_received_after_1800_is_never_the_snapshot(conn):
    """The cut-off rule on stored data: 16:37 is used, a response from 18:00:12 is not."""
    def skip(t):
        return local(16) <= t <= local(18) and t != local(16, 37)
    seed_day(conn, skip=skip, extra=[local(18, 0, 10)])
    nights.build_all(conn, AFTER_THE_NIGHT, first=DAY, last=DAY)
    for row in stored(conn):
        assert row["snapshot_received_at"] == local(16, 37, 2)
        assert row["snapshot_received_at"] <= row["features_cutoff_utc"]
        assert row["is_complete"]


def test_missing_night_hour_is_logged_and_rebuilding_changes_nothing(conn):
    lost = {local(2, 7, day=DAY + timedelta(days=1)), local(2, 37, day=DAY + timedelta(days=1)),
            local(3, 7, day=DAY + timedelta(days=1))}             # every run that can catch 02:00
    seed_day(conn, skip=lambda t: t in lost)
    nights.build_all(conn, AFTER_THE_NIGHT, first=DAY, last=DAY)
    first_build = stored(conn)
    for row in first_build:
        assert row["n_live_hours"] == 11 and row["target_min_c"] is None
        assert not row["is_complete"] and not row["calendar_night_complete"]
    log = incomplete_log(conn)
    assert len(log) == 10 and log[0][1] == "night 2026-11-12: night hours 11 of 12"

    nights.build_all(conn, AFTER_THE_NIGHT, first=DAY, last=DAY)   # build again
    assert stored(conn) == first_build
    assert incomplete_log(conn) == log


def test_two_incomplete_points_still_make_a_complete_calendar_night(conn):
    lost = {local(2, 7, day=DAY + timedelta(days=1)), local(2, 37, day=DAY + timedelta(days=1)),
            local(3, 7, day=DAY + timedelta(days=1))}
    broken = {3: {("current", "temperature_2m", None): 99.0},     # impossible value: point rejected
              7: {("current", "temperature_2m", None): 99.0}}
    seed_day(conn, overrides=lambda t: broken if t in lost else None)
    nights.build_all(conn, AFTER_THE_NIGHT, first=DAY, last=DAY)
    rows = stored(conn)
    assert [r["location_id"] for r in rows if not r["is_complete"]] == [3, 7]
    assert all(r["calendar_night_complete"] for r in rows)        # 8 of 10 is enough


def test_report_only_stores_nothing(conn):
    seed_day(conn, skip=lambda t: t > local(1))                    # a few runs are enough here
    built = nights.build_all(conn, AFTER_THE_NIGHT, first=DAY, last=DAY, save=False)
    assert len(built) == 10 and stored(conn) == []
