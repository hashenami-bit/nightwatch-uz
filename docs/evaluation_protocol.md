# Evaluation protocol

**Status: DRAFT (controls fixed).** Sections C1–C4 are fixed now, as required by the Module Leader's
approval of 28 Oct 2026 (`docs/approval/2026-10-28-module-leader-approval.md`). Section F is filled in
once, after model selection at the data cut-off. That commit is tagged `protocol-frozen` before any
final test prediction is made. Nothing in this file changes after the tag.

Approved design: complete proposal v3 (28 Oct 2026), a small exploratory study on prospectively
collected weather-model data for ten fixed monitored points.

## C1 · Final evaluation: what it is and how it is protected

- **Name.** The final evaluation is a **retrospective chronological hold-out evaluation**. The final
  model is selected after collection ends and its test predictions are made afterwards from the stored
  18:00 snapshots. These predictions were **not issued on the original evenings**. The report and the
  dashboard use exactly this wording.
- **During collection**, development analysis (exploration, baselines, candidates, cross-validation)
  uses only the **first 13 complete calendar nights**. Under the split rule these are always training
  nights.
- **At the fixed cut-off (C3)**, in this order and once only:
  1. compute N and the final split, k = max(5, round(0.2 × N)): the last k complete nights are test,
     all earlier complete nights are training;
  2. select settings and the model, and fit it, on **training nights only**;
  3. complete section F, commit, and tag `protocol-frozen`;
  4. run the single script that makes the final test predictions (`purpose = final_evaluation`)
     and only then open the test results.
- **Dashboard.** Predictions shown during collection are `provisional_dashboard` and are never used in
  the evaluation. The dashboard shows **no accuracy figures for nights after the first 13 complete
  nights**, and provisional performance on possible test nights is never used to change features,
  candidates or thresholds.
- **Cross-validation timing.** In every fold, preprocessing is fitted and training targets are used only
  if they were available **before the validation night's 18:00 cut-off**. A night D target is complete at
  08:15 on D+1 (07:00 value plus the 75-minute window), so a fold may train only on nights with
  date < the first validation night. Folds are whole nights in date order (4 folds × 2 nights at the
  end of the training period).

## C2 · Statistics proportionate to the sample

- **Main comparison: Q1** (selected model against persistence P0). Q2 and Q3 are exploratory.
- 5–7 test nights support **exploratory comparisons only**. The HLN correction and the bootstrap do not
  make this a precise evaluation, and autocorrelation estimates from so few nights cannot show
  independence. The report says this next to every p-value and interval.
- **Calendar dates are kept.** Each night-level difference d_t is stored with its real calendar date.
  Missing nights stay missing: they are never removed and the remaining dates are never treated as
  consecutive.
- **Diebold–Mariano implementation (exact).**
  - Loss: absolute error; d_t = mean over complete points of |model error| − |comparison error|.
  - Forecast horizon: **h = 1** (one forecast per night, for that night).
  - Statistic: DM = mean(d) / sqrt(V / n), where n is the number of test nights.
  - Variance V: Newey–West with Bartlett weights and **one lag**, written in our own code so that the
    lag-1 autocovariance uses **only pairs of test nights exactly one calendar day apart**. Pairs across a
    gap are not used. V = γ0 + 2 · (1/2) · γ1, with γ1 = (1/n) · Σ over valid pairs of
    (d_t − mean)(d_{t+1} − mean).
  - Small-sample correction (Harvey–Leybourne–Newbold, h = 1): DM* = DM · sqrt((n − 1) / n),
    compared with a t distribution with n − 1 degrees of freedom, two-sided, α = 0.05.
  - The code and a unit test with a hand-worked example are in the repository before the freeze.
- **Bootstrap.** Circular moving blocks of 2 nights, where a block may only join two nights exactly one
  calendar day apart; blocks never cross a gap.
- **When no p-value or interval is given.** If n < 5, if V ≤ 0, or if fewer than 3 usable 2-night
  blocks exist, no DM p-value or bootstrap interval is reported. Instead the nightly paired differences
  are shown in a dated table and chart, with the limitation explained.
- **Always reported**, whether or not O3 and O4 are met: MAE, RMSE and bias for every candidate and
  baseline, every d_t, and the alert counts (confusion matrix by location-night and table by calendar
  night).

## C3 · Data cut-off

- The cut-off is recorded in `DECISIONS.md` (D006) **before assessed collection starts** and is never
  moved because of favourable or unfavourable results.
- Two separate values:
  - **Last included night:** date D_last, the night from 20:00 on D_last to 07:00 on D_last + 1.
  - **Final collection time:** 08:15 on D_last + 1 (Asia/Tashkent), the end of the 75-minute window
    for the last 07:00 value.
- It is checked against the published LMS report deadline so that enough time remains for analysis,
  reproducibility checks and the report.
- **Fixed on 28 Oct 2026 (D006): last included night 30 Nov 2026; final collection time 1 Dec 2026,
  08:15 Asia/Tashkent.** LMS report deadline: 14 Dec 2026.
- Note: under the 20% rule, extra nights mainly add training nights; the test count grows only
  slowly (k = 5 up to N = 27, then 6 up to N = 32).

## C4 · Completeness and reproducibility

- The 90% completeness figure is a **planning assumption**. The real coverage of the first three nights
  is measured from `collection_runs` and reported at Checkpoint 1.
- Failed runs and missing schedule slots are kept in `collection_runs` and `data_quality_log`; nothing is
  deleted. GitHub says scheduled jobs can be delayed or dropped, so the extra runs and any backup
  trigger reduce the risk but do not guarantee collection.
- The 12-of-12 target rule and the receipt-time checks stay as approved.
- **Record types are kept apart:** back-filled values (`is_backfill`), manual runs
  (`collection_runs.trigger = 'manual'`, labelled in every count) and synthetic data (only in the test
  database `nightwatch_test`; the command line cannot write a `test` run to the real database).
- **Kept in the assessed repository:** a lawful snapshot of the collected data (Open-Meteo, CC BY 4.0,
  with credit), the raw responses with SHA-256 hashes, the fixed coordinates and query settings
  (`src/nightwatch/config.py`), the tests, and step-by-step instructions to restore the snapshot into a
  local PostgreSQL.

## F · Frozen configuration (filled in once at the cut-off)

| Item | Value |
|---|---|
| Last included night / final collection time | 30 Nov 2026 / 1 Dec 2026 08:15 Asia/Tashkent (fixed 28 Oct, D006) |
| N complete calendar nights, k test nights | |
| Training nights (dates) / test nights (dates) | |
| Selected model and settings | |
| Cross-validation MAE of M1 and M2 | |
| Feature list | as in proposal v3, section 8 |
| Commit hash of the `protocol-frozen` tag | |
