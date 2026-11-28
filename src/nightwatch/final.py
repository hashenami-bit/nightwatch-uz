"""Final evaluation: `python -m nightwatch.final select|evaluate|report|practice` (protocol C1; D019).

The protocol's four steps at the cut-off, in this order and once only:

  1. `select`   from 08:30 Tashkent on 3 Dec 2026 (the last night's window closes at 08:15 and the
                builder builds it 15 minutes later). Builds the last nights, counts the complete
                calendar nights N, makes the split k = max(5, round(0.2 N)) (the last k nights are
                test) and cross-validates and chooses the model on the TRAINING nights only. Test-night
                outcomes are never loaded (`features.load(conn, "final_training")`). Only when all of
                that has worked does it write `nights.split`, docs/frozen_config.json and section F of
                docs/evaluation_protocol.md.
  2.            (by hand) commit those files with this script, tag the commit `protocol-frozen`, push.
  3. `evaluate` checks that the code is exactly the tagged commit (every needed file committed,
                nothing changed), that the frozen split and training data still match the database,
                and that no final prediction exists yet. Then it fits P0, P1 and the best M1 and M2 on
                the training nights, predicts the test nights ONCE, and stores the predictions
                (purpose final_evaluation) and model_runs rows. Only then are results made, from the
                stored rows: metrics, Q1 to Q4, O3 and O4, robustness, residual checks and charts, in
                results/final/.
  `report`      remakes results/final/ from the stored predictions. It predicts nothing and changes
                nothing in the database, so it is safe after a crash or a fix to the report code.
  `practice`    the same steps on the first 13 complete nights only (allowed during development),
                with the last 4 of them as pretend test nights so that cross-validation can run
                (9 training nights). It writes nothing to the database or to docs/ and its output goes
                to results/practice/ (git-ignored). It is a rehearsal, not part of the evaluation.

`select` and `evaluate` show which database they will use and ask for "yes"; a database whose name
contains "test" is refused.

Wording (C1): this is a retrospective chronological hold-out evaluation. The test predictions are
made after collection from the stored 18:00 snapshots; they were not issued on the original evenings.
With 5 to 7 test nights every p-value and interval is exploratory (C2).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as sps

from . import alerts, charts, config, cv, db, diagnostics, features, models, nights, robustness, stats

ROOT = Path(__file__).resolve().parents[2]
FROZEN_FILE = ROOT / "docs" / "frozen_config.json"
PROTOCOL_FILE = ROOT / "docs" / "evaluation_protocol.md"
FINAL_DIR = ROOT / "results" / "final"
PRACTICE_DIR = ROOT / "results" / "practice"
TAG = "protocol-frozen"
FROZEN_REL = "docs/frozen_config.json"
COMMITTED = (FROZEN_REL, "docs/evaluation_protocol.md", "src/nightwatch/final.py")
PURPOSE = "final_evaluation"
PRACTICE_TEST_NIGHTS = 4      # practice only: 13 nights -> 9 training (CV needs more than 8) + 4 test
O3_TARGET = 0.15              # at least 15% lower MAE than P0 (proposal v3, O3)

WORDING = ("This is a retrospective chronological hold-out evaluation. The final model was selected "
           "after collection ended, and its test predictions were made afterwards from the stored 18:00 "
           "snapshots. These predictions were not issued on the original evenings.")
EXPLORATORY = ("With this few test nights the comparison is exploratory only: the HLN correction and the "
               "bootstrap do not make it a precise evaluation, and autocorrelation estimates from so few "
               "nights cannot show independence (protocol C2).")


# ---------- small helpers ----------

def label(name: str, params: dict) -> str:
    setting = ", ".join(f"{k}={v}" for k, v in params.items())
    return f"{name} ({setting})" if setting else name


def table_hash(table: pd.DataFrame) -> str:
    """SHA-256 of a feature table, rows in a fixed order, values rounded to 4 decimals."""
    cols = ["night_date", "location_id"] + features.NUMERIC + [features.PROVIDER, features.TARGET]
    frame = table[cols].sort_values(["night_date", "location_id"]).copy()
    frame["night_date"] = frame["night_date"].astype(str)
    return hashlib.sha256(frame.round(4).to_csv(index=False).encode()).hexdigest()


def git(*args) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def to_jsonable(value):
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, pd.DataFrame):
        return to_jsonable(value.to_dict("records"))
    if isinstance(value, pd.Series):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return None if np.isnan(value) else float(value)
    return value


# ---------- step 1: split and selection on the training nights ----------

def choose(train: pd.DataFrame) -> dict:
    """Cross-validate every fixed setting on the training nights and apply the choice rule."""
    results = cv.run_grid(train)
    chosen, m1, m2 = cv.choose(results), cv.best(results, "M1"), cv.best(results, "M2")
    return {
        "selected": {"name": chosen.name, "params": chosen.params, "label": label(chosen.name, chosen.params)},
        "best_m1": {"params": m1.params, "cv_mae": m1.cv_mae},
        "best_m2": {"params": m2.params, "cv_mae": m2.cv_mae},
        "cv": [{"model": r.name, "params": r.params, "cv_mae": r.cv_mae, "fold_mae": r.fold_mae}
               for r in results],
        "folds": [{"training_nights": len(t), "last_training_night": t[-1], "validation": v}
                  for t, v in cv.folds(train["night_date"])],
    }


def pearson_q2(train: pd.DataFrame) -> dict:
    """Q2 (exploratory): dew-point gap at 18:00 against the overnight drop, training nights only."""
    gap = train["snap_dewpoint_gap_c"].to_numpy(float)
    drop = (train["snap_temp_c"] - train[features.TARGET]).to_numpy(float)
    if len(gap) < 3 or np.ptp(gap) == 0 or np.ptp(drop) == 0:
        return {"n": len(gap), "r": None, "p_value": None}
    r, p = sps.pearsonr(gap, drop)
    return {"n": len(gap), "nights": int(train["night_date"].nunique()), "r": float(r), "p_value": float(p),
            "limit": "The ten points of one night share its weather, so the location-nights are not "
                     "independent; the p-value is a rough guide only. Correlation, not cause."}


def ready_time() -> datetime:
    """When select may run: the last night's live window is closed AND the builder may build it.

    That is 08:15 + 15 minutes = 08:30 Tashkent on 3 Dec (nights.SETTLE). Before then the last night
    has no complete rows yet, so the split would be made without it.
    """
    return nights.windows(nights.last_included_night()).final_at + nights.SETTLE


def counted_nights(conn) -> list[date]:
    """The complete calendar nights up to the last included night. Reads no temperatures."""
    return [d for d in features.complete_calendar_nights(conn) if d <= nights.last_included_night()]


def select(conn, now: datetime, build: bool = True) -> dict:
    """Protocol C1 steps 1 and 2: the split and the choice. Writes NOTHING; `freeze` writes.

    build: first run the nights builder for the last nights (rows with a split stay frozen). The
    tests turn it off, because their synthetic nights have no hourly data to build from.
    """
    if FROZEN_FILE.exists():
        raise RuntimeError(f"{FROZEN_FILE.name} already exists: selection is made once only")
    if now < ready_time():
        raise PermissionError(f"select runs from {ready_time().astimezone(nights.TZ):%d %b %H:%M} Tashkent, "
                              "after the last night has been built")
    if build:
        nights.build_all(conn, now)
        if nights.finished_nights(now)[-1] != nights.last_included_night():
            raise AssertionError("the last included night has not been built")
    dates = counted_nights(conn)
    training, test = features.final_split(dates, now)
    train, left_out = features.load(conn, "final_training", now)
    if sorted(set(train["night_date"])) != training:
        raise AssertionError("the training table does not hold exactly the training nights")
    chosen = choose(train)
    return {
        "created_at": now,
        "selection_commit": git("rev-parse", "HEAD"),
        "selection_tree_clean": not git("status", "--porcelain", "--untracked-files=no"),
        "last_included_night": nights.last_included_night(),
        "final_collection_utc": config.FINAL_COLLECTION_UTC,
        "n_nights": len(dates), "k_test": len(test),
        "training_nights": training, "test_nights": test,
        "training_rows": len(train), "left_out": left_out,
        "training_table_sha256": table_hash(train),
        "features": features.FEATURES,
        "primary_set": "nights.is_complete AND nights.calendar_night_complete",
        "alert": {"cold_limit_c": alerts.COLD_LIMIT, "categories": "Low > 8 °C, Medium > 5 to 8 °C, High <= 5 °C",
                  "recall_target": alerts.RECALL_TARGET},
        "metrics": "MAE primary, RMSE second check, bias, R-squared; DM with HLN (h=1), Newey-West lag 1 "
                   "over nights exactly one day apart; 2-night circular block bootstrap, 10,000 draws, seed 0",
        "q2": pearson_q2(train),
        **chosen,
    }


def set_split(conn, training: list[date], test: list[date]) -> None:
    """Write train/test to every row of the complete nights. A different earlier split is refused."""
    existing = dict(conn.execute("SELECT DISTINCT night_date, split FROM nights WHERE split IS NOT NULL"))
    wanted = {d: "train" for d in training} | {d: "test" for d in test}
    if existing and existing != wanted:
        raise RuntimeError("nights.split already holds a different split; nothing was changed")
    for split, days in (("train", training), ("test", test)):
        conn.execute("UPDATE nights SET split = %s WHERE night_date = ANY(%s)", (split, days))
    conn.commit()


def protocol_text(cfg: dict) -> str:
    """Section F filled in, built in memory and checked before anything is written."""
    text = PROTOCOL_FILE.read_text()
    fill = {
        "N complete calendar nights, k test nights": f"N = {cfg['n_nights']}, k = {cfg['k_test']}",
        "Training nights (dates) / test nights (dates)":
            f"{', '.join(str(d) for d in cfg['training_nights'])} / {', '.join(str(d) for d in cfg['test_nights'])}",
        "Selected model and settings": cfg["selected"]["label"],
        "Cross-validation MAE of M1 and M2":
            f"M1 {label('M1', cfg['best_m1']['params'])}: {cfg['best_m1']['cv_mae']:.3f} °C; "
            f"M2 {label('M2', cfg['best_m2']['params'])}: {cfg['best_m2']['cv_mae']:.3f} °C",
        "Commit hash of the `protocol-frozen` tag":
            "printed in results/final/report.md and recorded in DECISIONS.md after `evaluate` "
            "(a commit cannot hold its own hash)",
    }
    for row, value in fill.items():
        line = f"| {row} | {value} |"
        text, n = re.subn(rf"^\| {re.escape(row)} \|[^\n]*\|$", lambda _m, line=line: line, text, flags=re.M)
        if n != 1:
            raise RuntimeError(f"section F row not found: {row}")
    status = "**Status: DRAFT (controls fixed).**"
    if status not in text:
        raise RuntimeError("the protocol status line was not found")
    return text.replace(status, f"**Status: FROZEN on {cfg['created_at']:%d %b %Y} (section F filled in).**", 1)


def freeze(conn, cfg: dict) -> None:
    """C1 step 3, before the tag: the split to the database, then docs/frozen_config.json and section F.

    Everything is worked out and checked first, so a failure leaves nothing half-written. If the
    split was written and a file write then fails, run select again: the same split is accepted.
    """
    frozen_text = json.dumps(to_jsonable(cfg), indent=2, ensure_ascii=False) + "\n"
    protocol = protocol_text(cfg)
    set_split(conn, cfg["training_nights"], cfg["test_nights"])
    FROZEN_FILE.write_text(frozen_text)
    PROTOCOL_FILE.write_text(protocol)


# ---------- step 3: checks before the test nights are opened ----------

def frozen_dates(cfg: dict) -> tuple[list[date], list[date]]:
    return ([date.fromisoformat(str(d)) for d in cfg["training_nights"]],
            [date.fromisoformat(str(d)) for d in cfg["test_nights"]])


def check_frozen(conn, cfg: dict) -> str:
    """Refuse unless HEAD is the tagged commit with every needed file committed and unchanged, and
    the database still matches the freeze. Returns the commit."""
    head = git("rev-parse", "HEAD")
    try:
        tagged = git("rev-list", "-n", "1", TAG)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"no tag {TAG!r}: commit and tag the frozen protocol first") from exc
    if head != tagged:
        raise RuntimeError(f"HEAD {head[:7]} is not the {TAG} commit {tagged[:7]}")
    if git("status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("tracked files differ from the tagged commit")
    try:
        git("ls-files", "--error-unmatch", *COMMITTED)
        tagged_config = git("show", f"{TAG}:{FROZEN_REL}")
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("a needed file is not in the tagged commit: " + ", ".join(COMMITTED)) from exc
    if tagged_config.strip() != FROZEN_FILE.read_text().strip():
        raise RuntimeError("docs/frozen_config.json differs from the tagged commit")
    training, test = frozen_dates(cfg)
    split = dict(conn.execute("SELECT DISTINCT night_date, split FROM nights WHERE split IS NOT NULL"))
    if split != {d: "train" for d in training} | {d: "test" for d in test}:
        raise RuntimeError("nights.split in the database does not match docs/frozen_config.json")
    if counted_nights(conn) != training + test:
        raise RuntimeError("the complete nights in the database are not the frozen training + test nights")
    if conn.execute("SELECT count(*) FROM predictions WHERE purpose = %s", (PURPOSE,)).fetchone()[0]:
        raise RuntimeError("final_evaluation predictions already exist: the test nights are scored once only. "
                           "To remake the results from them, run: python -m nightwatch.final report")
    return head


# ---------- step 4: predictions and results ----------

def fit_and_predict(train: pd.DataFrame, test: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame, dict]:
    """Test rows with y, every candidate's prediction and the provider forecast. Fitted on training only."""
    if train["night_date"].max() >= test["night_date"].min():
        raise AssertionError("training nights must all be before the test nights")
    specs = {"P0": {}, "P1": {}, "M1": cfg["best_m1"]["params"], "M2": cfg["best_m2"]["params"]}
    pred = test[["night_id", "night_date", "location_id"]].copy()
    pred["y"] = test[features.TARGET].to_numpy(float)
    pred["provider"] = test[features.PROVIDER].to_numpy(float)
    for name, params in specs.items():
        model = models.make(name, params).fit(train[features.FEATURES], train[features.TARGET])
        pred[name] = np.round(model.predict(test[features.FEATURES]), 2)   # as stored (numeric 5,2)
    pred["pred"] = pred[cfg["selected"]["name"]]      # the selected model
    pred["p0"] = pred["P0"]
    return pred, specs


