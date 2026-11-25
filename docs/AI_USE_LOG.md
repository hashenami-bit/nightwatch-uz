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
| 2026-11-10 | Check the state of collection and fix the missing runs | Found that GitHub's schedule dropped about 90% of runs; backup trigger input in the workflow, `docs/backup_timer.md`, DECISIONS D008 | Run counts per day read from `gh run list`; 13 tests pass; one test run with trigger `backup` |
| 2026-11-11 | Guide me through setting up the external timer, then check that it works | Step-by-step guide; D008 update with the switch-on time and the first 25 hours | I created the token and both cron-job.org jobs myself; both test signals returned 204; run list checked on 12 Nov |
| 2026-11-13 | Plan the work up to submission; check collection again | Found that about half of the runs got no answer from the provider; retry jobs in the workflow, error text in the run log, DECISIONS D009; dated plan | 13 tests pass; one manual run of the new workflow stored 10 of 10 points; first real retry still to be checked |
| 2026-11-18 | Check where the project stands; build the nights builder | `src/nightwatch/nights.py`, three new columns in `nights`, the `nights` workflow, 14 new tests, DECISIONS D010 | 27 tests pass; the schema change was tried on a copy of the old schema; the trend was checked on one real response (nothing stored); the first run on the real data is still to do |
| 2026-11-18 | Build the missing-run check | `src/nightwatch/slots.py`, a second step in the `nights` workflow, 7 new tests, DECISIONS D011 | 34 tests pass; the first run on the real data is still to do |
| 2026-11-18 | Update the email to the Module Leader about the collection gap with the real numbers | The email text, typed as a draft reply in my mailbox | I read the draft and sent it myself |
| 2026-11-19 | Record the Module Leader's approval of the later cut-off and apply it | `docs/approval/2026-11-19-...`, DECISIONS D013 (committed first), then the new date in `config.py`, the tests, the protocol and the README | I pasted his email myself and read the saved copy; 34 tests pass |
| 2026-11-19 | Make the coverage check after each night start by itself | One extra step in the collector workflow for the 08:37 and 09:07 Tashkent slots | The time rule was tried with ten made-up clock times; the first automatic check is due on 20 Nov |
| 2026-11-21 | Find out why every collector run failed since the morning | Found that the free Actions minutes ran out; set out paying or going public; made the repository public on my choice; DECISIONS D014 | I chose public after reading the risks; a check run and the next timer run were watched |
| 2026-11-25 | Build the modelling code (behind the 20–24 Nov plan) | `features.py`, `models.py`, `cv.py`, `stats.py`, `develop.py`, the `develop` workflow, 41 new tests on synthetic data, DECISIONS D015 | 75 tests pass locally; an independent code review found nothing serious (two small fixes made); the hand-worked DM example in the test file to be checked by me; nothing pushed yet; no real data read |
| 2026-11-25 | Build the alert scores and the robustness checks | `alerts.py`, `robustness.py`, a shorter-feature option in `models.py`, 22 new tests on made-up data, DECISIONS D016 | Tests pass locally; an independent code review found nothing serious (one F1 fix made); hand-worked confusion matrix in the test file to be checked by me; nothing run on real data |
