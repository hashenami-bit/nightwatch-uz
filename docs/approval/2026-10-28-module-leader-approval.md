# Module Leader approval (Checkpoint 1 evidence)

- **Decision:** Approved
- **From:** Sanjar Bekov, Module Leader, by University email
- **Received:** 28 October 2026, in reply to the complete proposal v3 sent the same day
- **Approved proposal:** `UDE5014CM CW2 - Project Proposal v3 (complete) - 28 Oct.docx`
- **Original email:** kept in the student's University mailbox
- **How each requirement is applied:** `docs/evaluation_protocol.md` and `DECISIONS.md` (D005)

## Email text (copied unchanged)

> Dear Usmon,
>
> Thank you for the complete Version 3.
>
> Decision: Approved.
>
> I approve NightWatch UZ as a small exploratory study using prospectively collected weather-model data. Your revised receipt-time rules, complete overnight target, daytime features, database design and comparative modelling provide a suitable route to LO1, LO2 and LO5.
>
> The approved minimum is 18 complete calendar nights, with 24 or more as your aim. Please apply the following implementation requirements:
>
> 1. Describe and protect the final evaluation correctly. Your timetable selects the final model after collection and then generates predictions from stored snapshots. Describe this as a retrospective chronological hold-out evaluation. These final predictions were not issued on the original evenings.
> During collection, keep development analysis within the first 13 complete nights. At the fixed collection cut-off, calculate the final split once, select and fit models using training nights only, then freeze the configuration before opening test results. Do not use provisional dashboard performance on potential test nights to revise features, candidates or thresholds. Within every cross-validation fold, preprocessing and training outcomes must have been available before the validation prediction cut-off.
>
> 2. Keep statistical conclusions proportionate to the sample. Five to seven test nights support exploratory comparisons only. The HLN correction and bootstrap do not make this a precise evaluation, and autocorrelation estimates from so few nights cannot establish independence.
> Preserve actual calendar dates. Missing nights must not be removed and the remaining dates treated as consecutive: standard Newey–West implementations assume consecutive, equally spaced observations. Bootstrap blocks must also respect calendar gaps. Document the exact DM implementation, forecast-horizon setting and variance correction. Where gaps or too few usable blocks prevent defensible inference, report the nightly paired differences descriptively and explain the limitation.
> Keep Q1 as the main comparison and Q2–Q3 exploratory. Report observed errors and alert counts whether or not the improvement targets are achieved.
>
> 3. A later data cut-off is acceptable. The 26 November date is your internal target. You may move it a few days later, provided sufficient time remains for analysis, reproducibility checks and report completion before the published LMS deadline. Record the revised date before assessed collection starts; do not extend collection in response to favourable or unfavourable results.
> Specify the last included night separately from the final collection time. A night beginning on date D needs collection through the following morning, including the allowance up to 08:15 for its 07:00 value. Update the timetable and expected split counts. Under your current 20% rule, additional nights may increase training data without increasing the test count.
>
> 4. Verify collection completeness and preserve reproducibility. Treat the estimated 90% completeness as a planning assumption. Report actual coverage after the first three nights and retain failed and missing slots. GitHub documents that scheduled jobs can be delayed or dropped, so additional triggers do not guarantee collection. Keep the 12-of-12 target rule, receipt-time checks and separation of back-filled, manual and synthetic records.
> Retain the lawful data snapshot, raw responses, fixed coordinates, query configuration, tests and local database restoration instructions in the assessed repository.
>
> Record these controls in your protocol and README. No further proposal rewrite is required for this scope. You may begin assessed collection after this approval once the collection MVP is working.
>
> Keep this email with Checkpoint 1 evidence and include all four historical checkpoint records in the final submission by the published LMS deadline.
>
> Kind regards,
> Sanjar Bekov
