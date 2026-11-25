"""Development run: `python -m nightwatch.develop` (DECISIONS.md D015).

Cross-validates P0, P1 and every fixed setting of M1 and M2 on the development nights only: the
first 13 complete calendar nights (protocol C1). Later nights are never selected, so their outcomes
are never read. It prints counts and cross-validation MAEs only, and writes nothing to the database.

This is development analysis allowed during collection. The final split, the final choice of model,
the `protocol-frozen` tag and the test predictions come only after the cut-off (C1, D013).
"""
from __future__ import annotations

import os
import sys

from . import cv, db, features


def report(table, left_out) -> str:
    dates = sorted(table["night_date"].unique())
    lines = [f"Development nights: {len(dates)} of at most {features.DEVELOPMENT_NIGHTS} "
             f"({', '.join(str(d) for d in dates)})",
             f"Location-nights in the primary set: {len(table)}"]
    lines += [f"Left out: {reason}" for reason in left_out]
    try:
        folds = cv.folds(dates)
    except ValueError as exc:
        return "\n".join(lines + [f"No cross-validation yet: {exc}."])
    lines.append("")
    for number, (training, validation) in enumerate(folds, start=1):
        lines.append(f"Fold {number}: train on {len(training)} nights up to {training[-1]}, "
                     f"validate on {validation[0]} and {validation[1]}")
    results = cv.run_grid(table)
    lines += ["", "model  setting                                          CV MAE   fold MAEs (°C)",
              "-----  -----------------------------------------------  -------  -----------------------"]
    for r in results:
        setting = ", ".join(f"{k}={v}" for k, v in r.params.items()) or "-"
        folds_text = "  ".join(f"{m:.2f}" for m in r.fold_mae)
        lines.append(f"{r.name:<5}  {setting:<47}  {r.cv_mae:7.3f}  {folds_text}")
    m1, m2, chosen = cv.best(results, "M1"), cv.best(results, "M2"), cv.choose(results)
    lines += ["", f"Best M1: {m1.params} ({m1.cv_mae:.3f}). Best M2: {m2.params} ({m2.cv_mae:.3f}).",
              f"On these development nights the rule would pick {chosen.name}. This is provisional: "
              "the final choice is made once, on the final training nights, after the cut-off."]
    return "\n".join(lines)


def main() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is not set.", file=sys.stderr)
        return 2
    with db.connect(dsn) as conn:
        table, left_out = features.load(conn, "development")
    print(report(table, left_out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
