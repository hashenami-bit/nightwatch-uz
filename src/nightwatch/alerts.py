"""Cold-night alert scores: Q4 and objective O4 (proposal v3 sections 4, 5 and 8; DECISIONS.md D016).

Fixed before any data existed (proposal v3 section 5):
- Event: night minimum at or below 5 °C.  Alert: predicted night minimum at or below 5 °C.
- Dashboard category from the predicted minimum: Low above 8 °C, Medium above 5 and up to 8 °C,
  High at 5 °C or below (High is the alert).
- O4 target: recall of at least 0.80, with precision next to it. If the scored nights have fewer than
  10 cold location-nights or fewer than 3 distinct cold calendar nights, the result is descriptive only
  and the target is not judged.
- Q4 is descriptive: events are few and dependent, so there is no significance test.

The limits 3 and 8 °C are used only in the robustness check; the primary limit stays 5 °C.
This is a general temperature alert, not a frost warning.
"""
from __future__ import annotations

import pandas as pd

COLD_LIMIT = 5.0
SENSITIVITY_LIMITS = (3.0, 8.0)
MEDIUM_ABOVE = 5.0
LOW_ABOVE = 8.0
MIN_COLD_POINTS = 10
MIN_COLD_NIGHTS = 3
RECALL_TARGET = 0.80


def category(predicted_min: float) -> str:
    """Dashboard category for one predicted night minimum."""
    if predicted_min > LOW_ABOVE:
        return "Low"
    if predicted_min > MEDIUM_ABOVE:
        return "Medium"
    return "High"


def _ratio(top: int, bottom: int) -> float | None:
    return top / bottom if bottom else None


def alert_scores(table: pd.DataFrame, pred: str = "pred", target: str = "y",
                 limit: float = COLD_LIMIT) -> dict:
    """Confusion matrix, precision, recall and F1 by location-night, plus the cold-night counts.

    A score whose denominator is 0 (for example precision with no alerts) is None, not 0.
    F1 = 2 TP / (2 TP + FP + FN): None only with no cold nights and no alerts.
    """
    cold = table[target] <= limit
    alert = table[pred] <= limit
    tp, fp = int((cold & alert).sum()), int((~cold & alert).sum())
    fn, tn = int((cold & ~alert).sum()), int((~cold & ~alert).sum())
    precision, recall = _ratio(tp, tp + fp), _ratio(tp, tp + fn)
    f1 = _ratio(2 * tp, 2 * tp + fp + fn)     # 0 when cold nights exist but no alert was right
    cold_nights = int(table.loc[cold, "night_date"].nunique())

    reasons = []
    if int(cold.sum()) < MIN_COLD_POINTS:
        reasons.append(f"only {int(cold.sum())} cold location-nights (need {MIN_COLD_POINTS})")
    if cold_nights < MIN_COLD_NIGHTS:
        reasons.append(f"only {cold_nights} distinct cold calendar nights (need {MIN_COLD_NIGHTS})")
    met = None if reasons or recall is None else recall >= RECALL_TARGET
    return {"limit": limit, "n": len(table), "cold_location_nights": int(cold.sum()),
            "cold_calendar_nights": cold_nights, "alerts": tp + fp,
            "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": precision, "recall": recall, "f1": f1,
            "descriptive_only": bool(reasons), "reasons": reasons, "recall_target_met": met}


def by_night(table: pd.DataFrame, pred: str = "pred", target: str = "y",
             limit: float = COLD_LIMIT) -> pd.DataFrame:
    """The table by calendar night: one row per night, in date order."""
    frame = pd.DataFrame({"night_date": table["night_date"].to_numpy(),
                          "cold": (table[target] <= limit).to_numpy(),
                          "alert": (table[pred] <= limit).to_numpy()})
    frame["hit"] = frame["cold"] & frame["alert"]
    frame["miss"] = frame["cold"] & ~frame["alert"]
    frame["false_alarm"] = ~frame["cold"] & frame["alert"]
    out = frame.groupby("night_date").agg(
        points=("cold", "size"), cold_points=("cold", "sum"), alerted_points=("alert", "sum"),
        hits=("hit", "sum"), misses=("miss", "sum"), false_alarms=("false_alarm", "sum"))
    out = out.astype("int64")
    out["night_cold"] = out["cold_points"] > 0
    out["night_alerted"] = out["alerted_points"] > 0
    return out.reset_index().sort_values("night_date", ignore_index=True)