def store_predictions(conn, pred: pd.DataFrame, specs: dict, version: str, cfg: dict, data_sha: str) -> None:
    for name, params in specs.items():
        for night_id, value in zip(pred["night_id"], pred[name]):
            conn.execute(
                """INSERT INTO predictions (night_id, model_name, protocol_version, purpose, predicted_min_c)
                   VALUES (%s, %s, %s, %s, %s)""",
                (int(night_id), label(name, params), version, PURPOSE, round(float(value), 2)))
        conn.execute(
            """INSERT INTO model_runs (git_commit, data_snapshot_sha256, model_name, parameters, metrics)
               VALUES (%s, %s, %s, %s, %s)""",
            (version, data_sha, name, json.dumps(params),
             json.dumps({"selected": name == cfg["selected"]["name"], "purpose": PURPOSE,
                         "test_rows": len(pred)})))
    conn.commit()


def squared_differences(pred: pd.DataFrame, model: str, comparison: str) -> pd.Series:
    loss = (pred[model] - pred["y"]) ** 2 - (pred[comparison] - pred["y"]) ** 2
    d = loss.groupby(pred["night_date"]).mean().sort_index()
    d.name = "d_squared"
    return d


def comparison(pred: pd.DataFrame, against: str) -> dict:
    """d_t, the DM test and the bootstrap for the selected model against P0 or the provider."""
    d = stats.night_differences(pred, "pred", against)
    d_sq = squared_differences(pred, "pred", against)
    return {"d": d, "dm": stats.diebold_mariano(d), "bootstrap": stats.block_bootstrap(d),
            "autocorrelation": {"lag1": stats.autocorrelation(d, 1), "lag2": stats.autocorrelation(d, 2)},
            "squared_loss": {"d": d_sq, "dm": stats.diebold_mariano(d_sq), "bootstrap": stats.block_bootstrap(d_sq)}}


