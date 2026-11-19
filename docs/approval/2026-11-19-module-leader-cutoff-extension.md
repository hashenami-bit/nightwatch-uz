# Module Leader approval of the later data cut-off

- **Decision:** Approved
- **From:** Sanjar Bekov, Module Leader, by University email
- **Received:** 19 November 2026, 09:28 Tashkent time, in reply to my email of 18 November 2026
  (13:05) in the same thread as the project approval of 28 October
- **What it changes:** last included night 30 Nov → 2 Dec 2026; final collection time
  1 Dec → 3 Dec 2026, 08:15 Asia/Tashkent. Nothing else.
- **Original email:** kept in the student's University mailbox
- **How each point is applied:** `DECISIONS.md` (D013)

## Email text (copied unchanged)

> Dear Usmon,
>
> Approved. You may include the night beginning 2 December 2026 and finish collection at 08:15
> Tashkent time on 3 December 2026. You reported the scheduler fault and requested the extension
> before fitting a model or examining evaluation results. The two additional nights are a reasonable
> response to the documented loss of coverage. This is the final collection cutoff.
>
> 1. Keep the original eligibility rules. Apply the 12-of-12 night coverage rule, the
>    daytime-feature requirement and the receipt-time cutoffs to every night, including the two
>    additional nights. Retain incomplete nights in the database with the reason for exclusion. Keep
>    archive backfill out of the assessed sample.
> 2. Count coverage by scheduled slot. The external timer and retry mechanism are acceptable. Keep
>    each attempt in the run log, but count a slot only once when checking completeness. Report
>    missed slots separately from slots where a run started but stored no usable data. Check coverage
>    after each night for the rest of collection.
> 3. Preserve the chronological holdout. You may build and test the analysis code on the first 13
>    complete nights under the approved split. Record the model configuration and evaluation
>    procedure before examining held-out outcomes. Count each calendar night once toward the minimum
>    of 18, even when all ten locations are complete. If fewer than 18 nights qualify by the final
>    cutoff, report the achieved count and follow the agreed descriptive fallback.
>
> Add this decision to `DECISIONS.md` before changing the cutoff in code. Record the original and
> revised dates, the reason for the extension and the date of this approval. The submission deadline
> remains 14 December 2026 at 17:00.
>
> Kind regards,
> Sanjar Bekov
