# AI use log

Claude Code (Anthropic) is used as development support. I review, test and must be able to
explain everything it helps with. This log is declared in the final report.

| Date | What I asked | What it produced | What I checked |
|---|---|---|---|
| 2026-10-12 | Help choose a project and draft the approval proposal | Proposal v1 | Read and sent to the Module Leader |
| 2026-10-20 | Revise the proposal after "Revisions required" | Proposal v2 | Read and sent |
| 2026-10-22 | Revise again after the second feedback | Proposal v3 (amended sections) | To read and send |
| 2026-10-22 | Build the collector before approval, with collection switched off | Schema, collector, tests, workflows | 11 tests pass; one `--dry-run` format check (nothing stored) |
| 2026-10-28 | Update v3 for the later start, review it for risks, then merge v2 and v3 into one complete document | Proposal v3 (complete); `PAST_HOURS` fix | Read and sent; 11 tests pass; one `--dry-run` check |
| 2026-10-28 | Record the approval and turn its four requirements into rules | `docs/approval/`, `docs/evaluation_protocol.md`, README, schema update | Read the protocol against the email point by point |