def results_for(conn, train: pd.DataFrame, test: pd.DataFrame, pred: pd.DataFrame, cfg: dict) -> dict:
    sel = cfg["selected"]
    metrics = {name: stats.metrics(pred["y"], pred[name]) for name in ("P0", "P1", "M1", "M2")}
    metrics["provider forecast (Q3 only)"] = stats.metrics(pred["y"], pred["provider"])
    q1 = comparison(pred, "p0")
    reduction = None if metrics["P0"]["mae"] == 0 else 1 - metrics[sel["name"]]["mae"] / metrics["P0"]["mae"]
    out = {
        "wording": WORDING, "exploratory": EXPLORATORY,
        "selected": sel, "n_test_nights": int(pred["night_date"].nunique()), "test_rows": len(pred),
        "training_rows": len(train),
        "metrics": metrics,
        "q1": q1,
        "o3": {"target_reduction": O3_TARGET, "mae_reduction": reduction,
               "met": None if reduction is None else reduction >= O3_TARGET,
               "interval_of_mean_d": [q1["bootstrap"]["lo"], q1["bootstrap"]["hi"]]},
        "q2": cfg["q2"],
        "q3": comparison(pred, "provider"),
        "q4": {"selected": alerts.alert_scores(pred, "pred"), "p0": alerts.alert_scores(pred, "p0"),
               "by_night": alerts.by_night(pred, "pred")},
        "robustness": {
            "leave_one_point_out": robustness.leave_one_point_out(train, test, sel["name"], sel["params"]),
            "backfill": robustness.count_backfill(conn, min(train["night_date"]), max(test["night_date"])),
            "cold_limits": robustness.cold_limits(pred),
            "without_daytime": (robustness.without_daytime(train, test, sel["name"], sel["params"])
                                if sel["name"] in ("M1", "M2") else None),
            "worst_nights": robustness.worst_nights(pred),
        },
        "diagnostics": {"errors_by_night": diagnostics.errors_by_night(pred),
                        "bias_by_week": diagnostics.bias_by_week(pred),
                        "residual_checks": diagnostics.residual_checks(pred)},
    }
    return out


