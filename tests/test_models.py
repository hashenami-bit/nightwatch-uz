"""Tests for the baselines, the candidates and the time-ordered CV (D015). Synthetic data only."""
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from nightwatch import cv, models
from nightwatch.features import FEATURES, TARGET
from tests.fixtures.synthetic import synthetic_table

DATES = [date(2026, 11, 12) + timedelta(days=i) for i in range(13)]
TABLE = synthetic_table(DATES, seed=3)


def rows(dates):
    return TABLE[TABLE["night_date"].isin(dates)]


# ---------- models ----------

def test_p0_is_the_snapshot_temperature():
    part = rows(DATES[:2])
    pred = models.make("P0").fit(part[FEATURES], part[TARGET]).predict(part[FEATURES])
    assert list(pred) == list(part["snap_temp_c"])


def test_p1_subtracts_each_points_mean_drop_from_training_only():
    X = pd.DataFrame({"location_id": [1, 1, 2], "snap_temp_c": [10.0, 8.0, 5.0]})
    y = [6.0, 6.0, 4.0]                       # drops: 4 and 2 at point 1 (mean 3), 1 at point 2
    p1 = models.make("P1").fit(X, y)
    new = pd.DataFrame({"location_id": [1, 2, 3], "snap_temp_c": [9.0, 9.0, 9.0]})
    # point 3 has no training rows: mean drop of all rows (4 + 2 + 1) / 3
    assert p1.predict(new) == pytest.approx([6.0, 8.0, 9.0 - 7 / 3])


def test_grids_are_the_fixed_settings():
    assert models.GRIDS["M1"] == [{"alpha": a} for a in (0.1, 1.0, 10.0, 100.0)]
    assert len(models.GRIDS["M2"]) == 8
    assert {(p["max_depth"], p["learning_rate"], p["max_iter"]) for p in models.GRIDS["M2"]} == {
        (d, lr, it) for d in (2, 3) for lr in (0.05, 0.1) for it in (100, 300)}
    hgb = models.make("M2", models.GRIDS["M2"][0]).named_steps["model"]
    assert hgb.min_samples_leaf == 10 and hgb.early_stopping is False


@pytest.mark.parametrize("name,params", [("M1", {"alpha": 1.0}),
                                         ("M2", {"max_depth": 2, "learning_rate": 0.1, "max_iter": 100})])
def test_candidates_learn_the_synthetic_pattern(name, params):
    """On made-up data with a known evening-to-night drop, both candidates beat persistence."""
    train, test = rows(DATES[:10]), rows(DATES[10:])
    pred = models.make(name, params).fit(train[FEATURES], train[TARGET]).predict(test[FEATURES])
    p0 = models.make("P0").fit(train[FEATURES], train[TARGET]).predict(test[FEATURES])
    y = test[TARGET].to_numpy()
    assert np.mean(np.abs(y - pred)) < np.mean(np.abs(y - p0))


def test_an_unknown_point_does_not_break_prediction():
    train = rows(DATES[:5])
    train = train[train["location_id"] != 10]
    for name, params in [("M1", {"alpha": 1.0}), ("M2", models.GRIDS["M2"][0])]:
        model = models.make(name, params).fit(train[FEATURES], train[TARGET])
        assert np.isfinite(model.predict(rows(DATES[5:6])[FEATURES])).all()


# ---------- cross-validation ----------

def test_four_folds_of_two_nights_at_the_end_expanding_window():
    folds = cv.folds(DATES)
    assert len(folds) == 4
    assert [v for _, v in folds] == [DATES[5:7], DATES[7:9], DATES[9:11], DATES[11:13]]
    assert [t for t, _ in folds] == [DATES[:5], DATES[:7], DATES[:9], DATES[:11]]
    for training, validation in folds:
        assert max(training) < min(validation)          # only nights before the validation night


def test_folds_follow_the_real_dates_across_a_gap():
    with_gap = [date(2026, 11, d) for d in (12, 13, 14, 15, 16, 17, 18, 19, 22, 23, 24)]
    folds = cv.folds(with_gap)
    assert folds[0] == (with_gap[:3], with_gap[3:5])
    assert folds[2][1] == [date(2026, 11, 19), date(2026, 11, 22)]   # whole nights, date order
    assert folds[3][0] == with_gap[:9]


def test_too_few_nights_for_cross_validation():
    with pytest.raises(ValueError):
        cv.folds(DATES[:8])


def test_preprocessing_is_fitted_inside_the_fold():
    """A huge wind value on a validation night must not reach the fold's fitted scaler."""
    table = TABLE.copy()
    training, validation = cv.folds(table["night_date"])[0]
    table.loc[table["night_date"] == validation[0], "snap_wind_kmh"] = 1000.0
    model = cv.fit_fold(table, "M1", {"alpha": 1.0}, training)
    scaler = model.named_steps["prep"].named_transformers_["scale"]
    wind = list(scaler.feature_names_in_).index("snap_wind_kmh")
    expected = table[table["night_date"].isin(training)]["snap_wind_kmh"].mean()
    assert scaler.mean_[wind] == pytest.approx(expected) and scaler.mean_[wind] < 100


def test_cross_validate_predicts_every_validation_row_once():
    result = cv.cross_validate(TABLE, "M1", {"alpha": 10.0})
    validation_rows = rows(DATES[5:])
    assert sorted(result.predictions["night_id"]) == sorted(validation_rows["night_id"])
    assert result.predictions.groupby("fold")["night_date"].nunique().tolist() == [2, 2, 2, 2]
    assert result.cv_mae == pytest.approx(np.mean(result.fold_mae))
    for number, part in result.predictions.groupby("fold"):
        assert result.fold_mae[number - 1] == pytest.approx(np.mean(np.abs(part["y"] - part["pred"])))


def test_grid_runs_every_fixed_setting():
    results = cv.run_grid(TABLE)
    assert [r.name for r in results] == ["P0", "P1"] + ["M1"] * 4 + ["M2"] * 8
    chosen = cv.choose(results)
    assert chosen.name in ("M1", "M2")
    assert cv.best(results, "M1").cv_mae < cv.best(results, "P0").cv_mae


def fake(name, mae):
    return cv.Result(name, {}, [mae] * 4, pd.DataFrame())


@pytest.mark.parametrize("m1,m2,expected", [(1.00, 0.96, "M1"),    # within 0.05: the simpler M1
                                            (1.00, 0.95, "M1"),    # exactly 0.05: still M1
                                            (1.00, 0.94, "M2"),    # more than 0.05 better
                                            (0.90, 1.00, "M1")])
def test_choice_prefers_m1_unless_m2_is_more_than_005_better(m1, m2, expected):
    assert cv.choose([fake("M1", m1), fake("M2", m2)]).name == expected


# ---------- development report ----------

def test_development_report_prints_folds_and_cv_mae_only(monkeypatch):
    from nightwatch import develop
    monkeypatch.setattr(models, "GRIDS", {"P0": [{}], "P1": [{}], "M1": [{"alpha": 1.0}],
                                          "M2": [{"max_depth": 2, "learning_rate": 0.1, "max_iter": 100}]})
    text = develop.report(TABLE, [])
    assert "Development nights: 13 of at most 13" in text and "Fold 4: train on 11 nights" in text
    assert "would pick" in text and "provisional" in text
    assert "target" not in text.lower()                 # no outcomes, only CV MAEs


def test_development_report_before_nine_nights():
    from nightwatch import develop
    text = develop.report(rows(DATES[:8]), [])
    assert "No cross-validation yet" in text
