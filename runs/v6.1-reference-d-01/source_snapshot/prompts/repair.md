You are now correcting a draft that failed specific checks. Produce a revised complete JSON
brief, not an explanation of the changes. previous_output is your draft, not source evidence.
repair_failures identifies items that cannot be accepted unchanged.
repair_requests lists supported items whose category was unclear. Clarify their wording or
split mixed items by kind, without strengthening the speaker's certainty.

For each listed failure, change the wording, the cited unit IDs, or omit that item:
- For a missing number or unsupported statement, find the exact supplied unit that supports
  the assertion and cite it. If it does not exist, remove the assertion.
- For unresolved support, simplify the item to a single short statement close to the source.
  Remove extra causes, interpretation, or conclusions that the source does not explicitly give.
- For uncertain kind, split mixed items into separate reported events, expectations or
  interpretations. Preserve the speaker's certainty words and what the forecast measures.
- For an open question, state one sourced factual premise followed by one unanswered question.
  Cite every premise, including any amount. kind describes the premise, not the question.
- Never replace an attributed management expectation with a completed fact to pass a check.

Preserve already-supported items. Do not repeat a failed item verbatim with the same cites.
For a section-count failure, add only distinct, supported items from the supplied evidence.
You may move a supported item to its proper section. Keep latest results in Snapshot and
put a point under Bear only when it describes an actual risk or adverse development.
Do not add computed numbers or outside information. Return all four section arrays.
