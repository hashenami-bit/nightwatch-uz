"""Tests for the dashboard data (D018). All data here is synthetic (made up for the tests)."""
from datetime import date, datetime, time, timedelta, timezone

import pytest

from nightwatch import board, features, nights
from tests.fixtures.synthetic import store_synthetic_nights, synthetic_nights
from tests.test_nights import DAY, fixed_code_version, seed_day  # noqa: F401  (fixture)

LOCAL = timezone(timedelta(hours=5))
DEV_DATES = [date(2026, 10, 29) + timedelta(days=i) for i in range(13)]     # 29 Oct - 10 Nov
LATER = [date(2026, 11, 20), date(2026, 11, 21)]


def at_local(day, hour, minute=0):
    return datetime.combine(day, time(hour, minute), tzinfo=LOCAL)


EVENING = datetime.combine(DAY, time(21, 0), tzinfo=LOCAL)     # 21:00 Tashkent on 12 Nov


# ---------- no database ----------

def test_current_night_starts_at_1810_tashkent():
    d = date(2026, 11, 27)
    assert board.current_night(at_local(d, 18, 9)) == d - timedelta(days=1)
    assert board.current_night(at_local(d, 18, 10)) == d
    assert board.current_night(at_local(d + timedelta(days=1), 6, 0)) == d       # still tonight


def test_current_night_stops_at_the_last_included_night():
    assert board.current_night(at_local(date(2026, 12, 10), 20)) == date(2026, 12, 2)


def test_night_label_says_tonight_or_ended():
    d = date(2026, 11, 27)
    assert board.night_label(d, at_local(d, 21)).startswith("Tonight")
    assert board.night_label(d, at_local(d + timedelta(days=1), 9)).startswith("Last night")


@pytest.mark.parametrize("args, problem", [
    ((11, DAY, 3.0, "thermometer"), "monitored points"),
    ((1, date(2026, 10, 1), 3.0, "thermometer"), "night must be"),
    ((1, DAY + timedelta(days=30), 3.0, "thermometer"), "night must be"),
    ((1, DAY, 75.0, "thermometer"), "between -50 and 60"),
    ((1, DAY, float("nan"), "thermometer"), "between -50 and 60"),
    ((1, DAY, "cold", "thermometer"), "must be a number"),
    ((1, DAY, 3.0, "my name is Ali"), "method"),
])
def test_field_report_checks(args, problem):
    errors = board.validate_report(*args, today=DAY + timedelta(days=1))
    assert any(problem in e for e in errors)


def test_valid_field_report_has_no_errors():
    assert board.validate_report(1, DAY, -2.5, "weather station", today=DAY) == []


def test_night_query_never_selects_the_outcome():
    assert "n.target_min_c" not in board.NIGHT_QUERY
    assert "NULL::numeric AS target_min_c" in board.NIGHT_QUERY


# ---------- database ----------

def store_dev_nights(conn, later_target=None):
    rows = synthetic_nights(DEV_DATES + LATER, seed=3)
    if later_target is not None:
        for r in rows:
            if r["night_date"] in LATER:
                r["target_min_c"] = later_target
    store_synthetic_nights(conn, rows)


def test_provisional_model_never_reads_nights_after_the_first_13(conn):
    """Changing the outcomes of nights 14 and 15 must not change the model at all (protocol C1)."""
    store_dev_nights(conn, later_target=-40.0)
    first = board.train_provisional(conn)
    conn.execute("UPDATE nights SET target_min_c = 30.0 WHERE night_date = ANY(%s)", (LATER,))
    conn.commit()
    second = board.train_provisional(conn)
    assert first.nights == second.nights == DEV_DATES
    assert (first.label, first.cv_mae, first.range_low, first.range_high) == \
           (second.label, second.cv_mae, second.range_low, second.range_high)
    assert first.range_low < first.range_high


def test_no_model_with_too_few_nights(conn):
    store_synthetic_nights(conn, synthetic_nights(DEV_DATES[:5]))
    assert board.train_provisional(conn) is None


@pytest.fixture()
def seeded(conn):
    """13 synthetic development nights, then a full synthetic collector day for 12 Nov."""
    store_dev_nights(conn)
    seed_day(conn)
    return conn


def test_predict_night_stores_provisional_predictions_once(seeded):
    model = board.train_provisional(seeded)
    first = board.predict_night(seeded, DAY, model, EVENING)
    assert len(first) == 10 and all(p["predicted_min_c"] is not None for p in first)
    assert all(p["category"] in ("Low", "Medium", "High") for p in first)
    assert all(p["range_low_c"] < p["range_high_c"] for p in first)
    again = board.predict_night(seeded, DAY, model, EVENING)
    assert [p["predicted_min_c"] for p in again] == [p["predicted_min_c"] for p in first]
    count, purposes = seeded.execute(
        "SELECT count(*), array_agg(DISTINCT purpose) FROM predictions").fetchone()
    assert count == 10 and purposes == ["provisional_dashboard"]


