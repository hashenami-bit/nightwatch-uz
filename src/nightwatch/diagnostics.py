"""Extra metrics and residual checks (proposal v3 section 8; DECISIONS.md D017).

They describe the selected model on the test nights. They are exploratory, like everything on 5 to 7
test nights, and change nothing in the selection.

`pred` is a table of test rows with night_date, location_id, y (night minimum), pred (selected model)
and p0 (persistence), as made by robustness.predictions.

- errors by night, in date order;
- mean error (bias) per calendar week (Monday to Sunday), because nights get colder through November;
- residual checks, residual = y - pred:
  normality       Shapiro-Wilk on the location-night residuals;
  equal variance  Breusch-Pagan (Koenker form): squared residual regressed on the prediction,
                  LM = n * R-squared, chi-squared with 1 degree of freedom;
  independence    lag-1 and lag-2 autocorrelation of the night-mean residual, using only nights
                  exactly 1 or 2 calendar days apart (as in the DM test).
  The ten points of one night share that night's weather, so the location-night residuals are not
  independent; the p-values are a rough guide only and are reported with this limit.
"""
from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd
from scipy import stats as sps

from . import stats

MIN_SHAPIRO = 3


def errors_by_night(pred: pd.DataFrame) -> pd.DataFrame:
    """One row per test night in date order: model MAE and bias, P0 MAE."""
    frame = pred.assign(error=pred["pred"] - pred["y"], abs_error=(pred["pred"] - pred["y"]).abs(),
                        p0_abs_error=(pred["p0"] - pred["y"]).abs())
    out = frame.groupby("night_date").agg(points=("y", "size"), model_mae=("abs_error", "mean"),
                                          model_bias=("error", "mean"), p0_mae=("p0_abs_error", "mean"))
    return out.reset_index().sort_values("night_date", ignore_index=True)


def week_start(day):
    """The Monday of the calendar week of a date."""
    return day - timedelta(days=day.weekday())


def bias_by_week(pred: pd.DataFrame) -> pd.DataFrame:
    """Mean error (prediction minus outcome) and MAE per calendar week, Monday to Sunday."""
    frame = pred.assign(week=[week_start(d) for d in pred["night_date"]], error=pred["pred"] - pred["y"])
    frame["abs_error"] = frame["error"].abs()
    out = frame.groupby("week").agg(nights=("night_date", "nunique"), rows=("y", "size"),
                                    bias=("error", "mean"), mae=("abs_error", "mean"))
    return out.reset_index().rename(columns={"week": "week_from"}).sort_values("week_from", ignore_index=True)


def breusch_pagan(residual, fitted) -> dict | None:
    """Koenker's Breusch-Pagan test with one regressor. None if it cannot be computed."""
    e2 = np.asarray(residual, dtype=float) ** 2
    x = np.asarray(fitted, dtype=float)
    n = e2.size
    if n < 3 or np.ptp(x) == 0 or np.ptp(e2) == 0:
        return None
    slope, intercept = np.polyfit(x, e2, 1)
    r2 = 1 - np.sum((e2 - (slope * x + intercept)) ** 2) / np.sum((e2 - e2.mean()) ** 2)
    lm = float(n * r2)
    return {"lm": lm, "df": 1, "p_value": float(sps.chi2.sf(lm, df=1))}


def residual_checks(pred: pd.DataFrame) -> dict:
    residual = (pred["y"] - pred["pred"]).to_numpy(dtype=float)
    normality = None
    if residual.size >= MIN_SHAPIRO and np.ptp(residual) > 0:
        w, p = sps.shapiro(residual)
        normality = {"test": "Shapiro-Wilk", "w": float(w), "p_value": float(p)}
    night_mean = pd.Series(residual, index=pred["night_date"].to_numpy()).groupby(level=0).mean()
    return {
        "n": int(residual.size),
        "normality": normality,
        "equal_variance": breusch_pagan(residual, pred["pred"]),
        "independence": {"nights": len(night_mean),
                         "pairs_lag1": len(stats.neighbour_pairs(night_mean.index, 1)),
                         "pairs_lag2": len(stats.neighbour_pairs(night_mean.index, 2)),
                         "lag1": stats.autocorrelation(night_mean, 1) if len(night_mean) else None,
                         "lag2": stats.autocorrelation(night_mean, 2) if len(night_mean) else None},
        "limit": "points of one night share its weather, so residuals are not independent; "
                 "p-values are a rough guide on few nights",
    }
