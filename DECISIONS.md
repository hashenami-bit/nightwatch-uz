# Decision records

Each record: date, decision, reason, and the approval it depends on.

## D001 · 2026-10-22 · Scope follows proposal v2 plus v3 amended sections

- Proposal v2 sent 21 Oct 2026; Module Leader decision 21–22 Oct: Revisions required (3 points).
- Proposal v3 (amended sections) drafted 22 Oct 2026 but not sent then. Updated 28 Oct 2026 for the later
  start: minimum target 18 complete calendar nights (aim 24+), no separate validation
  split (test = last k = max(5, round(0.2N)) nights, selection by 4 × 2-night expanding-window CV inside
  training), three daytime features (07–16 max, 00–07 min, 12–16 mean cloud cover; live values received
  before 18:00 only). File: `../CW2 Proposals/4 - UDE5014CM CW2 - Proposal v3 (amended sections) - 28 Oct.docx`.
- The same file has a section B that aligns the untouched v2 text: O3 uses MAE (RMSE second), ~51 calls/day,
  forecast retrieval time, min 18 nights, no validation split, LO5 wording (Diebold–Mariano), and the 16:00/20:00
  cut-off robustness check replaced by an ablation without the daytime features.
- Sent on 28 Oct 2026 as one complete document that merges v2 and all v3 changes:
  `../CW2 Proposals/5 - UDE5014CM CW2 - Project Proposal v3 (complete) - 28 Oct.docx`.
  **Approved on 28 Oct 2026, see D005.**

## D002 · 2026-10-22 · Real collection stays off until written approval

- The collector refuses to run unless `COLLECTION_ENABLED=true`, and the GitHub schedule is commented out.
- Reason: the proposal and the Module Leader's email say assessed collection starts only after approval.
- Before approval: synthetic test data only, plus format checks with `--dry-run` that store nothing.
- Approval arrived 28 Oct 2026 (D005). Collection is switched on only after the data cut-off is recorded (D006).

## D003 · 2026-10-22 · Implementation details inside the accepted design

- Extra tables `raw_responses` (raw JSON + SHA-256, for provenance) and `current_conditions`
  (the current-conditions block used for the 18:00 feature snapshot). No change to scope.
- One request covers all ten points; times are requested in UTC as Unix time to avoid time-zone parsing errors.
- The collector uses psycopg directly; SQLAlchemy and pandas come in with the analysis pipeline.
- Python 3.12 managed with uv; the lock file `uv.lock` pins every version.

## D004 · 2026-10-28 · Collector asks for 4 past hours

- `PAST_HOURS` 2 → 4 so the snapshot response holds the hours needed for the 3-hour trend feature.
  Only live and forecast values are stored as rows; the extra hours stay in the raw JSON payload.
- pytest now also adds `src` to the path, because macOS can mark the editable `.pth` file hidden and Python skips it.

## D005 · 2026-10-28 · Module Leader approval: Approved

- Decision: **Approved**, by University email from Sanjar Bekov on 28 Oct 2026, for the complete proposal v3.
- Approved as a small exploratory study; minimum 18 complete calendar nights, aim 24 or more.
- Exact wording: `docs/approval/2026-10-28-module-leader-approval.md`. The original email stays in the
  University mailbox and is cited in the Checkpoint 1 evidence.
- His four implementation requirements are recorded as controls in `docs/evaluation_protocol.md`
  (sections C1–C4) and summarised in the README. No further proposal rewrite is required.
- Follow-up in the code, done the same day: the `nights` table allows only `train` or `test` splits and
  holds the three daytime features; a complete night must have all three.

## D006 · 2026-10-28 · Data cut-off fixed: last night 30 Nov, collection ends 1 Dec 08:15

- Required by approval point 3: record the last included night and the final collection time
  **before** assessed collection starts, and never move them because of results.
- **Last included night:** 30 November 2026 (20:00 on 30 Nov to 07:00 on 1 Dec, Asia/Tashkent).
- **Final collection time:** 1 December 2026, 08:15 Asia/Tashkent (03:15 UTC). The collector is
  switched off after this time; later responses are never used.
- Checked against the published LMS deadline for the final report: **14 December 2026**. This leaves
  13 days for the frozen protocol, final predictions, reproducibility checks, the report and viva
  preparation. 30 Nov is 4 days after the internal 26 Nov target ("a few days later", approval point 3).
  A later date (last night 4 Dec) was considered and rejected because it would leave only 9 days.
- **First counted night:** the first night D whose whole day D was collected (the daytime features need
  values from 00:00 on D). Expected 29 Oct 2026 if the collector is switched on during 28 Oct; recorded
  in this file when collection starts.
- **Expected split counts** (k = max(5, round(0.2 × N))): from 29 Oct there are 33 calendar nights.
  At 90% about N = 30 → 24 training / 6 test; at 70% about N = 23 → 18 training / 5 test. The minimum of
  18 complete nights is reached even at about 55% completeness.
