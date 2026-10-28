"""Unit tests that need no database. All data here is synthetic."""
from datetime import datetime, timedelta, timezone

from nightwatch import collect, collector, config
from nightwatch.validation import check_row
from tests.fixtures.synthetic import make_payload

UTC = timezone.utc


def test_request_covers_all_ten_points_and_variables():
    params = collector.build_params()
    assert len(params["latitude"].split(",")) == 10
    assert len(params["longitude"].split(",")) == 10
    for name in config.VARIABLES:
        assert name in params["hourly"] and name in params["current"]
    assert params["timezone"] == "GMT" and params["timeformat"] == "unixtime"


def test_schedule_slot_matches_v3_schedule():
    at = lambda h, m: datetime(2026, 11, 2, h, m, tzinfo=UTC)
    assert collector.scheduled_slot(at(9, 12)) == at(9, 7)
    assert collector.scheduled_slot(at(9, 59)) == at(9, 37)
    assert collector.scheduled_slot(at(12, 31)) == at(12, 25)      # 17:25 local extra run
    assert collector.scheduled_slot(at(12, 52)) == at(12, 40)      # 17:40 local extra run
    assert collector.scheduled_slot(at(0, 3)) == datetime(2026, 11, 1, 23, 37, tzinfo=UTC)


def test_only_hours_inside_75_minute_window_are_live():
    received = datetime(2026, 11, 2, 10, 7, tzinfo=UTC)
    item = make_payload(received)[0]
    result = collector.parse_point(item, config.LOCATIONS[0], received)
    live_hours = sorted(o["observed_at"] for o in result.observations)
    assert result.ok
    assert live_hours == [datetime(2026, 11, 2, 9, 0, tzinfo=UTC), datetime(2026, 11, 2, 10, 0, tzinfo=UTC)]
    assert all(f["target_time"] > received for f in result.forecasts)
    assert len(result.forecasts) == config.FORECAST_HOURS - 1


def test_out_of_range_hourly_value_is_rejected_and_logged():
    received = datetime(2026, 11, 2, 10, 7, tzinfo=UTC)
    bad = {1: {("hourly", "temperature_2m", 3): 99.0}}       # 99 °C is impossible
    result = collector.parse_point(make_payload(received, overrides=bad)[0], config.LOCATIONS[0], received)
    assert result.ok
    assert any(rule == "hourly_values" and "outside" in detail for rule, _, detail in result.issues)


def test_wrong_coordinates_reject_the_point():
    received = datetime(2026, 11, 2, 10, 7, tzinfo=UTC)
    item = make_payload(received)[0]
    item["latitude"] += 5
    result = collector.parse_point(item, config.LOCATIONS[0], received)
    assert not result.ok and result.issues[0][0] == "coordinate_mismatch"


def test_check_row_catches_dew_point_above_temperature():
    assert check_row({"temperature_c": 5.0, "dew_point_c": 7.0})
    assert check_row({"temperature_c": 5.0, "dew_point_c": 4.0}) == []
    assert check_row({"temperature_c": None}) == ["temperature_c missing"]


def test_collection_stops_after_the_fixed_cut_off():
    assert not collect.collection_closed(datetime(2026, 12, 1, 3, 15, tzinfo=UTC))   # 08:15 Tashkent: last moment
    assert collect.collection_closed(datetime(2026, 12, 1, 3, 16, tzinfo=UTC))
