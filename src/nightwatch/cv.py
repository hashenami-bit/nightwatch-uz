"""Time-ordered cross-validation (protocol C1, proposal v3 section 8, DECISIONS.md D015).

- The last 8 training nights form 4 folds of 2 nights each, in date order.
- Expanding window: each fold is predicted by a model fitted only on nights with a date before the
  fold's first night. A night D target is complete at 08:15 on D + 1, so it may be used for a
  validation night only if D is earlier.
- Folds are whole nights; all points of a night are in the same fold.
- A new model is made for each fold, so its preprocessing is fitted on that fold's training rows.
- Fold MAE = mean absolute error over the fold's location-nights.
  CV MAE = mean of the 4 fold MAEs. It is used only to choose; it is not the final accuracy.
- Choice: the best setting of M1 and of M2 by CV MAE. M2 is chosen only if its CV MAE is more than
  0.05 °C below M1's; otherwise M1, because it is simpler.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from . import models
from .features import FEATURES, TARGET

N_FOLDS = 4
FOLD_NIGHTS = 2
SIMPLER_MARGIN = 0.05   # °C


def folds(dates) -> list[tuple[list[date], list[date]]]:
    """(training nights, validation nights) for each fold, from the sorted training nights."""
    dates = sorted(set(dates))
    needed = N_FOLDS * FOLD_NIGHTS
    if len(dates) <= needed:
        raise ValueError(f"cross-validation needs more than {needed} nights, got {len(dates)}")
    last = dates[-needed:]
    out = []
    for i in range(N_FOLDS):
        validation = last[i * FOLD_NIGHTS:(i + 1) * FOLD_NIGHTS]
        training = [d for d in dates if d < validation[0]]
        out.append((training, validation))
    return out


def fit_fold(table: pd.DataFrame, name: str, params: dict, training: list[date]):
    """A new model fitted on the training nights only."""
    rows = table[table["night_date"].isin(training)]
    return models.make(name, params).fit(rows[FEATURES], rows[TARGET])


@dataclass
class Result:
    name: str
    params: dict
    fold_mae: list[float]
    predictions: pd.DataFrame = field(repr=False)   # night_id, night_date, location_id, fold, y, pred

    @property
    def cv_mae(self) -> float:
        return float(np.mean(self.fold_mae))


def cross_validate(table: pd.DataFrame, name: str, params: dict | None = None) -> Result:
    params = params or {}
    fold_mae, parts = [], []
    for number, (training, validation) in enumerate(folds(table["night_date"]), start=1):
        if max(training) >= min(validation):          # the timing rule (C1); folds() keeps it
            raise AssertionError("a fold trains on a night that is not before its validation nights")
        model = fit_fold(table, name, params, training)
        rows = table[table["night_date"].isin(validation)]
        pred = model.predict(rows[FEATURES])
        fold_mae.append(float(np.mean(np.abs(rows[TARGET].to_numpy() - pred))))
        parts.append(pd.DataFrame({"night_id": rows["night_id"].to_numpy(),
                                   "night_date": rows["night_date"].to_numpy(),
                                   "location_id": rows["location_id"].to_numpy(),
                                   "fold": number, "y": rows[TARGET].to_numpy(), "pred": pred}))
    return Result(name, params, fold_mae, pd.concat(parts, ignore_index=True))


def run_grid(table: pd.DataFrame) -> list[Result]:
    """Every setting of every model, in the fixed order of models.GRIDS."""
    return [cross_validate(table, name, params)
            for name, grid in models.GRIDS.items() for params in grid]


def best(results: list[Result], name: str) -> Result:
    """Lowest CV MAE for one model; on an exact tie the earlier setting in the grid."""
    return min((r for r in results if r.name == name), key=lambda r: r.cv_mae)


def choose(results: list[Result]) -> Result:
    """M1 or M2 by CV MAE; M2 only if more than 0.05 °C better."""
    m1, m2 = best(results, "M1"), best(results, "M2")
    return m2 if m2.cv_mae < m1.cv_mae - SIMPLER_MARGIN else m1