def draw_charts(conn, train, test, pred, res, out_dir: Path, now: datetime) -> list[str]:
    all_rows = pd.concat([train, test], ignore_index=True)
    figs = {
        "1_night_minimums.png": lambda: charts.night_min_distribution(all_rows, label=" (all complete nights)"),
        "2_dewpoint_gap_vs_drop.png": lambda: charts.dewpoint_gap_vs_drop(train),
        "3_error_by_point.png": lambda: charts.error_by_point(pred, label=" (test nights)"),
        "4_predicted_vs_collected.png": lambda: charts.predicted_vs_collected(pred, label=" (test nights)"),
        "5_completeness.png": lambda: charts.completeness_over_time(charts.completeness_table(conn, now)),
        "6_cold_nights_by_point.png": lambda: charts.cold_nights_by_point(all_rows, label=" (all complete nights)"),
        "7_d_t_vs_p0.png": lambda: charts.night_differences(res["q1"]["d"], "P0"),
        "8_errors_over_time.png": lambda: charts.errors_over_time(res["diagnostics"]["errors_by_night"],
                                                                 label=" (test nights)"),
    }
    saved = []
    for name, make in figs.items():
        try:
            charts.save(make(), out_dir / "figures" / name)
            saved.append(name)
        except Exception as exc:                  # a chart must never stop the results: say so instead
            saved.append(f"{name}: not drawn ({type(exc).__name__}: {exc})")
    return saved


