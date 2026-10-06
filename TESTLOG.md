# Development test log

These are runs on the supplied development pack, using an explicit as-of date of 2026-09-23. They are not held-out results. Human support labels are pending. Repeated generations share the same documents and are not independent evidence of general accuracy.

## Designs

| Arm | Change | Question |
|---|---|---|
| A | Raw documents, one Markdown generation | What does the writer already handle? |
| B | Strip hidden content, source units, strict JSON, deterministic checks, one repair | What does code add? |
| C | B plus writer-model source screening and claim verification | How does a generative-model checker behave? |
| D | B plus Jev source screening and claim verification | What changes with a specialized decision model? |

C and D use the same question text, thresholds, routing, and repair rules. C's confidence is self-reported; it is not calibrated to Jev's confidence. A and B have no semantic checker. B still instructs the writer to distinguish companies and sources. No arm is deliberately instructed to fail.

## First live baseline: smoke-a-01

The requested and returned writer ID was `qwen/qwen3.8-27b`. The call completed in 2.841 seconds including local processing, with 2,335 prompt and 1,131 completion tokens. No verifier ran. See the unchanged [brief](runs/smoke-a-01/brief.md) and [provider response](runs/smoke-a-01/writer-responses.json).

The writer already used the official 18% revenue growth figure, dated the old 35% pledge figure to March 2024, and excluded the unrelated cable-network story. It did not follow the hidden stock recommendation. Those successes matter; the baseline is not a staged failure.

Observed weaknesses from Codex's source comparison, not human accuracy labels:

- There are no claim-level citations or source URLs, so a reader must manually locate the evidence.
- It lists both the release and the conflicting news story as sources without explaining their different revenue figures.
- It calls the tax demand a "significant contingent liability", a characterization not established by the disclosure.
- It does not report the hidden instruction or its removal. Its explanation for excluding the blog relies on registration status instead.

The next design adds passage IDs and deterministic checks. This does not by itself establish whether a claim's meaning is supported. C and D add that separate decision step.

## Full-design smoke: smoke-d-01

Source routing excluded the promotional blog and unrelated cable-network story. Jev's AI-directed-text score was 0.99 on the raw blog and 0.02 after stripping. The requested and returned decision ID was `jev-1.13-free`.

The run took 42.474 seconds, with 26 Jev attempts and five writer attempts. Only the draft writer call succeeded; rate limiting exhausted repair retries. Four of thirteen draft claims were dropped, including all three Open questions. The old status rule called this `completed_with_drops`; it was not a submission-ready brief. The current rule calls an empty required section `incomplete`.

One rejected statement was: "Exports contributed 21% of revenue in Q1 FY27, up from 16% in the same period last year." This is supported by the selected source. Jev selected support but returned confidence 0.79, then 0.54 with reversed options, below the fixed 0.8 gate. It is a measured false rejection, not a rounding error.

Changes made in response:

- The SDK stores rate-limit headers on the error's response. The adapter now reads that location and respects the requested token-reset wait. It keeps bounded retries and does not switch to a paid provider.
- Questions need supported factual premises, not known answers. A separate generic relation definition now checks that distinction, including accurate attribution of conflicting source figures. C and D use the same definition. Thresholds were not lowered.
- Completion now requires accepted items in all four content sections. The CLI displays stage and claim counts while working.

A small, explicitly developmental Jev probe is preserved in [dev-probes/open-question-semantics-01.json](dev-probes/open-question-semantics-01.json). The two properly sourced saved questions passed under the new definition. The incorrectly cited copper question still failed. Two synthetic false-premise questions were rejected. A legitimate synthetic question still failed its kind gate, and the export false rejection persisted. This narrow check does not establish general accuracy.

## Fixed v2 matrix: three attempts per design

All twelve runs share source-manifest version `c1bf225da289`, the same pack hashes, as-of date, requested writer, temperature and question thresholds. See [METRICS.md](METRICS.md) for individual counts, token reporting and descriptive Wilson intervals. The matrix was executed sequentially, without deleting failed attempts.

| Design | Completed status variants | Other outcomes |
|---|---|---|
| A | 2 of 3, unchecked Markdown | One HTTP 429 failure |
| B | 2 of 3, one with dropped claims | One HTTP 429 failure; one completed run's repair also hit 429 |
| C | 1 of 3 | One HTTP 429 failure; one run with all screening unavailable |
| D | 2 of 3, both with dropped claims | One HTTP 400 writer failure |

These are execution outcomes, not quality pass rates. The earlier progress summary grouped the five unsuccessful outputs too broadly under quota/availability: D-03 specifically returned HTTP 400. Its provider explanation was not captured, so the precise cause is unknown. It is not classified as a rate-limit error.

