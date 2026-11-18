# NightWatch UZ

UDE5014CM Data Science for Developers, Assessment 2 (individual project).

NightWatch UZ collects hourly weather-model values for ten fixed monitored points in
Uzbekistan, predicts the lowest temperature of the coming night from data available at
18:00 (Asia/Tashkent), and shows a general cold-night alert. It does not measure real farm
conditions and it is not a frost warning.

## Status

**Approved on 28 Oct 2026** by the Module Leader as a small exploratory study
(`docs/approval/2026-10-28-module-leader-approval.md`). Data cut-off fixed before collection (`DECISIONS.md`, D006):
last included night **30 Nov 2026**, final collection **1 Dec 2026 08:15** Asia/Tashkent. Real collection
starts once the hosted database is connected. Until then the
code is tested with synthetic data only (`tests/fixtures`).
See `DECISIONS.md` for the approval record and every later change.

## Evaluation controls (from the approval)

The full rules are in `docs/evaluation_protocol.md`. In short:

- The final result is a **retrospective chronological hold-out evaluation**: the model is chosen after
  collection and its test predictions are made from stored 18:00 snapshots. They were not issued on
  the original evenings.
- While collecting, development analysis uses only the first 13 complete nights. At the cut-off the
  split is computed once, the model is chosen and fitted on training nights only, and the protocol is
  frozen (git tag `protocol-frozen`) before any test result is opened.
- Q1 (model against persistence) is the main comparison; Q2 and Q3 are exploratory. 5–7 test nights
  support exploratory conclusions only. Real calendar dates are kept: missing nights are never closed
  up, and the variance and bootstrap blocks never cross a gap.
- The data cut-off (last included night and final collection time) is fixed before collection starts
  and never moved because of results.
- Coverage of the first three nights is measured and reported. Failed and missing runs are kept.
  Back-filled, manual and synthetic records are kept apart.

## What is built so far

| Part | Where |
|---|---|
| PostgreSQL schema with constraints (proposal v2 section 7 + v3 amendments) | `db/schema.sql` |
| Collector: one request for all ten points, validation, live-window rule, run log, raw payload with SHA-256 | `src/nightwatch/collector.py` |
| Database writes (parameterised SQL, duplicates skipped) | `src/nightwatch/db.py` |
| Command line with an off switch | `src/nightwatch/collect.py` |
| Nights builder: one row per point per night, complete-night rules, coverage report (D010) | `src/nightwatch/nights.py`, `.github/workflows/nights.yml` |
| Missing-run check: the timer's slots compared with `collection_runs` (D011) | `src/nightwatch/slots.py` |
| Hourly schedule (on since 28 Oct, D007) with an external backup timer (D008) | `.github/workflows/collect.yml`, `docs/backup_timer.md` |
| Tests on every push | `.github/workflows/tests.yml`, `tests/` |

## Run the tests

Needs [uv](https://docs.astral.sh/uv/) and a local PostgreSQL (the database tests create and
drop a database called `nightwatch_test`).

```bash
uv sync
uv run pytest -v
```

Check the live API format without storing anything:

```bash
uv run python -m nightwatch.collect --dry-run
```

Build the nights table and print the coverage report (needs `DATABASE_URL`; `--report-only`
stores nothing):

```bash
uv run python -m nightwatch.nights
```

Compare the timer's slots with the stored runs and log every missing run once:

```bash
uv run python -m nightwatch.slots
```

## Timing rules (proposal v3, section 3)

- Cut-off: 18:00 local (13:00 UTC). Only responses **received** by then count for that night.
- Live value for hour H: taken from the earliest valid response received between H and H + 75 minutes.
- Runs at minutes 07 and 37 every hour, plus 17:10, 17:25 and 17:40 local.
- Every run, including failed ones, is written to `collection_runs`.
- Snapshot: the latest response received from 16:00 to 18:00. Night: the 12 hours from 20:00 to 07:00.
  A point's night is complete only with the snapshot, all 12 live night hours, the three daytime
  features and the provider forecast for the 12 hours. A calendar night needs 8 of 10 points.

## Data source and licence

Weather data by [Open-Meteo.com](https://open-meteo.com/), licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Non-commercial use, about 51
requests per day, each covering all ten points.

## AI use

Claude Code is used as development support. See `docs/AI_USE_LOG.md`.