# ---------- the readable report ----------

def fmt(x, digits=2, unit=""):
    return "none" if x is None else f"{x:.{digits}f}{unit}"


def fmt_p(p):
    """'p = 0.067', 'p < 0.001' or 'p not given'."""
    return "p not given" if p is None else "p < 0.001" if p < 0.001 else f"p = {p:.3f}"


def test_lines(title: str, comp: dict, against: str) -> list[str]:
    dm, boot = comp["dm"], comp["bootstrap"]
    interval = "none" if boot["lo"] is None else f"{boot['lo']:+.3f} to {boot['hi']:+.3f} °C"
    lines = [f"### {title}", "",
             f"H0: E[d_t] = 0 (equal expected absolute error). H1: E[d_t] ≠ 0. Two-sided, α = 0.05. "
             f"d_t = mean over the night's points of |model error| − |{against} error|; below 0 means the "
             "model was better.", "",
             "| night | d_t (°C) |", "|---|---|"]
    lines += [f"| {d} | {v:+.3f} |" for d, v in comp["d"].items()]
    lines += ["", f"- Mean d = {fmt(dm['mean_d'], 3)} °C over n = {dm['n']} test nights "
                  f"({dm['n_pairs']} pairs of nights exactly one day apart).",
              f"- DM* (HLN) = {fmt(dm['dm_hln'], 3)}, t with {dm['df']} df, {fmt_p(dm['p_value'])}.",
              f"- 95% block-bootstrap interval of mean d: {interval} ({boot['n_blocks']} usable 2-night blocks).",
              f"- Autocorrelation of d_t: lag 1 {fmt(comp['autocorrelation']['lag1'])}, "
              f"lag 2 {fmt(comp['autocorrelation']['lag2'])}."]
    if dm["reasons"]:
        lines.append(f"- No p-value or interval is given (C2): {'; '.join(dm['reasons'])}. "
                     "The dated table above is the result.")
    if boot.get("note"):
        lines.append(f"- Note: {boot['note']}.")
    sq = comp["squared_loss"]
    lines += [f"- Second check with squared error: mean d = {fmt(sq['dm']['mean_d'], 3)} °C², "
              f"{fmt_p(sq['dm']['p_value'])}.", "", f"*{EXPLORATORY}*", ""]
    return lines


