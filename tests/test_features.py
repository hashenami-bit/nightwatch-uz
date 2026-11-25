"""Tests for the feature table (DECISIONS.md D015). All data here is synthetic."""
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from nightwatch import config, features
from tests.fixtures.synthetic import store_synthetic_nights, synthetic_nights

UTC = timezone.utc
BEFORE_CUTOFF = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
AFTER_CUTOFF = config.FINAL_COLLECTION_UTC + timedelta(minutes=1)
# 12 Nov to 2 Dec without 20 and 21 Nov: 19 nights, like the real collection could end
DATES = [date(2026, 11, 12) + timedelta(days=i) for i in range(21)]
DATES = [d for d in DATES if d not in (date(2026, 11, 20), date(2026, 11, 21))]


def raw_frame(**changes):
    raw = pd.DataFrame(synthetic_nights(DATES[:2]))
    raw["night_id"] = range(1, len(raw) + 1)
    raw["features_cutoff_utc"] = pd.Timestamp("2026-11-12 13:00", tz="UTC")
    raw["snapshot_received_at"] = pd.Timestamp("2026-11-12 12:40", tz="UTC")
    for column, value in changes.items():
        raw.loc[0, column] = value
    return raw[features.RAW_COLUMNS]


# ---------- no database ----------

def test_table_has_the_fixed_features_and_the_dew_point_gap():
    table, reasons = features.build_table(raw_frame())
    assert reasons == [] and len(table) == 20
    assert features.FEATURES == ["location_id", "snap_temp_c", "snap_dewpoint_gap_c", "snap_rh_pct",
                                 "snap_wind_kmh", "snap_cloud_pct", "snap_trend_3h_c",
                                 "day_max_temp_c", "morning_min_temp_c", "afternoon_cloud_pct"]
    assert features.PROVIDER not in features.FEATURES          # never a model feature
    raw = raw_frame()
    gap = raw["temperature_c"].astype(float) - raw["dew_point_c"].astype(float)
    assert table["snap_dewpoint_gap_c"].tolist() == pytest.approx(gap.tolist())
    assert all(table[c].dtype == "float64" for c in features.NUMERIC)


def test_missing_snapshot_measure_leaves_the_row_out_and_says_why():
    table, reasons = features.build_table(raw_frame(relative_humidity_pct=None))
    assert len(table) == 19
    assert reasons == [f"night {DATES[0]} point 1: a feature value is missing, left out"]


def test_snapshot_received_after_1800_is_refused():
    """The cut-off rule, checked again on the joined rows: a late snapshot would be a leak."""
    with pytest.raises(ValueError, match="after the 18:00 cut-off"):
        features.build_table(raw_frame(snapshot_received_at=pd.Timestamp("2026-11-12 13:00:01", tz="UTC")))


def test_final_split_follows_the_twenty_percent_rule():
    nights = DATES[:13]
    train, test = features.final_split(nights, now=AFTER_CUTOFF)
    assert test == nights[-5:] and train == nights[:-5]                 # k = max(5, round(2.6)) = 5
    many = [date(2026, 1, 1) + timedelta(days=i) for i in range(28)]
    assert len(features.final_split(many, now=AFTER_CUTOFF)[1]) == 6    # round(5.6) = 6
    assert len(features.final_split(many[:27], now=AFTER_CUTOFF)[1]) == 5


def test_final_split_is_locked_before_the_cutoff():
    with pytest.raises(PermissionError):
        features.final_split(DATES, now=BEFORE_CUTOFF)


# ---------- with the test database ----------

def test_development_reads_only_the_first_13_complete_calendar_nights(conn):
    lost_night = DATES[3]                       # not a complete calendar night
    store_synthetic_nights(conn, synthetic_nights(DATES), incomplete=[(DATES[1], 4)],
                           calendar_incomplete=[lost_night])
    complete = [d for d in DATES if d != lost_night]
    assert features.complete_calendar_nights(conn) == complete

    table, reasons = features.load(conn, "development", now=BEFORE_CUTOFF)
    assert reasons == []
    assert sorted(table["night_date"].unique()) == complete[:13]
    assert table["night_date"].max() == complete[12]              # nothing later is read
    assert len(table) == 13 * 10 - 1                               # one incomplete point left out
    assert not ((table["night_date"] == DATES[1]) & (table["location_id"] == 4)).any()
    assert table[features.FEATURES + [features.TARGET]].notna().all().all()


def test_development_with_fewer_than_13_nights_uses_what_exists(conn):
    store_synthetic_nights(conn, synthetic_nights(DATES[:11]))
    table, _ = features.load(conn, "development", now=BEFORE_CUTOFF)
    assert sorted(table["night_date"].unique()) == DATES[:11]


def test_final_table_is_locked_before_the_cutoff_and_open_after(conn):
    store_synthetic_nights(conn, synthetic_nights(DATES))
    with pytest.raises(PermissionError):
        features.load(conn, "final", now=BEFORE_CUTOFF)
    table, _ = features.load(conn, "final", now=AFTER_CUTOFF)
    assert sorted(table["night_date"].unique()) == DATES          # up to 2 Dec, the last included night


def test_empty_database_gives_an_empty_table(conn):
    table, reasons = features.load(conn, "development", now=BEFORE_CUTOFF)
    assert table.empty and reasons == []
