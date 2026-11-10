# Backup timer for the collector (DECISIONS.md D008)

GitHub's own schedule dropped about 90% of the planned runs between 28 Oct and 10 Nov 2026.
An external timer now starts the same workflow at the same schedule slots. It does not collect
any data itself: it only tells GitHub "start the collect workflow now". The collector code, the
database and the rules for a complete night are unchanged.

Runs started this way are stored with `collection_runs.trigger = 'backup'`.

## Settings of the external timer (cron-job.org, free plan)

Two jobs with the same request. Time zone of both jobs: **UTC**.

| Job | Schedule (UTC) | Tashkent time |
|---|---|---|
| nightwatch hourly | minutes 7 and 37 of every hour, every day | :07 and :37 |
| nightwatch snapshot | 12:10, 12:25 and 12:40 every day | 17:10, 17:25, 17:40 |

Request (the same for both jobs):

- **URL:** `https://api.github.com/repos/hashenami-bit/nightwatch-uz/actions/workflows/collect.yml/dispatches`
- **Method:** `POST`
- **Headers:**
  - `Accept: application/vnd.github+json`
  - `Authorization: Bearer <token>` (the token is **not** stored in this repository)
  - `Content-Type: application/json`
- **Body:** `{"ref":"main","inputs":{"trigger":"backup"}}`
- A successful call answers with HTTP status **204** (accepted, empty answer).

## The token

A GitHub fine-grained personal access token that can do one thing only:

- Repository access: only `hashenami-bit/nightwatch-uz`
- Permission: **Actions: Read and write** (nothing else)
- Expiry: after the data cut-off (D006), for example 15 Dec 2026

It is kept only in the cron-job.org job settings. Delete the token on GitHub after 1 Dec 2026.

## How to check that it works

```bash
gh run list --workflow collect.yml --limit 10
```

There should be a new run within a minute of every :07 and :37, with the event
`workflow_dispatch`. The exact coverage is measured from `collection_runs` (protocol C4).

## Switching it off

Disable both jobs on cron-job.org after 1 Dec 2026 08:15 Tashkent. The collector refuses to store
anything after that time anyway (D006).
