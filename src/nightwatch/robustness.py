"""Robustness checks, sensitivity only (proposal v3 section 8; DECISIONS.md D016).

None of these changes the primary result, the selected model or its settings. They are run once,
after the protocol freeze, on the same training and test nights as the primary evaluation:

1. leave one point out: for each point, the selected model (same settings) refitted without that
   point and scored on the other points;
2. add the back-filled records: the collector stores live values only, so this counts them;
3. cold-night limit 3 and 8 °C next to the primary 5 °C;
4. the selected model refitted with the same settings on the same training nights without the three
   daytime features, scored on the same test nights;
5. the worst nights of the selected model.

`train` and `test` are feature tables (features.load). `name` and `params` are the selected model and
its settings, as frozen.
"""
from __future__ import annotations

import pandas as pd

from . import alerts, models, stats
from .features import FEATURES, NUMERIC, TARGET

DAYTIME = ["day_max_temp_c", "morning_min_temp_c", "afternoon_cloud_pct"]
WORST_NIGHTS = 3


def predictions(train: pd.DataFrame, test: pd.DataFrame, name: str, params: dict,
                numeric: list[str] | None = None) -> pd.DataFrame:
    """Test rows with the outcome y, the model's prediction and persistence P0 (no learning)."""
    if not train.empty and not test.empty and train["night_date"].max() >= test["night_date"].min():
        raise ValueError("training nights must all be before the test nights")
    model = models.make(name, params, numeric).fit(train[FEATURES], train[TARGET])
    return pd.DataFrame({"night_date": test["night_date"].to_numpy(),
                         "location_id": test["location_id"].to_numpy(),
                         "y": test[TARGET].to_numpy(dtype=float),
                         "pred": model.predict(test[FEATURES]),
                         "p0": test["snap_temp_c"].to_numpy(dtype=float)})


def compare(pred: pd.DataFrame) -> dict:
    """MAE/RMSE/bias of the model and of P0, the MAE reduction and the mean night difference."""
    model, p0 = stats.metrics(pred["y"], pred["pred"]), stats.metrics(pred["y"], pred["p0"])
    reduction = None if p0["mae"] == 0 else 1 - model["mae"] / p0["mae"]
    d = stats.night_differences(pred, "pred", "p0")
    return {"model": model, "p0": p0, "mae_reduction": reduction, "mean_d": float(d.mean()),
            "nights": len(d)}


def leave_one_point_out(train, test, name, params) -> pd.DataFrame:
    """For each point: refit without it on the training nights and score the other points."""
    rows = []
    for point in sorted(set(train["location_id"]) | set(test["location_id"])):
        pred = predictions(train[train["location_id"] != point], test[test["location_id"] != point],
                           name, params)
        result = compare(pred)
        rows.append({"point_left_out": int(point), "test_rows": len(pred),
                     "model_mae": result["model"]["mae"], "p0_mae": result["p0"]["mae"],
                     "mae_reduction": result["mae_reduction"], "mean_d": result["mean_d"]})
    return pd.DataFrame(rows)


def count_backfill(conn, first, last) -> dict:
    """Back-filled hourly values and back-fill runs from the first night to the morning after the last.

    The collector stores live values only (collector.py) and nothing has been back-filled (D014), so
    both are expected to be 0. Then the set with back-filled records equals the primary set.
    """
    from .nights import windows
    start, end = windows(first).morning[0], windows(last).final_at
    values = conn.execute("SELECT count(*) FROM observations WHERE is_backfill AND observed_at BETWEEN %s AND %s",
                          (start, end)).fetchone()[0]
    runs = conn.execute("SELECT count(*) FROM collection_runs WHERE is_backfill AND request_sent_at BETWEEN %s AND %s",
                        (start, end)).fetchone()[0]
    return {"values": values, "runs": runs}


def cold_limits(pred: pd.DataFrame, limits=(alerts.SENSITIVITY_LIMITS[0], alerts.COLD_LIMIT,
                                            alerts.SENSITIVITY_LIMITS[1])) -> list[dict]:
    """Alert scores at 3, 5 and 8 °C, for the model and for P0. 5 °C stays the primary limit."""
    out = []
    for limit in limits:
        for label, column in (("model", "pred"), ("p0", "p0")):
            scores = alerts.alert_scores(pred, pred=column, limit=limit)
            out.append({"limit": limit, "predictor": label, "primary": limit == alerts.COLD_LIMIT,
                        **{k: scores[k] for k in ("cold_location_nights", "cold_calendar_nights",
                                                  "alerts", "tp", "fp", "fn", "tn", "precision",
                                                  "recall", "f1", "descriptive_only")}})
    return out


def without_daytime(train, test, name, params) -> dict:
    """The selected model, same settings and nights, with and without the three daytime features."""
    if name not in ("M1", "M2"):
        raise ValueError(f"{name} uses no daytime features; the check is for the selected M1 or M2")
    reduced = [c for c in NUMERIC if c not in DAYTIME]
    full = compare(predictions(train, test, name, params))
    less = compare(predictions(train, test, name, params, numeric=reduced))
    return {"features_removed": DAYTIME, "with_daytime": full, "without_daytime": less,
            "mae_change": less["model"]["mae"] - full["model"]["mae"]}


def worst_nights(pred: pd.DataFrame, n: int = WORST_NIGHTS) -> pd.DataFrame:
    """The n test nights with the largest model MAE, with bias, P0 MAE and cold points."""
    frame = pred.assign(abs_error=(pred["pred"] - pred["y"]).abs(), error=pred["pred"] - pred["y"],
                        p0_abs_error=(pred["p0"] - pred["y"]).abs(),
                        cold=pred["y"] <= alerts.COLD_LIMIT)
    out = frame.groupby("night_date").agg(points=("y", "size"), model_mae=("abs_error", "mean"),
                                          bias=("error", "mean"), p0_mae=("p0_abs_error", "mean"),
                                          cold_points=("cold", "sum"), coldest=("y", "min"))
    out = out.reset_index().sort_values(["model_mae", "night_date"], ascending=[False, True])
    return out.head(n).reset_index(drop=True)
