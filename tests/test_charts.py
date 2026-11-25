"""Tests for the report charts (DECISIONS.md D017). Synthetic data; each chart is drawn and saved."""
from datetime import date, timedelta

import pandas as pd
import pytest

from nightwatch import charts, diagnostics, robustness, stats
from tests.fixtures.synthetic import store_synthetic_nights, synthetic_nights, synthetic_table

DATES = [date(2026, 11, 12) + timedelta(days=i) for i in range(16)]
DATES = [d for d in DATES if d not in (date(2026, 11, 20), date(2026, 11, 21))]
TABLE = synthetic_table(DATES, seed=2)
TRAIN, TEST = TABLE[TABLE["night_date"] < DATES[-5]], TABLE[TABLE["night_date"] >= DATES[-5]]
PRED = robustness.predictions(TRAIN, TEST, "M1", {"alpha": 1.0})


def figures():
    counts = pd.DataFrame({"night_date": DATES, "complete_points": [10] * len(DATES),
                           "calendar_complete": [True] * len(DATES)})
    return {
        "night_min_distribution": charts.night_min_distribution(TRAIN),
        "dewpoint_gap_vs_drop": charts.dewpoint_gap_vs_drop(TRAIN),
        "error_by_point": charts.error_by_point(PRED),
        "predicted_vs_collected": charts.predicted_vs_collected(PRED),
        "completeness_over_time": charts.completeness_over_time(counts),
        "cold_nights_by_point": charts.cold_nights_by_point(TABLE),
        "night_differences": charts.night_differences(stats.night_differences(PRED, "pred", "p0")),
        "errors_over_time": charts.errors_over_time(diagnostics.errors_by_night(PRED)),
    }


def test_every_chart_is_drawn_and_saved(tmp_path):
    made = figures()
    assert len(made) == 8                                          # the proposal asks for at least five
    for name, fig in made.items():
        assert fig._suptitle.get_text()                            # every chart has a title
        path = charts.save(fig, tmp_path / f"{name}.png")
        assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" and path.stat().st_size > 10_000


def test_box_plot_has_one_box_per_point_with_names():
    fig = charts.error_by_point(PRED)
    labels = [t.get_text() for t in fig.axes[0].get_xticklabels()]
    assert labels[:3] == ["Tashkent", "Samarkand", "Bukhara"] and len(labels) == 10


def test_night_differences_colour_model_better_blue_and_p0_better_red():
    d = pd.Series([-1.0, 0.5], index=[date(2026, 12, 1), date(2026, 12, 3)])
    bars = charts.night_differences(d).axes[0].patches
    assert len(bars) == 2
    assert [b.get_facecolor()[:3] for b in bars] == [
        pytest.approx(tuple(int(charts.BETTER[i:i + 2], 16) / 255 for i in (1, 3, 5))),
        pytest.approx(tuple(int(charts.WORSE[i:i + 2], 16) / 255 for i in (1, 3, 5)))]


def test_a_tie_is_gray_and_empty_input_gives_a_clear_error():
    d = pd.Series([0.0], index=[date(2026, 12, 1)])
    bar = charts.night_differences(d).axes[0].patches[0]
    assert bar.get_facecolor()[:3] == pytest.approx(tuple(int(charts.TIE[i:i + 2], 16) / 255 for i in (1, 3, 5)))
    with pytest.raises(ValueError, match="no nights to draw"):
        charts.completeness_over_time(pd.DataFrame(columns=["night_date", "complete_points", "calendar_complete"]))
    one = TRAIN.head(1).assign(target_min_c=5.0)
    assert len(charts.night_min_distribution(one).axes[0].patches) == 1          # one whole-degree value: one bin


def test_errors_over_time_breaks_the_line_at_a_missing_night():
    by_night = diagnostics.errors_by_night(PRED)
    by_night = by_night[by_night["night_date"] != by_night["night_date"].iloc[2]]
    line = charts.errors_over_time(by_night).axes[0].get_lines()[0]
    assert pd.isna(line.get_ydata()).sum() == 1                    # the gap is drawn as a gap


def test_completeness_table_reads_counts_only(conn):
    store_synthetic_nights(conn, synthetic_nights(DATES[:3]), incomplete=[(DATES[1], 2), (DATES[1], 5)],
                           calendar_incomplete=[DATES[2]])
    counts = charts.completeness_table(conn)
    assert list(counts.columns) == ["night_date", "complete_points", "calendar_complete"]
    assert counts["complete_points"].tolist() == [10, 8, 10]
    assert counts["calendar_complete"].tolist() == [True, True, False]
