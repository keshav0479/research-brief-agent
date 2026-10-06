# Submission checklist

The build and report are prepared. A narrated edited walkthrough is also prepared. Publishing, video review, final owner selection and form submission remain separate actions. This checklist does not claim those actions have happened.

## The four assignment deliverables

| Required item | Prepared evidence | Remaining owner action |
|---|---|---|
| Build link, separate prompt files and short README | [README.md](README.md), [writer prompt](prompts/system.md), [repair prompt](prompts/repair.md), [decision questions](prompts/jev_questions.json), runnable CLI and local UI | Review the release contents, explicitly authorize the actual commit and push, publish an accessible GitHub repository, and check the reviewer can open it. |
| Exact generated SRVCABLE brief | [submission/brief.md](submission/brief.md), copied exactly from [v6.1-reference-d-01](runs/v6.1-reference-d-01/brief.md); [selection record](submission/selection.json) | Approve this candidate or select another preserved run, then paste its exact Markdown into the form, including Sources. Disclose any hand edits and retain the original. |
| At least three runs with changed prompts or design, plus observations | [TESTLOG.md](TESTLOG.md), [METRICS.md](METRICS.md), saved run artifacts and code snapshots | Read the actual changes and failures, then use the log in the submission. Unit tests, UI clicks and reloading a saved report are not new model experiments. |
| A 2–3 minute recording showing the build, a design decision and a limitation | [Prepared 2:50 video](submission/demo.mp4), [transcript and disclosure](submission/VIDEO.md); [DEMO.md](DEMO.md) provides an optional recording script | Review the edited real-screen walkthrough and synthetic narration, upload it, verify viewing permissions, and put the real link in the form. |

## Prepared candidate and known limits

`v6.1-reference-d-01` is a real, unedited TypeSafe reference-route run using the fixed assignment date, `2026-09-23`. The prepared copy contains 397 body words and 15 retained claims. Body word count excludes Sources and the terms footer. It is provisional until the owner signs off.

The candidate omits profit growth and revenue guidance. Its data-centre forecast leaves the doubling referent unclear, and two statement types remain unconfirmed. Its code checks passed, but that does not establish completeness or factual accuracy. The UI's Coverage view is a heuristic review aid and cannot supply that guarantee either. Keep these limitations visible when discussing the result.

The repository describes the actual models and routes used. Do not call this candidate a successful free Zen run, a live NSE result, or proof that Jev outperforms an LLM checker. Do not count provider waits as a controlled model-speed measurement. All generated briefs remain unedited; copying or exporting one does not create a new generation.

## Final owner checklist

- [ ] Run the documented local checks and inspect the selected report and its passages.
- [ ] Approve the exact candidate and its disclosed limitations.
- [ ] Review the prepared public files. Keep credentials, uploaded private packs, local logs and private review material out of the release. The eight assignment documents are supplied locally as explained in README.
- [ ] Explicitly authorize and complete the actual commit and push. Verify evaluator access to the build link.
- [ ] Review and upload the prepared 2–3 minute walkthrough, or record your own. Keep the saved-run and synthetic-narration disclosure visible.
- [ ] Check access to both the repository and recording from the reviewer's perspective.
- [ ] Paste the selected exact brief, test-log information and real links into the submission form.

## Additional review commitments

These are our additional checks, not extra requirements in the original assignment:

- [ ] The owner reviews factual support and the development criteria. The existing [review-label template](runs/v6.1-reference-d-01/review-labels.template.json) remains unfilled. Model-generated notes and coverage hints must not be recorded as human judgments.
- [ ] Record a reviewed release checkpoint before any owner-run sealed evaluation.
- [ ] The owner runs and records the sealed evaluation separately. Its cases and contents stay owner-only, outside the public repository and the builder's view. Do not show them in the demo or claim results before evaluation occurs.

No new prompt experiment, paid model benchmark or live NSE integration is required to complete the original four deliverables. The live-data extension is an optional assignment bonus.
