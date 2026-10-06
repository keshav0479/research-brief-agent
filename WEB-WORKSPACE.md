# Local research workspace

A browser interface for the existing company research agent. Read a saved report, select any claim to inspect its source passages, review excluded documents, and run the same CLI pipeline on another document pack.

## Start

Use the Python environment described in README.md. No additional dependencies or frontend build are required.

```bash
.venv/bin/python -B -m research_ui --port 8765
```

Open http://127.0.0.1:8765. The server binds only to this machine. It is intended for local use, not public hosting.

Saved reports work without API keys. For new runs, use the existing ignored `.env` configuration or export the writer key before starting the server. If your keys are in shell environment files:

```bash
source "$HOME/.config/groq.env"
source "$HOME/.config/typesafe.env"
.venv/bin/python -B -m research_ui --port 8765
```

The TypeSafe file is needed only for the explicitly selected reference route. The UI reports whether a key is configured; it never displays the key or accepts one in the browser. Documents in new runs are sent to the selected writer and verifier services, as in the CLI.

## Workflow

1. Open the saved v6.1 example, or choose **New research**.
2. Enter the ticker and evidence cutoff date. Use the supplied example pack or choose Markdown documents.
3. Select the free Zen verifier or the TypeSafe reference route. There is no automatic fallback between providers. One run can execute at a time.
4. Read real pipeline progress. A provider failure remains a failed or unavailable run, with its saved diagnostics.
5. Select report claims to inspect cited passages and recorded checks. Inspect **Sources**, **Coverage**, and **Run details** for exclusions, possible omissions, and repair history.
6. Export the exact saved Markdown or the workspace audit JSON. The interface never rewrites generated claims.

Uploads currently support UTF-8 `.md` files with the same source metadata required by the CLI:

```markdown
---
source: Example company investor relations
url: https://example.test/ir/results
published: 2026-08-12
type: official company filing
---
# Example Ltd quarterly results
Example Ltd (NSE: EXAMPLE) reported its quarterly results here.
```

The complete source types are `official exchange filing`, `official company filing`, `official company transcript`, `news article`, and `blog post`. Tiers use supplied metadata; the app does not independently authenticate a document's origin. PDF extraction and live ticker data are not implemented.

## Evidence and coverage

Saved source quotes appear only when both document bytes and the passage parser match that run's saved hashes. If either changes, the report remains readable but quotes are withheld with an explanation. New input packs are retained under ignored `.web-workspace/inputs/`; generated runs retain the CLI's normal `runs/` artifacts.

Coverage is a deterministic review aid. It compares topic and numeric cues in recent, admitted primary sources with accepted claim text. A citation alone does not establish that every fact in its passage was mentioned. Revenue and profit growth are reviewed separately from their amounts. The four labels indicate a keyword mention, possible partial coverage with figures to review, no matching mention in the brief, or no matching source passage. They are not completeness or factual-accuracy scores. Numeric-token comparisons do not match metric, sign, unit or scale, and equivalent paraphrases can be missed. Figure lists are prompts for review, not proven missing facts. Coverage never edits the brief, changes model decisions, or fills human review labels.

The initial example is `v6.1-reference-d-01`, chosen for demonstrating revenue growth and contract pricing risk alongside inspectable evidence. It remains a saved example, not a new live run or a final submission approval. Historical unchecked, incomplete, unavailable, and failed runs stay distinguishable.

## Reviewed baseline

The reviewed v6.1 engine, prompts and all saved run records remain unchanged. The UI has since received coverage, table-formatting and drawer fixes. Historical review manifests describe their named checkpoints; the release manifest identifies the packaged version. README.md describes the current engine and workflow. UI and coverage tests are separate from the original 136 engine tests.

```bash
.venv/bin/python -B -m unittest discover -s tests -v
```

The optional browser-state regression tests use Node's built-in runner; Node is not required to serve the application.

```bash
node --test tests/test_web_ui.mjs
```

Final submission selection, human claim labels, owner-run sealed evaluation, and the recorded demonstration remain separate final steps.