def report_md(res: dict, cfg: dict, title: str, extra: list[str]) -> str:
    sel = res["selected"]
    m = res["metrics"]
    lines = [f"# {title}", "", *extra, "", f"> {WORDING}", "",
             f"Training nights: {len(cfg['training_nights'])} ({res['training_rows']} location-nights). "
             f"Test nights: {res['n_test_nights']} ({res['test_rows']} location-nights): "
             f"{', '.join(str(d) for d in cfg['test_nights'])}.", "",
             f"Selected model: **{sel['label']}** (cross-validation MAE: M1 {cfg['best_m1']['cv_mae']:.3f}, "
             f"M2 {cfg['best_m2']['cv_mae']:.3f} °C; used only for choosing).", "",
             "## Accuracy on the test nights", "",
             "| method | MAE °C | RMSE °C | bias °C | R² |", "|---|---|---|---|---|"]
    for name, v in m.items():
        lines.append(f"| {name}{' (selected)' if name == sel['name'] else ''} | {v['mae']:.3f} | "
                     f"{v['rmse']:.3f} | {v['bias']:+.3f} | {fmt(v['r2'], 3)} |")
    o3 = res["o3"]
    lines += ["", f"**O3** (target: at least 15% lower MAE than P0): reduction "
                  f"{fmt(None if o3['mae_reduction'] is None else 100 * o3['mae_reduction'], 1, '%')}; "
                  f"target {'met' if o3['met'] else 'not met' if o3['met'] is not None else 'not judged'}. "
                  "95% interval of mean d: " + ("none (see Q1)" if o3["interval_of_mean_d"][0] is None else
                  f"{o3['interval_of_mean_d'][0]:+.3f} to {o3['interval_of_mean_d'][1]:+.3f} °C") + ".", ""]
    lines += test_lines("Q1: selected model against persistence P0 (main comparison)", res["q1"], "P0")
    q2 = res["q2"]
    lines += ["### Q2: dew-point gap at 18:00 and the overnight drop (training nights, exploratory)", "",
              f"Pearson r = {fmt(q2['r'], 3)}, {fmt_p(q2['p_value'])}, n = {q2['n']} location-nights. "
              f"{q2.get('limit', '')}", ""]
    lines += test_lines("Q3: selected model against the provider forecast (exploratory)", res["q3"], "provider")
    for who in ("selected", "p0"):
        a = res["q4"][who]
        lines += [f"### Q4 / O4: cold-night alert, {'selected model' if who == 'selected' else 'P0'} "
                  f"(night minimum ≤ {a['limit']:g} °C)", "",
                  f"TP {a['tp']}, FP {a['fp']}, FN {a['fn']}, TN {a['tn']}; precision {fmt(a['precision'])}, "
                  f"recall {fmt(a['recall'])}, F1 {fmt(a['f1'])}. Cold location-nights {a['cold_location_nights']}, "
                  f"cold calendar nights {a['cold_calendar_nights']}. "
                  + (f"Descriptive only: {'; '.join(a['reasons'])}." if a["descriptive_only"]
                     else f"Recall target 0.80 {'met' if a['recall_target_met'] else 'not met'}."), ""]
    r = res["robustness"]
    lines += ["## Robustness (sensitivity only; changes nothing above)", "",
              f"- Back-filled values {r['backfill']['values']}, back-fill runs {r['backfill']['runs']}.",
              "- Leave one point out: model MAE from "
              f"{r['leave_one_point_out']['model_mae'].min():.3f} to {r['leave_one_point_out']['model_mae'].max():.3f} °C.",
              ]
    if r["without_daytime"]:
        lines.append(f"- Without the three daytime features: MAE change "
                     f"{r['without_daytime']['mae_change']:+.3f} °C.")
    lines += ["- Worst nights: " + ", ".join(f"{w.night_date} (MAE {w.model_mae:.2f})"
                                            for w in r["worst_nights"].itertuples()), ""]
    rc = res["diagnostics"]["residual_checks"]
    norm, var, ind = rc["normality"], rc["equal_variance"], rc["independence"]
    lines += ["## Residual checks (rough guide only)", "",
              "- Normality (Shapiro-Wilk): " + ("not computed" if norm is None else
                                                f"W = {norm['w']:.3f}, {fmt_p(norm['p_value'])}"),
              "- Equal variance (Breusch-Pagan, Koenker): " + ("not computed" if var is None else
                                                              f"LM = {var['lm']:.2f}, {fmt_p(var['p_value'])}"),
              f"- Independence (night-mean residual): lag 1 {fmt(ind['lag1'])} "
              f"({ind['pairs_lag1']} pairs), lag 2 {fmt(ind['lag2'])} ({ind['pairs_lag2']} pairs)",
              f"- Limit: {rc['limit']}.", ""]
    return "\n".join(lines) + "\n"