def test_placeholder_row_has_no_outcome_and_the_builder_replaces_it(seeded):
    board.predict_night(seeded, DAY, board.train_provisional(seeded), EVENING)
    rows = seeded.execute("""SELECT target_min_c, is_complete, calendar_night_complete, day_max_temp_c
                               FROM nights WHERE night_date = %s""", (DAY,)).fetchall()
    assert len(rows) == 10
    assert all(t is None and not c and not cal and dm is not None for t, c, cal, dm in rows)
    assert DAY not in features.complete_calendar_nights(seeded)
    assert seeded.execute("SELECT count(*) FROM data_quality_log WHERE rule_name = 'night_incomplete'"
                          ).fetchone()[0] == 0

    # After the night has ended the builder writes the real row over the placeholder.
    nights.build_all(seeded, at_local(DAY + timedelta(days=1), 9), first=DAY, last=DAY)
    rows = seeded.execute("SELECT target_min_c, is_complete FROM nights WHERE night_date = %s",
                          (DAY,)).fetchall()
    assert all(t is not None and c for t, c in rows)
    assert DAY in features.complete_calendar_nights(seeded)
    # The stored predictions still point at the same night rows.
    assert seeded.execute("SELECT count(*) FROM predictions p JOIN nights n USING (night_id) "
                          "WHERE n.night_date = %s", (DAY,)).fetchone()[0] == 10


def test_point_without_snapshot_gets_a_reason_not_a_number(conn):
    store_dev_nights(conn)
    seed_day(conn, skip=lambda t: t.hour in (16, 17))       # no run from 16:00 to 18:00
    out = board.predict_night(conn, DAY, board.train_provisional(conn), EVENING)
    assert all(p["predicted_min_c"] is None for p in out)
    assert all(p["reason"] == "no snapshot received 16:00-18:00" for p in out)
    assert conn.execute("SELECT count(*) FROM predictions").fetchone()[0] == 0


def test_field_report_round_trip(conn):
    saved = board.add_field_report(conn, 3, DAY, -1.25, "thermometer", now=at_local(DAY, 21))
    assert saved["point"] == "Bukhara" and saved["measured_low_c"] == -1.2
    listed = board.field_reports(conn)
    assert len(listed) == 1 and listed[0]["report_id"] == saved["report_id"]
    with pytest.raises(ValueError, match="between -50 and 60"):
        board.add_field_report(conn, 3, DAY, 99, "thermometer", now=at_local(DAY, 21))
    assert len(board.field_reports(conn)) == 1


def test_data_health_counts(seeded):
    health = board.data_health(seeded, at_local(DAY + timedelta(days=1), 9))
    assert health["complete_calendar_nights"] == 15            # 13 dev + 2 later synthetic nights
    assert health["needed_nights"] == 18
    assert health["last_included_night"] == date(2026, 12, 2)
    assert health["collection_closed"] is False


def test_features_only_never_reads_night_temperatures(seeded):
    """The dashboard's night summary has no night hours and no target, even with full data."""
    rows = nights.build_night(seeded, DAY, features_only=True)
    assert all(r["n_live_hours"] == 0 and r["target_min_c"] is None for r in rows)
    assert all(r["day_max_temp_c"] is not None and r["snapshot_run_id"] for r in rows)
    full = nights.build_night(seeded, DAY)
    keep = ("snapshot_run_id", "day_max_temp_c", "morning_min_temp_c", "afternoon_cloud_pct",
            "snapshot_trend_3h_c", "provider_forecast_min_c")
    assert [{k: r[k] for k in keep} for r in rows] == [{k: r[k] for k in keep} for r in full]


def test_no_placeholder_before_1810_or_outside_the_counted_nights(seeded):
    assert not board.ensure_night_rows(seeded, DAY, at_local(DAY, 18, 9))
    assert not board.ensure_night_rows(seeded, date(2026, 10, 28), at_local(DAY, 21))
    assert not board.ensure_night_rows(seeded, date(2026, 12, 3), datetime(2026, 12, 4, tzinfo=LOCAL))
    assert seeded.execute("SELECT count(*) FROM nights WHERE night_date = %s", (DAY,)).fetchone()[0] == 0
    assert board.ensure_night_rows(seeded, DAY, at_local(DAY, 18, 10))


def test_demo_mode_writes_nothing(seeded):
    """write=False: no placeholder and no stored prediction; a night with rows is still shown."""
    model = board.train_provisional(seeded)
    assert board.predict_night(seeded, DAY, model, EVENING, write=False) == []
    assert seeded.execute("SELECT count(*) FROM nights WHERE night_date = %s", (DAY,)).fetchone()[0] == 0
    nights.build_all(seeded, at_local(DAY + timedelta(days=1), 9), first=DAY, last=DAY)
    shown = board.predict_night(seeded, DAY, model, EVENING, write=False)
    assert len(shown) == 10 and all(p["predicted_min_c"] is not None for p in shown)
    assert seeded.execute("SELECT count(*) FROM predictions").fetchone()[0] == 0


def test_completeness_chart_ignores_tonights_placeholder(seeded):
    from nightwatch import charts
    board.ensure_night_rows(seeded, DAY, EVENING)
    counts = charts.completeness_table(seeded, now=EVENING)
    assert DAY not in set(counts["night_date"])


def test_newest_field_report_per_point_of_any_age(conn):
    for i in range(25):                                       # 25 reports for Tashkent
        board.add_field_report(conn, 1, DAY, float(i), "other", now=EVENING)
    board.add_field_report(conn, 2, DAY - timedelta(days=5), -3.0, "thermometer", now=EVENING)
    for i in range(25):
        board.add_field_report(conn, 1, DAY, 10.0, "other", now=EVENING)
    latest = board.latest_report_per_point(conn)
    assert latest[2]["measured_low_c"] == -3.0 and latest[1]["measured_low_c"] == 10.0


def test_health_stops_counting_slots_after_the_cut_off(conn):
    health = board.data_health(conn, datetime(2026, 12, 10, 12, tzinfo=LOCAL))
    assert health["collection_closed"] is True and health["slots_last_24h"] == 51
