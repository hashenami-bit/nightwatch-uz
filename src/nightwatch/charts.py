"""Report charts (proposal v3 section 8 lists at least five; DECISIONS.md D017).

Each function takes a table and returns a matplotlib figure; `save` writes it as a PNG. Nothing here
reads the database except `completeness_table`, which reads counts only, no temperatures.

Which nights go into which chart is the caller's job and follows protocol C1: charts with outcomes
of test nights are made only after the protocol freeze. The dew-point chart (Q2) uses training
nights only.

Style (static report figures): a light surface, recessive hairline grid, blue for the model, orange
for persistence P0, a blue/red pair for "model better / P0 better", text in gray inks, never in a
series color. Real calendar dates are kept on time axes, so missing nights show as gaps.
"""
from __future__ import annotations

from datetime import timedelta
import sys
from pathlib import Path

import matplotlib

if "matplotlib.pyplot" not in sys.modules:   # files only, no window; leave a running notebook alone
    matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from . import alerts, config  # noqa: E402

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e5e4e0"
MODEL = "#2a78d6"          # blue, categorical slot 1
P0 = "#eb6834"             # orange, categorical slot 2
BETTER = "#2a78d6"         # diverging pole: model better (d < 0)
WORSE = "#e34948"          # diverging pole: P0 better (d > 0)
TIE = "#b7b6b0"            # neutral gray: d = 0
NAMES = {loc: name for loc, name, *_ in config.LOCATIONS}
COLD = alerts.COLD_LIMIT


def _figure(title: str, subtitle: str | None = None, size=(7.5, 4.2)):
    fig, ax = plt.subplots(figsize=size, dpi=150)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_2, labelsize=8.5, length=0)
    ax.grid(True, color=GRID, linewidth=0.8, linestyle="-")
    ax.set_axisbelow(True)
    fig.suptitle(title, x=0.015, ha="left", fontsize=11.5, color=INK, fontweight="bold")
    if subtitle:
        ax.set_title(subtitle, loc="left", fontsize=8.5, color=INK_2, pad=8)
    return fig, ax


def _need(rows, what: str = "rows"):
    """A clear error instead of a matplotlib crash when there is nothing to draw."""
    if len(rows) == 0:
        raise ValueError(f"no {what} to draw")


def _labels(ax, x: str, y: str):
    ax.set_xlabel(x, color=INK_2, fontsize=9)
    ax.set_ylabel(y, color=INK_2, fontsize=9)


def _cold_line(ax, axis="y", label=True):
    line = ax.axhline if axis == "y" else ax.axvline
    line(COLD, color=INK_2, linewidth=1, zorder=3)          # drawn over the bars
    if label:
        if axis == "y":
            ax.annotate(f"cold limit {COLD:g} °C", xy=(1, COLD), xycoords=("axes fraction", "data"),
                        xytext=(-2, 3), textcoords="offset points", ha="right", fontsize=8, color=INK_2)
        else:
            ax.annotate(f"cold limit {COLD:g} °C", xy=(COLD, 1), xycoords=("data", "axes fraction"),
                        xytext=(3, -10), textcoords="offset points", ha="left", fontsize=8, color=INK_2)