def write_results(res: dict, cfg: dict, out_dir: Path, title: str, extra: list[str],
                  figures: list[str] | None = None) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    tables = out_dir / "tables"
    tables.mkdir(exist_ok=True)
    res["q1"]["d"].rename("d_vs_p0").to_csv(tables / "q1_d_t.csv")
    res["q3"]["d"].rename("d_vs_provider").to_csv(tables / "q3_d_t.csv")
    res["q4"]["by_night"].to_csv(tables / "q4_alerts_by_night.csv", index=False)
    res["robustness"]["leave_one_point_out"].to_csv(tables / "leave_one_point_out.csv", index=False)
    res["diagnostics"]["errors_by_night"].to_csv(tables / "errors_by_night.csv", index=False)
    res["diagnostics"]["bias_by_week"].to_csv(tables / "bias_by_week.csv", index=False)
    (out_dir / "results.json").write_text(json.dumps(to_jsonable({**res, "figures": figures or []}), indent=2,
                                                     ensure_ascii=False) + "\n")
    (out_dir / "report.md").write_text(report_md(res, cfg, title, extra))


def publish(conn, train, test, pred, cfg, out_dir: Path, now: datetime, title: str, extra: list[str]) -> dict:
    """Results first (JSON, tables, report), then the charts, so a chart problem loses nothing."""
    res = results_for(conn, train, test, pred, cfg)
    write_results(res, cfg, out_dir, title, extra)
    figures = draw_charts(conn, train, test, pred, res, out_dir, now)
    write_results(res, cfg, out_dir, title, extra, figures)
    return res


def frozen_tables(conn, cfg: dict, now: datetime) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, list]:
    """The full final table split by the frozen dates; refuses if the training data changed."""
    table, left_out = features.load(conn, "final", now)
    training, test_dates = frozen_dates(cfg)
    train = table[table["night_date"].isin(training)].reset_index(drop=True)
    test = table[table["night_date"].isin(test_dates)].reset_index(drop=True)
    if table_hash(train) != cfg["training_table_sha256"]:
        raise RuntimeError("the training data changed since the freeze")
    return table, train, test, left_out


