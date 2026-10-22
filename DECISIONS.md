# Decision records

Each record: date, decision, reason, and the approval it depends on.

## D001 · 2026-10-22 · Scope follows proposal v2 plus v3 amended sections

- Proposal v2 sent 21 Oct 2026; Module Leader decision 21–22 Oct: Revisions required (3 points).
- Proposal v3 (amended sections) sent 22 Oct 2026. **Approval: pending.**
- When approval arrives, record here: date, exact wording, and where the email is kept.

## D002 · 2026-10-22 · Real collection stays off until written approval

- The collector refuses to run unless `COLLECTION_ENABLED=true`, and the GitHub schedule is commented out.
- Reason: the proposal and the Module Leader's email say assessed collection starts only after approval.
- Before approval: synthetic test data only, plus format checks with `--dry-run` that store nothing.

## D003 · 2026-10-22 · Implementation details inside the accepted design

- Extra tables `raw_responses` (raw JSON + SHA-256, for provenance) and `current_conditions`
  (the current-conditions block used for the 18:00 feature snapshot). No change to scope.
- One request covers all ten points; times are requested in UTC as Unix time to avoid time-zone parsing errors.
- The collector uses psycopg directly; SQLAlchemy and pandas come in with the analysis pipeline.
- Python 3.12 managed with uv; the lock file `uv.lock` pins every version.