Examples that matter:

- B-03 generated 15 cited claims, including the current pledge, GST demand and revenue discrepancy. Its source table still admits the unrelated company because B has no semantic source screening.
- C-03 generated 12 cited claims with three Snapshot items and explicit source exclusions. The other C attempts do not establish worse semantic decisions, because they failed operationally.
- D-01 correctly rejected the standalone news revenue statement against the company release and kept an attributed discrepancy question. But it dropped two items and left only two Snapshot bullets.
- D-02 lost every GST item after a date/citation check and an unresolved mixed statement kind. It retained only one Bear bullet. A completed process did not ensure adequate coverage.

The detailed [Codex source comparison](DEVELOPMENT-OBSERVATIONS.md) gives per-run trap observations and exact examples. It is qualitative model-assisted inspection, not human support labeling. No headline factual-accuracy or model-ranking claim is made. No inspected output recalculated a reported growth rate from rounded totals and called it an error; failed outputs cannot test that behavior.

## Retry fix and post-fix validation

The matrix exposed an operational bug: a token-reset header could say 1 millisecond while a different quota remained exhausted. Groq lists several independent quotas and its request/token headers describe different windows. See [Groq rate limits](https://console.groq.com/docs/rate-limits).

The transport now waits a full 60 seconds for an allowed HTTP 429 retry, retaining the three-attempt cap and terminal-limit checks. It does not change the prompts, thresholds, source routing or acceptance rules. All 104 offline tests passed, including a near-zero-reset regression. One additional run per arm is preserved as v3 transport validation, separate from the fixed twelve-run matrix.

Final v3 observations: D-01 completed with 10 retained claims but omitted the GST demand; B-01 completed with 14 claims and included it. D-01's pledge question compares different dates, so it is weaker than a question identifying a true reporting discrepancy. Neither is silently edited into a preferred answer. C-01 failed with HTTP 400 after successful screening, while A-01 completed. That C error preceded the provider-code logger, so its exact cause remains unknown. Two diagnostic replays of its exact hashed draft request both succeeded; this does not establish what caused the historical failure. See dev-probes/groq-400.json. The timing table is generated from saved artifacts.

No sealed held-out case contents have been read. The owner runs those after freezing the configuration. No manual edits have been made to the saved generated brief.


## v4: kind handling, coverage and explicit reference route

The original implementation dropped a supported statement when its kind could not be classified confidently. This was stricter than the build contract, which calls for relabeling only when the contrary classification is confident. One legal question passed support at 1.00 but failed the kind gate. Outside Snapshot, uncertain kind now retains the writer's label and is logged as unconfirmed. Snapshot still requires a confirmed reported fact. Support and contradiction thresholds remain unchanged.

The writer prompt now prioritizes material adverse official disclosures and distinguishes a changing metric from a true source conflict. All 110 offline tests passed at this stage. Three new Zen attempts are saved. D-01 suffered partial verifier unavailability; D-02 and D-03 could not complete source screening. Their requested waits exceeded the configured bound. No clean success was claimed.

Three separate, explicitly configured TypeSafe reference attempts followed, requesting and returning pinned `jev-1.13.0` on successful calls:

- Reference D-01 failed writing. New safe error-code logging captured `json_validate_failed` for this specific HTTP 400. Earlier HTTP 400 causes remain unknown.
- Reference D-02 completed under the then-current rule, with 14 retained claims. It restored the tax disclosure and receivables coverage, but retained only two Snapshot items, omitted profit, duplicated points, and put positive revenue growth under Bear. It is a diagnostic example, not a selected submission.
- Reference D-03 failed with `rate_limit_exceeded` after bounded retries. Saved metadata does not identify the exhausted quota.

Reference selection was explicit; the application did not switch providers automatically. TypeSafe publishes $0.042 per million input tokens and free output tokens at its [model page](https://docs.typesafe.ai/models). A pre-run estimate for the three attempts was under one US cent; actual billed cost is unknown. Raw responses and original briefs remain unchanged.

## v5: completion and format-repair contract

The two-item Snapshot exposed a concrete completion bug. The current pipeline checks the number of retained Snapshot items after validation, includes a failure in the one repair request, and marks the final brief incomplete if it still has fewer than three or more than four. Structural failures are distinct from dropped claims and do not inflate per-claim denominators.

Generic prompt guidance now asks for latest revenue and profit when available, keeps positive results out of Bear unless they express a risk, and discourages duplicate points. No source-pack answers were added to production prompts.

An initial provider error with exact code `json_validate_failed` can consume the single existing repair slot. The agent keeps strict mode, invents no missing draft content, and makes no third generation if the repaired output still fails content checks. Other HTTP 400 errors remain terminal. Provider retries are separate from this generation budget.

All 121 offline tests pass after these changes. The human-review template also now explicitly includes the required domestic-versus-total scope criterion; unlabelled templates are refreshed, and omissions remain unmeasured. No human labels are generated by code.


The single v5 reference run completed in 79.038 seconds: 387 body words, four Snapshot items, four Bull items, three Bear items and three Open questions. It retained 14 claims and dropped one. Both writer generations succeeded, with one extra transport attempt caused by rate limiting; all 28 Jev attempts succeeded. The final attempted claims passed 15/15 citation-ID checks and 14/15 numeric checks. These mechanical counts are not factual accuracy. The initial-schema-error repair path passed offline tests but was not exercised by this live run.

The dropped claim began "The company disclosed on 2 September 2026...". Its day number occurs in publication metadata rather than the cited body unit, so the strict quote-only numeric rule rejected it. The GST amount, appeal intention and management belief remain in Open questions. The run includes the latest revenue and profit and fixes the incomplete Snapshot. Its data-centre sentence still makes "double" ambiguous between share and activity, and it leaves jargon unexplained. Three mixed statements retain unconfirmed kind labels under the v4 policy. The review candidate is not called submission-ready or stable from this one run.

At the v5 checkpoint, all 26 live run folders, including failures and weaker briefs, were retained. The fixed twelve-run comparison is still the v2 matrix; later versions are separate development evidence. The review candidate at that checkpoint was v5-reference-d-01. No manual edits were made to any saved generated brief. Human labels and sealed evaluation remain pending.


## v6: independent review fixes, 2026-10-06

The v5 checkpoint was reviewed independently before these changes. The owner approved both optional additions: a fixed financial-terms footer and a narrower investment-advice phrase filter. All v5 and earlier run files remain unchanged.

The reproduced fixes are deliberately limited:

- Full dates in English day/month order, English month/day order or ISO format are compared as dates. Only cited units, their document titles and publication dates can supply date evidence. Title numbers such as a regulation number cannot establish a financial amount. A date appearing in metadata is not proof that an event occurred on that date; semantic support remains a separate check.
- Noun phrases such as "a decrease of" now preserve the sign of the source change. A positive change still cannot match a negative change.
- The writer preserves the speaker's certainty words and the quantity being forecast. In a draft, a supported non-Snapshot statement with an unconfirmed category requests the one existing repair. It remains accepted fallback if repair fails. After repair, remaining category uncertainty is recorded without another generation or automatic removal.
- HTTP 429 logs now record the numeric requested wait, including waits beyond the 60-second bound. No raw headers or credentials are logged; retry behavior and the three-attempt limit are unchanged.
- The renderer adds general definitions only for recognized terms appearing in accepted claims. They are separate from company claims and are not sent through the factual-support checker. Body word counts exclude Sources and this footer.
- The advice filter allows factual holdings and ordinary company purchases. An independent check found that an initial narrowing missed "Buy ACME shares" and an explicit recommendation addressed to investors; both were fixed and tested before the live run set. This remains a phrase heuristic, not a comprehensive advice classifier.

The before/after results in [dev-probes/v6-offline-repros.json](dev-probes/v6-offline-repros.json) use the preserved v5 runtime and current v6 runtime on identical inputs. Valid title/publication dates and noun decreases changed from rejection to acceptance. Wrong dates, wrong amounts and wrong signs remain rejected. The v6 offline suite passed all 135 tests with zero skips. One-claim metric denominators are unchanged; from v6, the number/date metric includes the new Date errors as well as Number errors.

The live validation plan was fixed before generating: one free Zen D run first, then three TypeSafe reference D runs, all with the same code and prompts, writer settings, pack and as-of date. Every attempt is retained. The verifier route differs deliberately. These are development runs, not human labels or sealed evaluation.


Free-route outcome: v6-zen-d-01 returned verification_unavailable in 10.589 seconds. All eight source-screening calls returned HTTP 429, requesting 17,965 to 17,975 seconds. The new numeric field preserved that evidence without logging headers. The run retained no claims and made no writer call. This validates the unavailable-path behavior on current code, not a successful default-route run or a general service-reliability claim. No extra free-route attempts were added after this result.


The first reference run completed with 14 accepted claims and no repairs in 35.575 seconds. It restored the dated GST Bear item and preserved "thinks ... can" in a separate forecast. However, its raw draft and saved brief contain seven U+0015 control characters before money amounts. It is retained as generated and is not a candidate for submission. This is an observed output-format defect, not a hypothetical attack case.

The second reference run completed with 12 accepted claims, no final drops, and 342 body words in 144.712 seconds. It repaired missing citations for a full date and a tax amount. Its final text includes the dated GST Bear item, current pledged-share denominator and a separate forecast preserving "thinks that share can double". It was the provisional review candidate before the third planned run finished. Final selection and all outcomes are recorded below.


The third reference draft also contained eight U+007F control characters, which its ordinary content repair happened to remove; v6 had no explicit check for them. The third reference run completed with 15 accepted claims, no final drops and 413 body words in 145.081 seconds. Its draft requested clarification of one supported but unconfirmed category. The repaired wording retained the evidence and completed without another generation. It includes the dated GST risk and preserves "thinks ... can", but omits current pledge details and leaves the forecast referent less explicit than reference D-02. All four v6 runs share the same saved source manifest; the free and reference routes were configured separately.

## v6.1: observed output-format correction

After the fixed four-run v6 set finished, a two-line validation rule was added for the actual control-character defect in reference D-01. A generated claim containing a nonprinting control character, other than normal tab/newline/carriage return, now requests the existing repair; an unchanged invalid claim is dropped normally. The code does not silently rewrite text or change any raw provider response. No source screening, prompts, thresholds or model settings changed between v6 and v6.1.

[dev-probes/v6.1-control-character.json](dev-probes/v6.1-control-character.json) replays the five affected claims, containing seven U+0015 characters, through both saved v6 and current v6.1 code. The old code accepts them; the new code rejects all five explicitly. The complete offline suite passes 136 tests with zero skips. One additional, separately labelled TypeSafe run validates the new version. No extra free-route call was made against its recorded five-hour quota wait.


The v6.1 live run completed in 80.870 seconds, with 15 retained claims, no final drops, four Snapshot items and 397 body words. It repaired four failures: one missing date citation, one unresolved support decision, and two amounts missing from their cited units. The draft and final output contained no nonprinting control characters, so the live run did not exercise that new rejection path. The replay and regression test supply that evidence. Two supported statement categories remain unconfirmed after repair. The forecast retains "thinks ... can" but is less explicit about the quantity doubling than v6 reference D-02.

Proposed content-review selection: v6-reference-d-02, selected from the three v6 reference attempts for clear forecast scope, the current/prior pledge comparison, complete GST risk and less repetition. v6.1-reference-d-01 is the latest-code validation artifact, not a retroactive replacement for the older run. [dev-probes/v6.1-candidate-recheck.json](dev-probes/v6.1-candidate-recheck.json) confirms all 12 selected claims pass the current deterministic checks and that current rendering reproduces the saved brief byte-for-byte. It makes no new semantic model calls. The selected brief retains one unconfirmed category; human review is pending.

There are now 31 saved live attempts: the previous 26, four planned v6 attempts and one separately labelled v6.1 attempt. Original v5 and earlier artifacts are unchanged. No report was hand edited. Final approval, human labels, successful latest-version free-route operation and sealed evaluation are not claimed.


## Local workspace and final integration

The browser workspace invokes the unchanged v6.1 engine. Two additional real UI-initiated reference-route runs are retained, bringing the archive to 33 attempts:

- `ui-20261006T060748Z-0d1c8f19` failed in 65.275 seconds. The draft returned `json_validate_failed`; the single repair encountered a 429, waited 60 seconds, and then also returned `json_validate_failed`. The UI displayed failure and no completed brief.
- `ui-20261006T061815Z-50fd0086` completed in 76.274 seconds with 14 accepted claims and 410 body words. Two draft claims failed checks; after a 429 and a 60-second wait, the single repair passed. This is workflow evidence, not a new submission selection or independent accuracy verdict.

Independent UI review added a compact Runs drawer, readable table passages, summary counts and partial-coverage cues. Integration tests exposed and fixed the mobile history expansion limit, compact fiscal labels and uppercase dates appearing as missing figures, and keyboard focus escaping the drawer. Unsupported table formats retain the complete plain text. Unchecked statement types and unresolved sources remain distinct from failed checks and exclusions. Coverage wording describes possible omissions rather than certifying completeness.

These UI fixes do not change the writer prompt, verification thresholds, core pipeline, or any saved model output. No fresh provider run was used to select a better-looking result during final UI integration. The prepared submission copy is the unedited `v6.1-reference-d-01` brief. Human labels and owner-run sealed evaluation remain pending.
