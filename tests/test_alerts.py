"""Tests for the cold-night alert scores (Q4, O4; DECISIONS.md D016). Made-up numbers only."""
from datetime import date

import pandas as pd
import pytest

from nightwatch import alerts

D1, D2, D3 = date(2026, 12, 1), date(2026, 12, 2), date(2026, 12, 3)

# Hand-worked example, limit 5 °C (cold = y <= 5, alert = pred <= 5):
#   night  y     pred   cold  alert  result
#   D1     4.0   4.5    yes   yes    hit (TP)
#   D1     6.0   4.0    no    yes    false alarm (FP)
#   D1     5.0   6.0    yes   no     miss (FN)      5.0 is "at or below 5", so cold
#   D2    10.0   9.0    no    no     TN
#   D2     9.0   3.0    no    yes    false alarm (FP)
#   D3     2.0   1.0    yes   yes    hit (TP)
#   D3     7.0   8.0    no    no     TN
HAND = pd.DataFrame({"night_date": [D1, D1, D1, D2, D2, D3, D3],
                     "y":    [4.0, 6.0, 5.0, 10.0, 9.0, 2.0, 7.0],
                     "pred": [4.5, 4.0, 6.0, 9.0, 3.0, 1.0, 8.0]})


def test_hand_worked_confusion_matrix_and_scores():
    out = alerts.alert_scores(HAND)
    assert (out["tp"], out["fp"], out["fn"], out["tn"]) == (2, 2, 1, 2)
    assert out["precision"] == pytest.approx(2 / 4)            # TP / (TP + FP)
    assert out["recall"] == pytest.approx(2 / 3)               # TP / (TP + FN)
    assert out["f1"] == pytest.approx(4 / 7)                   # 2 * 0.5 * 2/3 / (0.5 + 2/3)
    assert out["cold_location_nights"] == 3 and out["cold_calendar_nights"] == 2
    assert out["alerts"] == 4 and out["n"] == 7


def test_few_cold_nights_make_the_result_descriptive_only():
    out = alerts.alert_scores(HAND)
    assert out["descriptive_only"] and out["recall_target_met"] is None
    assert out["reasons"] == ["only 3 cold location-nights (need 10)",
                              "only 2 distinct cold calendar nights (need 3)"]


def test_table_by_calendar_night():
    table = alerts.by_night(HAND)
    assert list(table["night_date"]) == [D1, D2, D3]
    assert table[["points", "cold_points", "alerted_points", "hits", "misses", "false_alarms"]
                 ].values.tolist() == [[3, 2, 2, 1, 1, 1], [2, 0, 1, 0, 0, 1], [2, 1, 1, 1, 0, 0]]
    assert list(table["night_cold"]) == [True, False, True]
    assert list(table["night_alerted"]) == [True, True, True]


def test_scores_without_alerts_or_cold_nights_are_none_not_zero():
    warm = pd.DataFrame({"night_date": [D1, D2], "y": [9.0, 10.0], "pred": [9.0, 10.0]})
    out = alerts.alert_scores(warm)
    assert out["precision"] is None and out["recall"] is None and out["f1"] is None
    assert out["tn"] == 2


def test_f1_is_zero_when_cold_nights_exist_but_no_alert_is_right():
    never_alerts = pd.DataFrame({"night_date": [D1, D2], "y": [2.0, 9.0], "pred": [7.0, 9.0]})
    out = alerts.alert_scores(never_alerts)
    assert out["recall"] == 0 and out["precision"] is None and out["f1"] == 0


def enough_cold(hits_per_night):
    """Three nights with four cold points each (12 cold location-nights): enough to judge O4."""
    rows = []
    for night, hits in zip((D1, D2, D3), hits_per_night):
        for i in range(4):
            rows.append({"night_date": night, "y": 2.0, "pred": 2.0 if i < hits else 7.0})
    return pd.DataFrame(rows)


def test_recall_target_is_judged_only_with_enough_cold_nights():
    met = alerts.alert_scores(enough_cold([4, 3, 3]))           # recall 10/12 = 0.83
    assert not met["descriptive_only"] and met["recall_target_met"] is True
    missed = alerts.alert_scores(enough_cold([3, 3, 3]))        # recall 9/12 = 0.75
    assert missed["recall_target_met"] is False


def test_limit_is_a_parameter():
    out = alerts.alert_scores(HAND, limit=8.0)                  # robustness check only
    assert out["limit"] == 8.0 and out["cold_location_nights"] == 5


@pytest.mark.parametrize("value,expected", [(8.01, "Low"), (8.0, "Medium"), (5.01, "Medium"),
                                            (5.0, "High"), (-3.0, "High")])
def test_dashboard_categories(value, expected):
    assert alerts.category(value) == expected
