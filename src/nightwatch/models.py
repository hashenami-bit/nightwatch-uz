"""The two baselines and two candidates, fixed in proposal v3 section 8 (DECISIONS.md D015).

Every model has the same two steps: `fit(X, y)` on training rows and `predict(X)` for new rows.
X is the feature table (features.FEATURES), y is the night minimum. A new, unfitted model is made
for every fit, so nothing learned on one set of nights is carried to another.

- P0  persistence: night minimum = snapshot temperature. Learns nothing.
- P1  P0 minus the average evening-to-night drop (snapshot temperature minus night minimum) of
      that point, learned on the training rows only.
- M1  ridge regression with point indicators; alpha in {0.1, 1, 10, 100}.
- M2  HistGradientBoostingRegressor; max_depth in {2, 3}, learning_rate in {0.05, 0.1},
      max_iter in {100, 300}, min_samples_leaf = 10.

For M1 and M2 the preprocessing (scaling, point indicators) is part of the model, so it is fitted
on the training rows of each fit and nowhere else.
"""
from __future__ import annotations

from itertools import product

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder, StandardScaler

from .features import CATEGORICAL, NUMERIC

RANDOM_STATE = 0


class Persistence:
    """P0: the night minimum equals the latest temperature in the snapshot."""

    def fit(self, X: pd.DataFrame, y) -> "Persistence":
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return X["snap_temp_c"].to_numpy(dtype=float)


class PointDrop:
    """P1: snapshot temperature minus the point's average evening-to-night drop.

    The drop is learned on the training rows only. A point with no training rows uses the mean drop
    of all training rows (D015).
    """

    def fit(self, X: pd.DataFrame, y) -> "PointDrop":
        drop = X["snap_temp_c"].to_numpy(dtype=float) - np.asarray(y, dtype=float)
        frame = pd.DataFrame({"location_id": X["location_id"].to_numpy(), "drop": drop})
        self.drop_by_point_ = frame.groupby("location_id")["drop"].mean().to_dict()
        self.drop_all_ = float(drop.mean())
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        drops = np.array([self.drop_by_point_.get(loc, self.drop_all_) for loc in X["location_id"]])
        return X["snap_temp_c"].to_numpy(dtype=float) - drops


def ridge(alpha: float, numeric: list[str] = NUMERIC) -> Pipeline:
    """M1: point indicators (one column per point) and scaled numeric features, then ridge.

    `numeric` is the full fixed list except in the robustness check without the daytime features.
    """
    prep = ColumnTransformer([
        ("point", OneHotEncoder(handle_unknown="ignore", sparse_output=False), CATEGORICAL),
        ("scale", StandardScaler(), list(numeric)),
    ])
    return Pipeline([("prep", prep), ("model", Ridge(alpha=alpha))])


def boosting(max_depth: int, learning_rate: float, max_iter: int,
             numeric: list[str] = NUMERIC) -> Pipeline:
    """M2: the point as a native categorical feature (column 0), numeric features unchanged."""
    prep = ColumnTransformer([
        ("point", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1), CATEGORICAL),
        ("keep", "passthrough", list(numeric)),
    ])
    model = HistGradientBoostingRegressor(
        max_depth=max_depth, learning_rate=learning_rate, max_iter=max_iter, min_samples_leaf=10,
        categorical_features=[0], early_stopping=False, random_state=RANDOM_STATE)
    return Pipeline([("prep", prep), ("model", model)])


# The settings tried, fixed in proposal v3 section 8. P0 and P1 have none.
GRIDS: dict[str, list[dict]] = {
    "P0": [{}],
    "P1": [{}],
    "M1": [{"alpha": a} for a in (0.1, 1.0, 10.0, 100.0)],
    "M2": [{"max_depth": d, "learning_rate": lr, "max_iter": it}
           for d, lr, it in product((2, 3), (0.05, 0.1), (100, 300))],
}
BUILDERS = {"P0": lambda: Persistence(), "P1": lambda: PointDrop(), "M1": ridge, "M2": boosting}


def make(name: str, params: dict | None = None, numeric: list[str] | None = None):
    """A new, unfitted model. `numeric` (M1 and M2 only) replaces the numeric feature list."""
    params = dict(params or {})
    if numeric is not None and name in ("M1", "M2"):
        params["numeric"] = list(numeric)
    return BUILDERS[name](**params)