def _date_axis(ax, dates):
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=max(1, len(dates) // 10 or 1)))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    first, last = min(dates), max(dates)
    ax.set_xlim(pd.Timestamp(first) - timedelta(hours=18), pd.Timestamp(last) + timedelta(hours=18))


def save(fig, path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, facecolor=SURFACE, bbox_inches="tight")
    plt.close(fig)
    return path


# ---------- 1. distribution of night minimums ----------

def night_min_distribution(table: pd.DataFrame, target: str = "target_min_c", label: str = "") -> plt.Figure:
    _need(table, "location-nights")
    y = table[target].to_numpy(dtype=float)
    fig, ax = _figure("Night minimum temperature",
                      f"{len(y)} location-nights{label}; 1 °C bins")
    bins = np.arange(np.floor(y.min()), np.floor(y.max()) + 2, 1.0)   # at least one 1 °C bin
    ax.hist(y, bins=bins, color=MODEL, edgecolor=SURFACE, linewidth=1.5, zorder=2)
    ax.set_ylim(0, ax.get_ylim()[1] * 1.12)                 # room for the limit label
    _cold_line(ax, axis="x")
    _labels(ax, "night minimum (°C)", "location-nights")
    ax.grid(axis="x", visible=False)
    return fig


# ---------- 2. dew-point gap against overnight drop (Q2, training nights only) ----------

def dewpoint_gap_vs_drop(table: pd.DataFrame, label: str = " (training nights)") -> plt.Figure:
    _need(table, "location-nights")
    gap = table["snap_dewpoint_gap_c"].to_numpy(dtype=float)
    drop = (table["snap_temp_c"] - table["target_min_c"]).to_numpy(dtype=float)
    fig, ax = _figure("Dew-point gap at 18:00 against the overnight drop",
                      f"{len(gap)} location-nights{label}; drop = snapshot temperature − night minimum")
    ax.scatter(gap, drop, s=22, color=MODEL, alpha=0.75, edgecolors=SURFACE, linewidths=0.8, zorder=2)
    _labels(ax, "snapshot dew-point gap (°C)", "overnight drop (°C)")
    return fig


# ---------- 3. error by point ----------

def error_by_point(pred: pd.DataFrame, label: str = "") -> plt.Figure:
    _need(pred, "test rows")
    points = sorted(pred["location_id"].unique())
    data = [(pred.loc[pred["location_id"] == p, "pred"] - pred.loc[pred["location_id"] == p, "y"]).to_numpy()
            for p in points]
    fig, ax = _figure("Prediction error by monitored point",
                      f"selected model{label}; error = predicted − collected minimum", size=(8, 4.2))
    box = ax.boxplot(data, patch_artist=True, widths=0.45, medianprops={"color": INK, "linewidth": 1.5},
                     whiskerprops={"color": INK_2}, capprops={"color": INK_2},
                     flierprops={"marker": "o", "markersize": 4, "markerfacecolor": MODEL,
                                 "markeredgecolor": SURFACE})
    for patch in box["boxes"]:
        patch.set(facecolor="#cde2fb", edgecolor=MODEL, linewidth=1)
    ax.set_xticks(range(1, len(points) + 1), [NAMES.get(p, str(p)) for p in points], rotation=30, ha="right")
    ax.axhline(0, color=INK_2, linewidth=1, zorder=1)
    _labels(ax, "", "error (°C)")
    ax.grid(axis="x", visible=False)
    return fig


# ---------- 4. predicted against collected ----------

def predicted_vs_collected(pred: pd.DataFrame, label: str = "") -> plt.Figure:
    _need(pred, "test rows")
    fig, ax = _figure("Predicted against collected night minimum",
                      f"selected model{label}; the diagonal is a perfect prediction", size=(5.6, 5.2))
    lo = float(min(pred["y"].min(), pred["pred"].min())) - 1
    hi = float(max(pred["y"].max(), pred["pred"].max())) + 1
    ax.plot([lo, hi], [lo, hi], color=INK_2, linewidth=1, zorder=1)
    ax.axhline(COLD, color="#b7b6b0", linewidth=1, zorder=1)    # the cold limit on both axes
    ax.axvline(COLD, color="#b7b6b0", linewidth=1, zorder=1)
    ax.scatter(pred["y"], pred["pred"], s=24, color=MODEL, alpha=0.8, edgecolors=SURFACE, linewidths=0.8, zorder=2)
    ax.set_xlim(lo, hi)
    ax.set_ylim(lo, hi)
    ax.set_aspect("equal")
    ax.annotate(f"{COLD:g} °C", xy=(COLD, hi), xytext=(3, -10), textcoords="offset points", fontsize=8, color=INK_2)
    _labels(ax, "collected night minimum (°C)", "predicted night minimum (°C)")
    return fig


# ---------- 5. collector completeness over time ----------

def completeness_table(conn) -> pd.DataFrame:
    """Complete points per night and whether the calendar night is complete. Counts only."""
    rows = conn.execute(
        """SELECT night_date, count(*) FILTER (WHERE is_complete), bool_or(calendar_night_complete)
             FROM nights GROUP BY night_date ORDER BY night_date""").fetchall()
    return pd.DataFrame(rows, columns=["night_date", "complete_points", "calendar_complete"])


def completeness_over_time(counts: pd.DataFrame) -> plt.Figure:
    _need(counts, "nights")
    dates = list(counts["night_date"])
    n_complete = int(counts["calendar_complete"].fillna(False).sum())
    fig, ax = _figure("Complete points per night",
                      f"{n_complete} complete calendar nights of {len(dates)} nights built; "
                      "the line marks 8 of 10 points, needed for a complete night; gray = not complete",
                      size=(8.5, 4))
    colors = [MODEL if c else "#b7b6b0" for c in counts["calendar_complete"].fillna(False)]
    ax.bar(pd.to_datetime(dates), counts["complete_points"], width=0.7, color=colors, zorder=2)
    ax.axhline(8, color=INK_2, linewidth=1, zorder=3)
    ax.set_ylim(0, 10.5)
    ax.set_yticks(range(0, 11, 2))
    _date_axis(ax, dates)
    _labels(ax, "night (starting evening)", "complete points")
    ax.grid(axis="x", visible=False)
    return fig


# ---------- 6. cold nights by point ----------

def cold_nights_by_point(table: pd.DataFrame, target: str = "target_min_c", label: str = "") -> plt.Figure:
    _need(table, "location-nights")
    cold = (table[target] <= COLD).groupby(table["location_id"]).sum()
    cold = cold.reindex([loc for loc, *_ in config.LOCATIONS], fill_value=0)
    fig, ax = _figure("Cold nights by monitored point",
                      f"night minimum at or below {COLD:g} °C{label}", size=(8, 4.2))
    bars = ax.bar([NAMES[p] for p in cold.index], cold.to_numpy(), width=0.55, color=MODEL, zorder=2)
    ax.bar_label(bars, fontsize=8, color=INK_2, padding=2)
    ax.tick_params(axis="x", rotation=30)
    for tick in ax.get_xticklabels():
        tick.set_ha("right")
    ax.yaxis.get_major_locator().set_params(integer=True)
    _labels(ax, "", "cold location-nights")
    ax.grid(axis="x", visible=False)
    return fig


# ---------- 7. d_t in date order ----------

def night_differences(d: pd.Series, comparison: str = "P0") -> plt.Figure:
    """d_t per test night on its real date; below 0 the model was better, gray = a tie."""
    _need(d, "test nights")
    dates = list(d.index)
    values = d.to_numpy(dtype=float)
    fig, ax = _figure(f"Nightly difference in absolute error, model − {comparison}",
                      f"{len(d)} test nights; below 0 = model better, above 0 = {comparison} better; "
                      f"mean {values.mean():+.2f} °C", size=(7.5, 4))
    ax.bar(pd.to_datetime(dates), values, width=0.6, zorder=2,
           color=[BETTER if v < 0 else WORSE if v > 0 else TIE for v in values])
    ax.axhline(0, color=INK_2, linewidth=1, zorder=1)
    low, high = min(0.0, values.min()), max(0.0, values.max())
    pad = 0.12 * (high - low or 1.0)
    ax.set_ylim(low - pad, high + pad)                      # 0 and every bar inside, with air
    ax.axhline(values.mean(), color=INK, linewidth=1, linestyle=(0, (1, 2)), zorder=3)
    ax.annotate("mean", xy=(1, values.mean()), xycoords=("axes fraction", "data"), xytext=(-2, 3),
                textcoords="offset points", ha="right", fontsize=8, color=INK_2)
    _date_axis(ax, dates)
    _labels(ax, "test night", "d_t (°C)")
    ax.grid(axis="x", visible=False)
    return fig


# ---------- 8. errors in date order ----------

def errors_over_time(by_night: pd.DataFrame, label: str = "") -> plt.Figure:
    """Nightly MAE of the model and of P0 on the real dates (diagnostics.errors_by_night)."""
    _need(by_night, "test nights")
    # every calendar day on the axis, so a missing night breaks the line instead of being bridged
    frame = by_night.set_index(pd.to_datetime(list(by_night["night_date"])))
    frame = frame.reindex(pd.date_range(frame.index.min(), frame.index.max(), freq="D"))
    fig, ax = _figure("Nightly mean absolute error in date order",
                      f"selected model and persistence P0{label}; a break in a line is a missing night",
                      size=(7.5, 4))
    for column, color, name in (("model_mae", MODEL, "model"), ("p0_mae", P0, "P0")):
        ax.plot(frame.index, frame[column], color=color, linewidth=2, marker="o", markersize=5,
                markeredgecolor=SURFACE, markeredgewidth=1.5, label=name, zorder=2)
        ax.annotate(name, xy=(frame.index[-1], frame[column].iloc[-1]), xytext=(6, 0),
                    textcoords="offset points", va="center", fontsize=8.5, color=INK_2)
    ax.legend(frameon=False, fontsize=8.5, labelcolor=INK_2, loc="upper left")
    ax.set_ylim(bottom=0)
    _date_axis(ax, list(by_night["night_date"]))
    _labels(ax, "night (starting evening)", "MAE (°C)")
    return fig
