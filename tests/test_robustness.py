"""Tests for the robustness checks (DECISIONS.md D016). All data here is synthetic."""
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from nightwatch import alerts, models, robustness
from nightwatch.features import NUMERIC
from tests.fixtures.synthetic import synthetic_table

DATES = [date(2026, 11, 12) + timedelta(days=i) for i in range(18)]
TABLE = synthetic_table(DATES, seed=5)
TRAIN = TABLE[TABLE["night_date"] < DATES[13]]
TEST = TABLE[TABLE["night_date"] >= DATES[13]]
M1 = ("M1", {"alpha": 1.0})


def test_predictions_refuse_training_nights_after_the_test_nights():
    with pytest.raises(ValueError, match="before the test nights"):
        robustness.predictions(TABLE, TEST, *M1)


def test_predictions_carry_the_outcome_the_model_and_persistence():
    pred = robustness.predictions(TRAIN, TEST, *M1)
    assert len(pred) == len(TEST) == 50
    assert list(pred["p0"]) == list(TEST["snap_temp_c"]) and list(pred["y"]) == list(TEST["target_min_c"])


def test_model_without_daytime_features_never_uses_them():
    reduced = [c for c in NUMERIC if c not in robustness.DAYTIME]
    changed = TEST.copy()
    changed[robustness.DAYTIME] = 99.0                       # nonsense daytime values on test nights
    for name, params in (M1, ("M2", models.GRIDS["M2"][0])):
        a = robustness.predictions(TRAIN, TEST, name, params, numeric=reduced)["pred"]
        b = robustness.predictions(TRAIN, changed, name, params, numeric=reduced)["pred"]
        assert np.allclose(a, b)
        full = robustness.predictions(TRAIN, changed, name, params)["pred"]
        assert not np.allclose(a, full)                     # the full model does use them


def test_without_daytime_reports_both_on_the_same_test_rows():
    out = robustness.without_daytime(TRAIN, TEST, *M1)
    assert out["features_removed"] == ["day_max_temp_c", "morning_min_temp_c", "afternoon_cloud_pct"]
    assert out["with_daytime"]["model"]["n"] == out["without_daytime"]["model"]["n"] == 50
    assert out["with_daytime"]["p0"] == out["without_daytime"]["p0"]          # same rows, same P0
    assert out["mae_change"] == pytest.approx(out["without_daytime"]["model"]["mae"]
                                              - out["with_daytime"]["model"]["mae"])


def test_leave_one_point_out_drops_the_point_from_training_and_scoring():
    out = robustness.leave_one_point_out(TRAIN, TEST, *M1)
    assert list(out["point_left_out"]) == list(range(1, 11))
    assert (out["test_rows"] == 45).all()                   # 5 test nights x 9 points
    rest = TEST[TEST["location_id"] != 3]
    p0_mae = np.mean(np.abs(rest["snap_temp_c"] - rest["target_min_c"]))
    assert out.loc[out["point_left_out"] == 3, "p0_mae"].item() == pytest.approx(p0_mae)
    assert (out["model_mae"] < out["p0_mae"]).all()        # the synthetic pattern holds for every point


def test_cold_limits_three_five_and_eight():
    pred = robustness.predictions(TRAIN, TEST, *M1)
    out = robustness.cold_limits(pred)
    assert [(r["limit"], r["predictor"]) for r in out] == [
        (3.0, "model"), (3.0, "p0"), (5.0, "model"), (5.0, "p0"), (8.0, "model"), (8.0, "p0")]
    assert [r["primary"] for r in out] == [False, False, True, True, False, False]
    five = alerts.alert_scores(pred, limit=5.0)
    assert out[2]["tp"] == five["tp"] and out[2]["recall"] == five["recall"]
    assert out[0]["cold_location_nights"] <= out[2]["cold_location_nights"] <= out[4]["cold_location_nights"]


def test_worst_nights_by_hand():
    d1, d2, d3 = date(2026, 12, 1), date(2026, 12, 2), date(2026, 12, 3)
    pred = pd.DataFrame({"night_date": [d1, d1, d2, d2, d3, d3],
                         "y":    [0.0, 0.0, 4.0, 6.0, 1.0, 1.0],
                         "pred": [1.0, -1.0, 7.0, 7.0, 1.5, 1.5],   # night MAE 1, 2, 0.5
                         "p0":   [3.0, 3.0, 9.0, 9.0, 1.0, 1.0]})
    out = robustness.worst_nights(pred, n=2)
    assert list(out["night_date"]) == [d2, d1]
    assert out.loc[0, "model_mae"] == 2.0 and out.loc[0, "bias"] == 2.0 and out.loc[0, "p0_mae"] == 4.0
    assert out.loc[0, "cold_points"] == 1 and out.loc[0, "coldest"] == 4.0
    assert out.loc[1, "bias"] == 0.0


def test_daytime_check_is_only_for_m1_or_m2():
    with pytest.raises(ValueError):
        robustness.without_daytime(TRAIN, TEST, "P1", {})


def test_reduced_feature_list_is_ignored_by_the_baselines():
    assert isinstance(models.make("P0", numeric=["snap_temp_c"]), models.Persistence)


# ---------- with the test database ----------

def test_backfill_count(conn):
    first, last = date(2026, 11, 12), date(2026, 11, 13)
    assert robustness.count_backfill(conn, first, last) == {"values": 0, "runs": 0}
    at = datetime(2026, 11, 12, 20, 0, tzinfo=timezone.utc)
    run_id = conn.execute(
        """INSERT INTO collection_runs (trigger, request_sent_at, request_url, code_version, is_backfill)
           VALUES ('test', %s, 'synthetic', 'synthetic000', true) RETURNING run_id""", (at,)).fetchone()[0]
    conn.execute(
        """INSERT INTO observations (location_id, run_id, observed_at, response_received_at, is_live,
                                     is_backfill, temperature_c, payload_sha256)
           VALUES (1, %s, %s, %s, false, true, 3.0, %s)""",
        (run_id, at, at + timedelta(hours=3), "0" * 64))
    assert robustness.count_backfill(conn, first, last) == {"values": 1, "runs": 1}
    assert robustness.count_backfill(conn, date(2026, 11, 20), date(2026, 11, 21)) == {"values": 0, "runs": 0}
