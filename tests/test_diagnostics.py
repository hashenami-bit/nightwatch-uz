"""Tests for the extra metrics and residual checks (DECISIONS.md D017). Made-up numbers only."""
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from nightwatch import diagnostics

D = [date(2026, 11, 23) + timedelta(days=i) for i in range(10)]      # 23 Nov is a Monday


def table(days, y, pred, p0=None):
    return pd.DataFrame({"night_date": days, "location_id": 1, "y": y, "pred": pred,
                         "p0": p0 if p0 is not None else y})


def test_errors_by_night_in_date_order_by_hand():
    pred = table([D[1], D[0], D[0], D[1]], [0.0, 0.0, 0.0, 2.0], [1.0, 1.0, -1.0, 2.0], [3.0, 0.0, 2.0, 2.0])
    out = diagnostics.errors_by_night(pred)
    assert list(out["night_date"]) == [D[0], D[1]]
    assert out[["points", "model_mae", "model_bias", "p0_mae"]].values.tolist() == [
        [2, 1.0, 0.0, 1.0],          # errors +1 and -1; P0 errors 0 and 2
        [2, 0.5, 0.5, 1.5]]          # errors +1 and 0;  P0 errors 3 and 0


def test_bias_per_calendar_week_monday_to_sunday():
    # 23-29 Nov is one week, 30 Nov starts the next
    pred = table([D[0], D[6], D[7]], [0.0, 0.0, 0.0], [1.0, 2.0, -1.0])
    out = diagnostics.bias_by_week(pred)
    assert list(out["week_from"]) == [date(2026, 11, 23), date(2026, 11, 30)]
    assert list(out["nights"]) == [2, 1] and list(out["bias"]) == [1.5, -1.0] and list(out["mae"]) == [1.5, 1.0]
    assert diagnostics.week_start(date(2026, 11, 29)) == date(2026, 11, 23)       # Sunday


def test_breusch_pagan_by_hand():
    # squared residuals 0, 1, 4, 9 against fitted 0, 1, 2, 3
    fitted = np.array([0.0, 1, 2, 3])
    residual = np.array([0.0, 1, 2, 3])
    e2 = residual ** 2
    slope, intercept = np.polyfit(fitted, e2, 1)                 # 3x - 1
    r2 = 1 - np.sum((e2 - (slope * fitted + intercept)) ** 2) / np.sum((e2 - e2.mean()) ** 2)
    assert (slope, intercept) == pytest.approx((3.0, -1.0)) and r2 == pytest.approx(1 - 4 / 49)
    out = diagnostics.breusch_pagan(residual, fitted)
    assert out["lm"] == pytest.approx(4 * (1 - 4 / 49)) and out["df"] == 1
    assert diagnostics.breusch_pagan([1, 1, 1], [0, 1, 2]) is None     # no spread: no test


def test_residual_checks_report_all_three_and_the_limit():
    rng = np.random.default_rng(0)
    days = [d for d in D for _ in range(10)]
    y = rng.normal(5, 3, size=len(days))
    pred = table(days, y, y + rng.normal(0, 1, size=len(days)))
    out = diagnostics.residual_checks(pred)
    assert out["n"] == 100 and out["normality"]["test"] == "Shapiro-Wilk"
    assert 0 <= out["normality"]["p_value"] <= 1 and 0 <= out["equal_variance"]["p_value"] <= 1
    assert out["independence"]["nights"] == 10 and out["independence"]["pairs_lag1"] == 9
    assert out["independence"]["pairs_lag2"] == 8 and -1 <= out["independence"]["lag1"] <= 1
    assert "not independent" in out["limit"]


def test_residual_checks_with_too_little_data_give_none():
    out = diagnostics.residual_checks(table([D[0], D[2]], [1.0, 2.0], [1.0, 2.0]))
    assert out["normality"] is None and out["equal_variance"] is None
    assert out["independence"]["pairs_lag1"] == 0                     # 23 and 25 Nov are not neighbours