def stored_predictions(conn, cfg: dict, test: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    """The final_evaluation predictions from the database, in the same shape as fit_and_predict."""
    names = {label("P0", {}): "P0", label("P1", {}): "P1",
             label("M1", cfg["best_m1"]["params"]): "M1", label("M2", cfg["best_m2"]["params"]): "M2"}
    rows = conn.execute("""SELECT night_id, model_name, predicted_min_c, protocol_version FROM predictions
                            WHERE purpose = %s""", (PURPOSE,)).fetchall()
    if not rows:
        raise RuntimeError("no final_evaluation predictions are stored yet: run evaluate first")
    versions = {r[3] for r in rows}
    stored = pd.DataFrame([(r[0], names.get(r[1], r[1]), float(r[2])) for r in rows],
                          columns=["night_id", "model", "value"]).pivot(index="night_id", columns="model",
                                                                         values="value")
    if set(stored.columns) != set(names.values()) or set(stored.index) != set(test["night_id"]):
        raise RuntimeError("the stored predictions do not cover every model and test row")
    pred = test[["night_id", "night_date", "location_id"]].copy()
    pred["y"] = test[features.TARGET].to_numpy(float)
    pred["provider"] = test[features.PROVIDER].to_numpy(float)
    for name in ("P0", "P1", "M1", "M2"):
        pred[name] = stored.loc[pred["night_id"], name].to_numpy(float)
    pred["pred"], pred["p0"] = pred[cfg["selected"]["name"]], pred["P0"]
    return pred, ", ".join(sorted(versions))


def evaluate(conn, now: datetime, out_dir: Path = FINAL_DIR) -> dict:
    """Protocol C1 step 4: once only, from the tagged commit. Predictions are stored before any result;
    the results are then made from the stored rows (the same path as `report`)."""
    cfg = json.loads(FROZEN_FILE.read_text())
    version = check_frozen(conn, cfg)
    table, train, test, left_out = frozen_tables(conn, cfg, now)
    pred, specs = fit_and_predict(train, test, cfg)
    store_predictions(conn, pred, specs, version[:12], cfg, table_hash(table))   # stored BEFORE any result
    return report(conn, now, out_dir, cfg)


def report(conn, now: datetime, out_dir: Path = FINAL_DIR, cfg: dict | None = None) -> dict:
    """Make (or remake) results/final/ from the stored final_evaluation predictions.

    Safe to run again after a crash: it predicts nothing and changes nothing in the database.
    """
    cfg = cfg or json.loads(FROZEN_FILE.read_text())
    _, train, test, left_out = frozen_tables(conn, cfg, now)
    pred, version = stored_predictions(conn, cfg, test)
    code = git("rev-parse", "HEAD")[:12]
    extra = [f"Predictions made once from the `{TAG}` commit {version}. "
             f"This report was made from the stored predictions at commit {code}, "
             f"{now:%d %b %Y %H:%M} UTC."]
    res = publish(conn, train, test, pred, cfg, out_dir, now, "NightWatch UZ: final evaluation", extra)
    res["left_out"] = left_out
    return res


def practice(conn, now: datetime, out_dir: Path = PRACTICE_DIR) -> dict:
    """The whole sequence on the development nights only. Writes nothing to the database."""
    table, left_out = features.load(conn, "development")
    dates = sorted(table["night_date"].unique())
    if len(dates) <= cv.N_FOLDS * cv.FOLD_NIGHTS + PRACTICE_TEST_NIGHTS:
        raise RuntimeError(f"practice needs more than {cv.N_FOLDS * cv.FOLD_NIGHTS + PRACTICE_TEST_NIGHTS} "
                           f"development nights, found {len(dates)}")
    training, test_dates = dates[:-PRACTICE_TEST_NIGHTS], dates[-PRACTICE_TEST_NIGHTS:]
    train = table[table["night_date"].isin(training)].reset_index(drop=True)
    test = table[table["night_date"].isin(test_dates)].reset_index(drop=True)
    cfg = {"training_nights": training, "test_nights": test_dates, "q2": pearson_q2(train), **choose(train)}
    pred, _ = fit_and_predict(train, test, cfg)
    res = publish(conn, train, test, pred, cfg, out_dir, now, "PRACTICE RUN: not the evaluation",
                  ["Practice on the first 13 complete nights only (allowed during development, protocol C1). "
                   f"The last {PRACTICE_TEST_NIGHTS} of them stand in for test nights so that cross-validation "
                   "can run. This is not the protocol split, nothing was written to the database, and these "
                   "numbers are not results."])
    res["left_out"] = left_out
    return res


def confirm(dsn: str, step: str) -> bool:
    """Show which database will be used (never the password) and ask before select or evaluate."""
    import psycopg
    info = psycopg.conninfo.conninfo_to_dict(dsn)
    name, host = info.get("dbname", "?"), info.get("host", "local socket")
    if "test" in name:
        print(f"Refused: database {name!r} looks like a test database.", file=sys.stderr)
        return False
    answer = input(f"Run '{step}' on database {name!r} at {host}? This is done once only. Type yes: ")
    return answer.strip().lower() == "yes"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("step", choices=["select", "evaluate", "report", "practice"])
    parser.add_argument("--out", type=Path, help="output folder (evaluate, report and practice)")
    args = parser.parse_args(argv)
    dsn = db.dsn_from_env_or_secrets()
    if not dsn:
        print("DATABASE_URL is not set (environment or .streamlit/secrets.toml).", file=sys.stderr)
        return 2
    if args.step in ("select", "evaluate") and not confirm(dsn, args.step):
        return 1
    now = datetime.now(timezone.utc)
    with db.connect(dsn) as conn:
        if args.step == "practice":
            res = practice(conn, now, args.out or PRACTICE_DIR)
            print(f"Practice done: {res['n_test_nights']} pretend test nights, model {res['selected']['label']}. "
                  f"See {(args.out or PRACTICE_DIR) / 'report.md'}")
        elif args.step == "select":
            cfg = select(conn, now)
            freeze(conn, cfg)
            print(f"Split: {cfg['n_nights']} complete nights, last {cfg['k_test']} are test. "
                  f"Selected on training nights only: {cfg['selected']['label']}.\n"
                  f"Next: commit {', '.join(COMMITTED)}, tag the commit '{TAG}', push, "
                  "then run: python -m nightwatch.final evaluate")
        elif args.step == "evaluate":
            res = evaluate(conn, now, args.out or FINAL_DIR)
            print(f"Final evaluation done: {res['n_test_nights']} test nights. "
                  f"See {(args.out or FINAL_DIR) / 'report.md'}")
        else:
            res = report(conn, now, args.out or FINAL_DIR)
            print(f"Results remade from the stored predictions. See {(args.out or FINAL_DIR) / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
