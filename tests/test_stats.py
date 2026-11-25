"""Tests for the test-night statistics (protocol C2, DECISIONS.md D015). Synthetic numbers only."""
import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

from nightwatch import stats


def series(days, values):
    return pd.Series(values, index=[date(2026, 12, d) for d in days], dtype=float)


# Hand-worked example. Five test nights, with a gap on 4 Dec:
#   date   1 Dec  2 Dec  3 Dec  (4 Dec missing)  5 Dec  6 Dec
#   d_t      1     -1      2                       0      3
HAND = series([1, 2, 3, 5, 6], [1, -1, 2, 0, 3])


def test_hand_worked_diebold_mariano():
    # n = 5, mean = 5 / 5 = 1
    # deviations d_t - mean: 0, -2, 1, -1, 2
    # g0 = (0 + 4 + 1 + 1 + 4) / 5 = 2
    # neighbour pairs (exactly one day apart): (1,2) 0 * -2 = 0; (2,3) -2 * 1 = -2; (5,6) -1 * 2 = -2
    #   (3 Dec, 5 Dec) crosses the gap and is NOT used.
    # g1 = (0 - 2 - 2) / 5 = -0.8
    # V = g0 + 2 * (1/2) * g1 = 2 - 0.8 = 1.2
    # DM = 1 / sqrt(1.2 / 5) = 1 / sqrt(0.24) = 2.04124
    # DM* = DM * sqrt(4 / 5) = sqrt(0.8 / 0.24) = sqrt(10 / 3) = 1.82574
    # t with 4 df: 1.533 < 1.826 < 2.132 (two-sided 20% and 10% points), so 0.10 < p < 0.20
    out = stats.diebold_mariano(HAND)
    assert out["n"] == 5 and out["df"] == 4 and out["n_pairs"] == 3
    assert out["mean_d"] == pytest.approx(1.0)
    assert out["gamma0"] == pytest.approx(2.0)
    assert out["gamma1"] == pytest.approx(-0.8)
    assert out["V"] == pytest.approx(1.2)
    assert out["dm"] == pytest.approx(1 / math.sqrt(0.24))
    assert out["dm_hln"] == pytest.approx(math.sqrt(10 / 3))
    assert 0.10 < out["p_value"] < 0.20
    assert out["p_value"] == pytest.approx(0.141927, abs=1e-6)
    assert out["reasons"] == []


def test_a_pair_across_the_gap_would_change_the_answer():
    """If 3 and 5 Dec were wrongly treated as neighbours, g1 would be -1 and V would be 1."""
    same_values_no_gap = series([1, 2, 3, 4, 5], [1, -1, 2, 0, 3])
    wrong = stats.diebold_mariano(same_values_no_gap)
    assert wrong["gamma1"] == pytest.approx(-1.0) and wrong["V"] == pytest.approx(1.0)
    assert stats.diebold_mariano(HAND)["V"] == pytest.approx(1.2)


def test_order_of_the_input_does_not_matter():
    shuffled = HAND.iloc[[3, 0, 4, 2, 1]]
    assert stats.diebold_mariano(shuffled)["dm_hln"] == pytest.approx(math.sqrt(10 / 3))


def test_no_p_value_with_fewer_than_five_nights():
    out = stats.diebold_mariano(series([1, 2, 3, 4], [1, -1, 2, 0]))
    assert out["p_value"] is None and "only 4 test nights (need 5)" in out["reasons"]
    assert stats.block_bootstrap(series([1, 2, 3, 4], [1, -1, 2, 0]))["lo"] is None


def test_no_p_value_when_the_variance_is_not_above_zero():
    out = stats.diebold_mariano(series([1, 2, 3, 4, 5], [0.5] * 5))
    assert out["V"] == 0 and out["dm"] is None and out["p_value"] is None
    assert "the variance estimate V is not above zero" in out["reasons"]


def test_no_p_value_with_fewer_than_three_usable_blocks():
    every_other_day = series([1, 3, 5, 7, 9], [1, -1, 2, 0, 3])
    out = stats.diebold_mariano(every_other_day)
    assert out["V"] > 0 and out["n_pairs"] == 0
    assert out["p_value"] is None and "only 0 usable 2-night blocks (need 3)" in out["reasons"]
    assert stats.block_bootstrap(every_other_day)["lo"] is None


