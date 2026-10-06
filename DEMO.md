# Recording guide: 2 to 3 minutes

Record the local web workspace and explain one design decision and one limitation in your own words. A prepared [2:50 narrated walkthrough](submission/demo.mp4) is now available, with [production details](submission/VIDEO.md). It uses edited real app screenshots and Kokoro synthetic narration. Review it before submission. The sequence below also supports making your own continuous recording.

The prepared brief is [submission/brief.md](submission/brief.md), an exact, unedited copy of `v6.1-reference-d-01`: 397 body words and 15 retained claims. Owner sign-off is pending. Opening this report in the UI loads a saved run; it does not generate a new one.

## Recording-night provider status

On 6 October, other development runs exhausted the shared Groq account's daily allowance. This does not invalidate the preserved main-project run, but a fresh call may fail. For the deadline recording, demonstrate the saved candidate and explicitly identify it as saved. A fresh run is optional; only show it as complete if it actually completes.

## Prepare the screen

Follow [README.md](README.md) and [WEB-WORKSPACE.md](WEB-WORKSPACE.md). Put the original eight documents in `research_pack/` so the matching saved source passages can be inspected. Start the local application:

```bash
.venv/bin/python -B -m research_ui --port 8765
```

Open http://127.0.0.1:8765. Keep the exact brief, [TESTLOG.md](TESTLOG.md) and [METRICS.md](METRICS.md) available. Keep credentials and private review material off screen. Rehearse opening **Runs**, selecting `v6.1-reference-d-01`, and selecting its revenue claim and revenue-conflict question.

## Suggested walkthrough

| Time | Show | Explain |
|---|---|---|
| 0:00 to 0:20 | **Runs**, the supplied pack and the saved candidate’s assignment date and TypeSafe route | “This agent takes an NSE ticker, company documents and an evidence cutoff. I am showing a saved run for 23 September 2026.” |
| 0:20 to 0:45 | **Runs** drawer, then `v6.1-reference-d-01` | “This is my saved submission candidate. The history keeps completed attempts and failures.” Expand **failed or unchecked runs** briefly, then return to the candidate. Do not imply that loading history makes a model call. |
| 0:45 to 1:25 | Revenue claim, its source passage, then the revenue-conflict open question | “The issuer reports revenue of ₹1,248 crore; the article says ₹1,428 crore. The brief uses the official result and keeps the disagreement visible. The writer supplies passage IDs, code checks citations, numbers and dates, and a separate decision model checks support.” Show both original passages rather than relying on a check badge. |
| 1:25 to 1:50 | **Run details**, then one version change in TESTLOG | “The agent gets one repair opportunity. Hard failures that remain are logged and dropped. Tests check this behavior, but a passing test or model score does not prove a claim is true.” Briefly explain the recorded fix that separated date checks from financial-number checks. |
| 1:50 to 2:25 | **Coverage** and the cited source text | “A supported brief can still leave out useful information. This candidate omits profit growth and revenue guidance. Coverage highlights possible omissions using keywords and numbers; it is a review aid, not a completeness score.” Also acknowledge that the data-centre doubling forecast could be clearer about what doubles. |
| 2:25 to 2:45 | **Export Markdown** and the exact prepared brief | “The exported report keeps the generated wording. The test log preserves changed designs and failed attempts. Mechanical pass rates are not factual accuracy, and human review remains a separate step.” |

Do not claim that Jev beat the LLM checker or that all material information is present. The saved comparison does not establish either. If mentioning test results, use results from the actual completed test commands, not an invented or stale count.

## Showing generation honestly

Optionally start one fresh model run to demonstrate the input-to-output workflow if quota is available. This is not required for the saved-run walkthrough. Use **New research**, set `SRVCABLE` and `2026-09-23`, choose the supplied pack, and explicitly select the **TypeSafe reference** route with its key already configured privately on the server. This route matches the prepared candidate's verifier. It is an explicit provider choice, not a fallback from free Zen.

Show the real progress and eventual status. If it is still pending, say that it is pending. If you switch to the saved candidate, say so and show its label. Quota waits or a failed generation must not be presented as a completed live result. Waiting time can be cut from the video if the edit is disclosed. A newly generated report may differ from the prepared submission and does not replace it without review and owner selection.

The required deliverables and remaining owner actions are in [SUBMISSION.md](SUBMISSION.md). A new experiment or live NSE integration is not needed just to record this walkthrough.
