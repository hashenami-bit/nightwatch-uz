# NightWatch UZ

UDE5014CM Data Science for Developers, Assessment 2 (individual project).

NightWatch UZ collects hourly weather-model values for ten fixed monitored points in
Uzbekistan, predicts the lowest temperature of the coming night from data available at
18:00 (Asia/Tashkent), and shows a general cold-night alert. It does not measure real farm
conditions and it is not a frost warning.

## Status

**Pre-approval. Real collection is switched off.** Until the Module Leader gives written
approval, the code is built and tested with synthetic data only (`tests/fixtures`).
See `DECISIONS.md` for the approval record and every later change.

## What is built so far

| Part | Where |
|---|---|
| PostgreSQL schema with constraints (proposal v2 section 7 + v3 amendments) | `db/schema.sql` |
| Collector: one request for all ten points, validation, live-window rule, run log, raw payload with SHA-256 | `src/nightwatch/collector.py` |
| Database writes (parameterised SQL, duplicates skipped) | `src/nightwatch/db.py` |
| Command line with an off switch | `src/nightwatch/collect.py` |
| Hourly schedule (written, switched off) | `.github/workflows/collect.yml` |
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

## Timing rules (proposal v3, section 3)

- Cut-off: 18:00 local (13:00 UTC). Only responses **received** by then count for that night.
- Live value for hour H: taken from the earliest valid response received between H and H + 75 minutes.
- Runs at minutes 07 and 37 every hour, plus 17:10, 17:25 and 17:40 local.
- Every run, including failed ones, is written to `collection_runs`.

## Data source and licence

Weather data by [Open-Meteo.com](https://open-meteo.com/), licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Non-commercial use, about 51
requests per day, each covering all ten points.

## AI use

Claude Code is used as development support. See `docs/AI_USE_LOG.md`.