def test_blocks_never_cross_a_gap_or_the_wrap():
    blocks = stats.usable_blocks(HAND.index)
    assert blocks == [(date(2026, 12, 1), date(2026, 12, 2)), (date(2026, 12, 2), date(2026, 12, 3)),
                      (date(2026, 12, 5), date(2026, 12, 6))]   # not (3, 5), not the wrap (6, 1)


def test_bootstrap_interval_uses_only_usable_blocks_and_is_repeatable():
    out = stats.block_bootstrap(HAND, n_boot=2000, seed=1)
    assert out["n_blocks"] == 3 and out["nights_in_blocks"] == 5 and out["reasons"] == []
    assert -1 <= out["lo"] <= out["mean_d"] <= out["hi"] <= 3
    assert stats.block_bootstrap(HAND, n_boot=2000, seed=1) == out


def test_a_night_in_no_block_is_never_drawn():
    """10 Dec has no neighbour, so its large value can never enter a resample."""
    d = series([1, 2, 3, 4, 10], [0, 1, 0, 1, 50])
    out = stats.block_bootstrap(d, n_boot=2000)
    assert out["n_blocks"] == 3 and out["nights_in_blocks"] == 4
    assert out["hi"] <= 1
    assert out["note"].startswith("1 of 5 test nights have no neighbour")
    assert stats.block_bootstrap(HAND)["note"] is None


def test_rounding_rest_in_v_counts_as_zero():
    """Equal non-round differences: V should be 0 but rounding may leave a tiny positive rest."""
    out = stats.diebold_mariano(series([1, 2, 3, 4, 5], [0.1, 0.1, 0.1, 0.1, 0.1 + 1e-16]))
    assert out["dm"] is None and out["p_value"] is None


def test_autocorrelation_uses_only_pairs_the_right_number_of_days_apart():
    # deviations 0, -2, 1, -1, 2 on 1, 2, 3, 5, 6 Dec; lag 2 pairs: (1,3) 0 and (3,5) -1
    assert stats.autocovariance(HAND, 2) == pytest.approx(-1 / 5)
    assert stats.autocorrelation(HAND, 2) == pytest.approx(-0.1)
    assert stats.autocorrelation(HAND, 1) == pytest.approx(-0.4)


def test_night_differences_average_the_points_of_each_night():
    table = pd.DataFrame({
        "night_date": [date(2026, 12, 1)] * 2 + [date(2026, 12, 2)] * 2,
        "y":     [0.0, 0.0, 1.0, 1.0],
        "model": [1.0, -1.0, 1.0, 3.0],     # |errors| 1, 1, 0, 2
        "p0":    [2.0, 0.0, 0.0, 1.0],      # |errors| 2, 0, 1, 0
    })
    d = stats.night_differences(table, "model", "p0")
    assert list(d.index) == [date(2026, 12, 1), date(2026, 12, 2)]
    assert list(d) == [0.0, 0.5]           # (1-2 + 1-0)/2 = 0; (0-1 + 2-0)/2 = 0.5


def test_metrics_by_hand():
    out = stats.metrics([0, 0, 0, 0], [1, -1, 2, 2])
    assert out == {"n": 4, "mae": 1.5, "rmse": math.sqrt(2.5), "bias": 1.0, "r2": None}   # y all equal


def test_r_squared_by_hand():
    # y = 1, 2, 3 (mean 2, spread 1 + 0 + 1 = 2); errors 0.5, -0.5, 0 -> squared sum 0.5
    assert stats.metrics([1, 2, 3], [1.5, 1.5, 3])["r2"] == pytest.approx(1 - 0.5 / 2)
    assert stats.metrics([1, 2, 3], [3, 2, 1])["r2"] == pytest.approx(1 - 8 / 2)       # worse than the mean
    assert stats.metrics([0.1, 0.1, 0.1], [0.2, 0.0, 0.1])["r2"] is None              # no rounding rest


def test_one_value_per_night():
    with pytest.raises(ValueError):
        stats.diebold_mariano(series([1, 1, 2, 3, 4], [0, 1, 2, 3, 4]))
