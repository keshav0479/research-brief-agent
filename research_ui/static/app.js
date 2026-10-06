// Project origin: keshav0479/research-brief-agent | K0479-RBA-2026 | See NOTICE.
"use strict";

const $ = (id) => document.getElementById(id);
const state = { status: null, runs: [], data: null, tab: "brief", selectedClaim: null, selectedSource: null,
  job: null, jobTimer: null, historyLimit: 8, historyExpanded: false, showOtherRuns: false, loading: 0, submitting: false, mode: "report", jobEventCount: 0 };
const statusMap = {
  completed: ["Checks complete", "good"], completed_with_drops: ["Completed, claims dropped", "warn"],
  completed_unchecked: ["Unchecked output", "warn"], incomplete: ["Incomplete brief", "warn"],
  verification_unavailable: ["Verification unavailable", "bad"], failed: ["Run failed", "bad"],
};
const kindNames = { reported_fact: "Reported fact", management_view_or_forecast: "Management view / forecast", third_party_view: "Third-party view" };
function el(tag, className, text) { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined && text !== null) node.textContent = String(text); return node; }
function append(parent, ...nodes) { for (const node of nodes.flat()) if (node) parent.append(node); return parent; }
function button(text, className, action) { const node = el("button", className, text); node.type = "button"; node.addEventListener("click", action); return node; }
function pill(text, tone = "neutral") { return el("span", `status-pill ${tone}`, text); }
function note(text, tone = "") { return el("div", `notice ${tone}`, text); }
function stripEmphasis(text) { return String(text ?? "").replace(/\*\*/g, ""); }
// Show Markdown table passages as a small table; other passages as plain quoted text.
function quoteNode(text) {
  const original = String(text ?? "");
  const plain = () => el("blockquote", "evidence-quote", original || "No passage text available.");
  const lines = original.split("\n").map(line => line.trim()).filter(Boolean);
  const firstRow = lines.findIndex(line => line.startsWith("|"));
  if (firstRow < 0 || firstRow > 1 || /\\\|/.test(original)) return plain();
  const rows = lines.slice(firstRow);
  if (rows.length < 3 || rows.some(line => !line.startsWith("|") || !line.endsWith("|"))) return plain();
  const cells = (line) => line.trim().replace(/^\||\|$/g, "").split("|").map((cell) => stripEmphasis(cell.trim()));
  const isSeparator = (line) => cells(line).every((cell) => /^:?-{3,}:?$/.test(cell));
  const width = cells(rows[0]).length;
  if (width < 2 || !isSeparator(rows[1]) || rows.some(line => cells(line).length !== width)
      || rows.slice(2).some(isSeparator)) return plain();
  const figure = el("figure", "evidence-quote quote-table");
  if (firstRow === 1) figure.append(el("figcaption", "", stripEmphasis(lines[0])));
  const table = el("table");
  rows.filter((_, index) => index !== 1).forEach((line, index) => {
    const row = el("tr");
    for (const cell of cells(line)) row.append(el(index === 0 ? "th" : "td", "", cell));
    table.append(row);
  });
  figure.append(table);
  return figure;
}
function setText(id, text) { $(id).textContent = text ?? ""; }
function errorMessage(error) { return error instanceof Error ? error.message : "The request could not be completed."; }
function displayDate(value) { if (!value) return "Date unavailable"; const date = new Date(`${String(value).slice(0, 10)}T12:00:00`); return Number.isNaN(date.valueOf()) ? String(value) : date.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }); }
function formatNumber(value, suffix = "") { return typeof value === "number" && Number.isFinite(value) ? `${value.toLocaleString(undefined, { maximumFractionDigits: 3 })}${suffix}` : "Not reported"; }
function textValue(value) { if (value === null || value === undefined || value === "") return "Not reported"; if (Array.isArray(value)) return value.map(textValue).join(", "); if (typeof value === "object") return Object.entries(value).map(([k, v]) => `${k}: ${textValue(v)}`).join(" · "); return String(value); }
function allClaims() { return (state.data?.sections || []).flatMap((section) => section.claims || []); }
function sourceById(id) { return state.data?.sources?.find((source) => source.id === id); }
function showError(error) { setText("global-error", errorMessage(error)); $("global-error").hidden = false; }
function clearError() { $("global-error").hidden = true; }
async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { "Accept": "application/json", ...(options.body ? { "Content-Type": "application/json" } : {}), ...options.headers } });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(typeof data.error === "string" ? data.error : data.message || `Request failed (${response.status}).`);
    error.status = response.status;
    throw error;
  }
  return data;
}
function statusLabel(status) { return statusMap[status] || ["Needs review", "neutral"]; }
function setMode(mode) {
  state.mode = mode; $("new-research").hidden = mode !== "new"; $("report-workspace").hidden = mode === "new";
  if (mode === "new") updateReadiness();
}

function renderHistory() {
  const container = $("run-history");
  const focusedIndex = [...container.querySelectorAll("button")].indexOf(document.activeElement);
  container.replaceChildren(); setText("runs-count", state.runs.length);
  const filter = $("history-filter").value.trim().toLowerCase();
  const runs = state.runs.filter((run) => [run.id, run.company, run.ticker].some((v) => String(v || "").toLowerCase().includes(filter)));
  const selectedId = state.data?.run?.id, demoId = state.status?.demo_run;
  const pinned = (run) => run.id === selectedId || run.id === demoId;
  // Completed runs first; failed, unavailable and unchecked runs stay one click away.
  const primary = runs.filter((run) => pinned(run) || ["completed", "completed_with_drops"].includes(run.status));
  const other = runs.filter((run) => !primary.includes(run));
  primary.sort((a, b) => Number(pinned(b)) - Number(pinned(a)));
  const limit = window.innerWidth <= 680 && !state.historyExpanded ? Math.min(state.historyLimit, 4) : state.historyLimit;
  const shownPrimary = filter ? primary : primary.slice(0, limit);
  const visible = [...shownPrimary, ...(filter || state.showOtherRuns ? other : [])];
  if (!runs.length) container.append(el("p", "muted small", filter ? "No matching runs." : "No saved runs yet. Start with the example pack."));
  for (const run of visible) {
    const [label, tone] = statusLabel(run.status);
    const item = button("", `history-item${run.id === selectedId ? " active" : ""}`, () => { toggleRuns(false); loadRun(run.id); });
    item.setAttribute("aria-label", `${run.ticker || "Research"}, ${run.id}, ${label}`);
    if (run.id === selectedId) item.setAttribute("aria-current", "page");
    append(item,
      append(el("span", "history-top"), el("span", "history-ticker", run.ticker || "Research"), el("span", `history-status-dot ${tone}`)),
      append(el("span", "history-meta"), el("span", "", displayDate(run.as_of)), el("span", "", label)),
      el("span", "history-id", run.id));
    container.append(item);
  }
  if (!filter && shownPrimary.length < primary.length) {
    container.append(button(`Show ${primary.length - shownPrimary.length} more completed runs`, "text-button history-load-more", () => { state.historyLimit = primary.length; state.historyExpanded = true; renderHistory(); }));
  }
  if (!filter && other.length) {
    const text = state.showOtherRuns ? "Hide failed and unchecked runs" : `Show ${other.length} failed or unchecked runs`;
    container.append(button(text, "text-button history-load-more", () => { state.showOtherRuns = !state.showOtherRuns; renderHistory(); }));
  }
  if (focusedIndex >= 0) {
    const controls = [...container.querySelectorAll("button")];
    (controls[Math.min(focusedIndex, controls.length - 1)] || $("history-filter")).focus({ preventScroll: true });
  }
}

// Saved runs live in a drawer so the brief keeps the full width.
function toggleRuns(open = !document.querySelector(".workspace").classList.contains("runs-open")) {
  document.querySelector(".workspace").classList.toggle("runs-open", open);
  $("runs-toggle").setAttribute("aria-expanded", String(open));
  $("runs-backdrop").hidden = !open;
  $("run-drawer").inert = !open;
  $("run-drawer").setAttribute("aria-modal", String(open));
  $("main-content").inert = open;
  document.querySelector(".topbar").inert = open;
  document.querySelector(".skip-link").inert = open;
  if (open) $("history-filter").focus();
  else $("runs-toggle").focus({ preventScroll: true });
}

async function loadRun(id) {
  const request = ++state.loading; clearError();
  try {
    const data = await api(`/api/runs/${encodeURIComponent(id)}`);
    if (request !== state.loading) return;
    state.data = data; state.selectedClaim = null; state.selectedSource = null;
    setMode("report"); renderReport(); renderHistory();
  } catch (error) { if (request === state.loading) showError(error); }
}

function renderReport() {
  const data = state.data, run = data.run, audit = data.audit || {};
  setText("report-ticker", run.ticker || "Company research"); setText("report-title", run.company || run.ticker || "Research brief");
  const [label, tone] = statusLabel(run.status); const badge = $("report-status"); badge.textContent = label; badge.className = `status-pill ${tone}`;
  setText("report-description", `As of ${displayDate(run.as_of)} · ${run.id}${typeof run.word_count === "number" ? ` · ${run.word_count} words` : ""}`);
  renderSummary();
  document.title = `${run.ticker || "Company research"} · Research desk`;
  $("saved-demo-note").hidden = run.id !== state.status?.demo_run;
  const download = $("export-brief"); download.href = `/api/runs/${encodeURIComponent(run.id)}/brief.md`; download.hidden = !data.brief_markdown;
  download.download = `${run.id}-brief.md`;
  const warnings = $("report-warnings"); warnings.replaceChildren();
  const messages = {
    completed_with_drops: "Some proposed claims were dropped. The brief shows the retained claims; review the omissions in Run details.",
    completed_unchecked: "This is an unchecked baseline. Its text has not passed the structured claim or evidence checks.",
    incomplete: "This report is incomplete. A finished process does not mean the required sections are ready to use.",
    verification_unavailable: "Verification was unavailable. Unresolved items are not verified findings.",
    failed: "This run failed. Any saved partial material is not a completed research brief.",
  };
  if (messages[run.status]) warnings.append(note(messages[run.status], tone === "bad" ? "danger" : "warning"));
  if (audit.error || audit.error_type) warnings.append(note(`Run error${audit.error_type ? ` (${audit.error_type})` : ""}: ${textValue(audit.error || audit.error_type)}`, "danger"));
  if (audit.unavailable && run.status !== "verification_unavailable") warnings.append(note("Some verification was unavailable. Read the unresolved checks before using this report.", "warning"));
  for (const warning of audit.warnings || []) warnings.append(note(textValue(warning), "warning"));
  if (data.evidence_available === false) warnings.append(note(data.evidence_note || "Source bytes are unavailable for this saved run. The report remains visible, but its passages cannot be inspected here.", "warning"));
  setText("brief-count", allClaims().length || ""); setText("sources-count", data.sources?.length || "");
  renderBrief(); renderSources(); renderCoverage(); renderDetails(); renderInspector(); switchTab(state.tab);
}

function renderSummary() {
  const target = $("report-summary"); target.replaceChildren();
  const data = state.data, claims = allClaims();
  if (!claims.length) return;
  const dropped = (data.audit?.dropped || []).length;
  const unconfirmed = claims.filter((claim) => (claim.checks?.kind_confirmed ?? claim.kind_confirmed) === false).length;
  const notChecked = claims.filter((claim) => (claim.checks?.kind_confirmed ?? claim.kind_confirmed) == null).length;
  const sources = data.sources || [], excluded = sources.filter((source) => source.status === "excluded").length;
  const unresolved = sources.filter((source) => !["evidence", "excluded"].includes(source.status)).length;
  const review = (data.coverage?.items || []).filter((item) => ["partial", "review_needed"].includes(item.status)).length;
  const parts = [[`${claims.length} claims`, false], [`${dropped} dropped`, dropped > 0],
    [`${unconfirmed} statement type${unconfirmed === 1 ? "" : "s"} unconfirmed`, unconfirmed > 0]];
  if (notChecked) parts.push([`${notChecked} statement type${notChecked === 1 ? "" : "s"} not checked`, true]);
  parts.push([`${excluded} of ${sources.length} sources excluded`, false]);
  if (unresolved) parts.push([`${unresolved} source${unresolved === 1 ? "" : "s"} not resolved`, true]);
  parts.push([`${review} coverage topics to review`, review > 0]);
  for (const [text, flag] of parts) target.append(el("span", `summary-item${flag ? " flag" : ""}`, text));
}

function renderBrief() {
  const panel = $("panel-brief"); panel.replaceChildren();
  const claims = allClaims();
  if (!claims.length && state.data.brief_markdown) {
    append(panel, note("Original saved Markdown, shown as written. Claim-level evidence is unavailable for this output.", "warning"), el("pre", "literal-markdown", state.data.brief_markdown));
    return;
  }
  if (!claims.length) {
    append(panel, append(el("div", "empty-state"), el("h2", "", "No completed brief"), el("p", "", "This run kept no claims. Run details shows what happened.")));
    return;
  }
  for (const section of state.data.sections || []) {
    const wrap = el("section", "brief-section");
    append(wrap, append(el("div", "section-heading"), el("h2", "", section.title)));
    const list = el("ul", "claim-list");
    for (const claim of section.claims || []) {
      const card = button("", "claim-card", () => selectClaim(claim.id, window.innerWidth <= 930)); card.dataset.claimId = claim.id;
      card.setAttribute("aria-pressed", "false"); card.setAttribute("aria-controls", "evidence-inspector");
      append(card, el("span", "claim-text", claim.text));
      const foot = el("span", "claim-footer");
      const refs = [...new Set((claim.cites || (claim.evidence || []).map((e) => e.unit_id)).map((id) => String(id).split(".")[0]))];
      refs.forEach((id) => foot.append(el("span", "source-ref", id)));
      foot.append(el("span", "claim-kind", kindNames[claim.kind] || "Statement"));
      card.append(foot);
      if (claim.kind_confirmed === false || claim.checks?.kind_confirmed === false) card.append(el("span", "kind-warning", "Statement type unconfirmed"));
      card.addEventListener("keydown", (event) => {
        if (!["ArrowDown", "ArrowUp"].includes(event.key)) return;
        const cards = [...panel.querySelectorAll(".claim-card")], position = cards.indexOf(card);
        const next = cards[position + (event.key === "ArrowDown" ? 1 : -1)];
        if (next) { event.preventDefault(); next.focus(); selectClaim(next.dataset.claimId); }
      });
      list.append(append(el("li", "claim-item"), card));
    }
    wrap.append(list);
    if (!(section.claims || []).length) wrap.append(el("p", "section-empty", "No retained claim in this section."));
    for (const message of section.notices || []) wrap.append(append(el("div", "system-notice"), el("span", "eyebrow", "System notice"), el("span", "", textValue(message))));
    panel.append(wrap);
  }
  const sources = state.data.sources || [];
  const excluded = sources.filter((source) => source.status === "excluded").length;
  const unresolved = sources.filter((source) => !["evidence", "excluded"].includes(source.status)).length;
  const sourceSection = el("section", "brief-section");
  append(sourceSection, append(el("div", "section-heading"), el("h2", "", "Sources")),
    el("p", "tab-description", `${sources.length} documents, ${excluded} excluded${unresolved ? `, ${unresolved} not resolved` : ""}.`),
    button("View sources →", "text-button", () => switchTab("sources")));
  panel.append(sourceSection);
  if (state.data.glossary?.length) {
    const glossary = el("details", "glossary"); glossary.open = true; glossary.append(el("summary", "", "Terms used"));
    const definitions = el("dl");
    for (const item of state.data.glossary) append(definitions, el("dt", "", item.term), el("dd", "", item.definition));
    append(glossary, definitions, el("p", "glossary-note", "General definitions, not claims about the company.")); panel.append(glossary);
  }
}

function selectClaim(id, focusInspector = false) {
  const claim = allClaims().find((item) => item.id === id); if (!claim) return;
  state.selectedClaim = claim; state.selectedSource = null;
  document.querySelectorAll(".claim-card").forEach((card) => { const selected = card.dataset.claimId === id; card.classList.toggle("selected", selected); card.setAttribute("aria-pressed", String(selected)); });
  renderInspector();
  if (focusInspector) { $("inspector-title").focus({ preventScroll: true }); $("evidence-inspector").scrollIntoView({ behavior: "smooth", block: "start" }); }
}
function checkRow(label, value, tone) { return append(el("div", "check-row"), el("span", "", label), el("span", `check-value ${tone}`, value)); }
function linkForSource(source) {
  if (!source?.url) return el("span", "source-url", "No source URL supplied");
  try {
    const parsed = new URL(source.url);
    if (!["http:", "https:"].includes(parsed.protocol)) return el("span", "source-url", "Source URL is not an HTTP(S) address.");
    if (parsed.hostname === "example" || parsed.hostname.endsWith(".example") || ["example.com", "example.org", "example.net"].includes(parsed.hostname)) return el("span", "source-url", `${source.url} · supplied example address`);
    const link = el("a", "source-link", "Open source ↗"); link.href = parsed.href; link.target = "_blank"; link.rel = "noopener noreferrer"; return link;
  } catch { return el("span", "source-url", "Source URL is not a valid address."); }
}
function renderEvidence(entry) {
  const source = sourceById(entry.source_id); const wrap = el("div", "evidence-entry");
  append(wrap, append(el("div", "evidence-source-heading"), el("span", "source-ref", entry.source_id), el("strong", "", source?.name || source?.title || "Source passage")), el("p", "evidence-meta", `${entry.unit_id || ""}${source?.published ? ` · ${displayDate(source.published)}` : ""}${source?.type ? ` · ${source.type}` : ""}`), el("span", "quote-label", "Cited passage"), quoteNode(entry.quote));
  if (entry.context && entry.context !== entry.quote) { const details = el("details", "context-toggle"); append(details, el("summary", "", "Read surrounding context"), el("pre", "context-text", entry.context)); wrap.append(details); }
  if (source) append(wrap, linkForSource(source), button("View this source →", "text-button inspector-source-action", () => { switchTab("sources"); selectSource(source.id); }));
  return wrap;
}
function renderInspector() {
  const container = $("inspector-content"); container.replaceChildren();
  if (state.selectedSource) { renderSourceInspector(state.selectedSource); return; }
  const claim = state.selectedClaim;
  setText("inspector-title", "Evidence");
  if (!claim) { append(container, append(el("div", "inspector-empty"), el("p", "", "Select a claim to see its source passages and check results."), el("span", "small muted", "A passed check is not a guarantee of truth."))); return; }
  const body = el("div", "inspector-body"); append(body, el("p", "inspector-claim", claim.text));
  body.append(button("← Return to selected claim", "text-button return-to-claim", () => {
    switchTab("brief");
    const card = [...document.querySelectorAll(".claim-card")].find((node) => node.dataset.claimId === claim.id);
    if (card) { card.focus({ preventScroll: true }); card.scrollIntoView({ block: "center" }); }
  }));
  const checks = claim.checks || {}, grid = el("div", "check-grid");
  const codeLabels = { passed: ["Passed", "good"], not_run: ["Not run", "warn"], failed: ["Failed", "bad"] };
  const supportLabels = { supported: ["Support check passed", "good"], not_run: ["Not checked", "warn"], unavailable: ["Unavailable", "bad"], failed: ["Did not pass", "bad"] };
  grid.append(checkRow("Citation & numeric checks", ...(codeLabels[checks.code] || ["Not reported", "warn"])));
  grid.append(checkRow("Evidence support", ...(supportLabels[checks.support] || ["Not reported", "warn"])));
  const kindConfirmed = checks.kind_confirmed ?? claim.kind_confirmed;
  grid.append(checkRow("Statement type", kindConfirmed === true ? "Confirmed by checker" : "Unconfirmed", kindConfirmed === true ? "good" : "warn"));
  append(body, grid, el("p", "support-note", "Recorded checks, not proof. Read the passage for dates, scope and qualifications."));
  if (state.data.evidence_available === false) body.append(note(state.data.evidence_note || "The original evidence is unavailable for this run. Passage text cannot be verified against the saved source hashes.", "warning"));
  else if (!(claim.evidence || []).length) body.append(note("No original passage is available for this claim. The report text is preserved.", "warning"));
  else (claim.evidence || []).forEach((entry) => body.append(renderEvidence(entry)));
  container.append(body);
}

function renderSources() {
  const panel = $("panel-sources"); panel.replaceChildren();
  append(panel, el("h2", "coverage-heading", "Sources"), el("p", "tab-description", "Admitted, excluded and unresolved documents. Source types come from document metadata and are not verified."));
  const list = el("div", "sources-list");
  for (const source of state.data.sources || []) {
    const card = button("", "source-card paper", () => selectSource(source.id)); card.dataset.sourceId = source.id;
    const sourceStatus = source.status === "evidence" ? ["Admitted", "good"] : source.status === "excluded" ? ["Excluded", "warn"] : ["Unresolved", "warn"];
    append(card, append(el("div", "source-card-top"), el("span", "source-ref", source.id), pill(...sourceStatus)), el("h3", "", source.title || source.name || source.id), el("p", "", source.name || ""), append(el("div", "source-facts"), el("span", "", displayDate(source.published)), el("span", "", source.type || "Unknown source type"), el("span", "tier-label", `Tier ${source.tier ?? "?"}`)));
    if (source.reason) card.append(el("p", "source-reason", source.reason));
    if (source.age_days > 365) card.append(el("p", "source-reason", "Older than one year at the research date. Check whether a newer disclosure supersedes it."));
    if (source.removals?.length) card.append(el("p", "source-reason", "Hidden-content removal recorded"));
    list.append(card);
  }
  panel.append(list);
}
function selectSource(id) {
  const source = sourceById(id); if (!source) return;
  state.selectedSource = source; state.selectedClaim = null;
  document.querySelectorAll(".source-card").forEach((card) => card.classList.toggle("selected", card.dataset.sourceId === id));
  document.querySelectorAll(".claim-card").forEach((card) => { card.classList.remove("selected"); card.setAttribute("aria-pressed", "false"); });
  renderInspector();
  if (window.innerWidth <= 930) { $("inspector-title").focus({ preventScroll: true }); $("evidence-inspector").scrollIntoView({ block: "start" }); }
}
function renderSourceInspector(source) {
  setText("inspector-title", `${source.id} · Source detail`);
  const body = el("div", "inspector-body"); append(body, el("p", "inspector-claim", source.title || source.name), el("p", "evidence-meta", `${source.type || "Unknown type"} · Published ${displayDate(source.published)}`), linkForSource(source), note(source.reason || "No routing explanation recorded."));
  for (const warning of source.warnings || []) body.append(note(textValue(warning), "warning"));
  if (source.removals?.length) body.append(note(`Hidden-content removals: ${source.removals.map((r) => `${r.kind || "content"} (${r.count ?? "?"} characters)`).join(", ")}. Removed instructions are not displayed as evidence.`));
  if (state.data.evidence_available === false) body.append(note(state.data.evidence_note || "The original source is unavailable.", "warning"));
  else {
    const units = source.units || [];
    body.append(el("p", "support-note", `${units.length} numbered passages. Source metadata and passage content are supplied evidence, not verified facts.`));
    for (const unit of units) {
      const details = el("details", "context-toggle evidence-entry");
      append(details, el("summary", "", `${unit.id} · ${String(unit.text || "Passage").slice(0, 82)}${String(unit.text || "").length > 82 ? "…" : ""}`), quoteNode(unit.quote || unit.text || ""));
      if (unit.context && unit.context !== unit.quote) append(details, el("span", "quote-label", "Surrounding context"), el("pre", "context-text", unit.context));
      body.append(details);
    }
  }
  $("inspector-content").append(body);
}

function renderCoverage() {
  const panel = $("panel-coverage"); panel.replaceChildren();
  const coverage = state.data.coverage || {};
  const order = { partial: 0, review_needed: 1, mentioned: 2, not_found: 3 };
  const labels = { partial: ["Partly covered", "warn"], review_needed: ["No mention found", "warn"], mentioned: ["Mentioned", "neutral"], not_found: ["No source match", "neutral"] };
  const how = el("details", "coverage-how");
  append(how, el("summary", "", "How this check works"), el("p", "", coverage.notice || ""), el("p", "", coverage.method || ""));
  append(panel, el("h2", "coverage-heading", "Coverage"),
    el("p", "tab-description", "Topics in recent primary sources compared with the brief. A review aid, not a completeness or accuracy score."), how);
  // Topics needing attention come first.
  const items = [...(coverage.items || [])].sort((a, b) => (order[a.status] ?? 9) - (order[b.status] ?? 9));
  const list = el("div", "paper coverage-list");
  for (const item of items) {
    const row = el("article", "coverage-row");
    append(row, append(el("div", "coverage-top"), el("h3", "", item.label), pill(...(labels[item.status] || ["Not assessed", "neutral"]))));
    const missing = item.missing_figures || [];
    if (item.status === "partial") row.append(el("p", "coverage-missing", `Figures to review: ${missing.join(", ")}`));
    if (item.status === "review_needed") row.append(el("p", "coverage-missing", `No matching mention found in the brief.${missing.length ? ` Source figures to review: ${missing.join(", ")}` : ""}`));
    if (item.status === "not_found") row.append(el("p", "coverage-note", "No keyword match in the checked sources."));
    const links = el("div", "coverage-links");
    for (const id of item.claim_ids || []) {
      const section = state.data.sections.find((value) => value.claims.some((claim) => claim.id === id));
      const position = section ? section.claims.findIndex((claim) => claim.id === id) + 1 : 0;
      links.append(button(section ? `${section.title} ${position}` : "Brief mention", "mini-button", () => { switchTab("brief"); selectClaim(id, true); }));
    }
    if ((item.claim_ids || []).length) row.append(links);
    if (item.source_hits?.length) {
      const uncited = item.source_hits.filter((hit) => hit.cited === false).length;
      const details = el("details", "coverage-evidence");
      details.append(el("summary", "", `${item.source_hits.length} source passage${item.source_hits.length === 1 ? "" : "s"}${uncited ? `, ${uncited} not cited` : ""}`));
      for (const hit of item.source_hits) {
        append(details,
          append(el("div", "hit-head"), el("span", "source-ref", hit.unit_id || hit.source_id), el("span", hit.cited ? "hit-cited" : "hit-uncited", hit.cited ? "cited" : "not cited")),
          quoteNode(hit.quote));
      }
      row.append(details);
    }
    list.append(row);
  }
  panel.append(list);
  if (!(coverage.items || []).length) panel.append(note("Coverage is not available for this saved run."));
}

function detailCard(title, pairs) {
  const card = el("section", "paper details-card"), dl = el("dl", "details-dl"); card.append(el("h3", "", title));
  for (const [key, value] of pairs) append(dl, el("dt", "", key), el("dd", "", textValue(value)));
  return append(card, dl);
}
function renderDetails() {
  const panel = $("panel-details"); panel.replaceChildren(); const run = state.data.run, audit = state.data.audit || {};
  append(panel, el("p", "tab-description", "Recorded process, repairs and provider waits. These are automated checks, not human verification."));
  const ids = audit.model_ids || {}, returned = ids.returned || {}, review = audit.human_review || {};
  const model = (requested, got) => !requested ? null : `${requested}${(got || []).length && got.join(", ") !== requested ? ` (returned ${got.join(", ")})` : ""}`;
  const humanReview = review.supported === "pending" && review.traps === "pending" ? "Pending" : `Support: ${review.supported ?? "not reported"} · Traps: ${review.traps ?? "not reported"}`;
  append(panel, detailCard("Run record", [["Run", run.id], ["Design arm", run.arm], ["Outcome", statusLabel(run.status)[0]], ["As of", displayDate(run.as_of)], ["Length", typeof run.word_count === "number" ? `${run.word_count} words before Sources` : null], ["Elapsed time", formatNumber(audit.elapsed_s ?? run.elapsed_s, " seconds")], ["Manual edits", audit.manual_edits_to_brief === false ? "No manual edits recorded" : audit.manual_edits_to_brief === true ? "Manual edits recorded" : "Not reported"], ["Human review", humanReview]]));
  append(panel, detailCard("Models & calls", [["Writer model", model(ids.writer_requested, returned.writer) || run.writer_model], ["Decision model", model(ids.verifier_requested, returned.jev) || run.verifier_model], ["Writer attempts", audit.writer_attempts], ["Decision attempts", audit.verifier_attempts], ["Billed cost", typeof audit.billed_cost_usd === "number" ? `$${audit.billed_cost_usd.toFixed(4)}` : "Not reported by provider"]]));
  if (audit.error || audit.error_type || audit.repair_error || audit.draft_error) panel.append(detailCard("Recorded errors", [["Run error", audit.error], ["Error type", audit.error_type], ["Draft error", audit.draft_error], ["Repair error", audit.repair_error]]));
  if (audit.passes?.length) {
    const card = el("section", "paper details-card"); card.append(el("h3", "", "Draft & repair"));
    for (const pass of audit.passes) append(card, append(el("div", "audit-stage"), el("strong", "", pass.stage), el("span", "", `${pass.accepted ?? 0} accepted · ${pass.failed ?? 0} failed · ${pass.repair_requested ?? 0} repair requests`)));
    panel.append(card);
  }
  if (audit.dropped?.length) {
    const card = el("section", "paper details-card"), list = el("ul", "audit-list"); card.append(el("h3", "", "Dropped claims"));
    for (const item of audit.dropped) list.append(append(el("li", "audit-item"), el("strong", "", item.text || "Structural issue"), el("span", "", (item.errors || []).map(textValue).join(" · "))));
    append(panel, append(card, list));
  }
  if (audit.conflicts?.length) {
    const card = el("section", "paper details-card"), list = el("ul", "audit-list"); card.append(el("h3", "", "Source conflicts recorded"));
    for (const conflict of audit.conflicts) list.append(append(el("li", "audit-item"), el("strong", "", conflict.text || "Proposed claim"), el("span", "", `Cited sources: ${(conflict.cited_sources || []).join(", ")}; higher-tier source: ${conflict.higher_tier_source || "not reported"}`)));
    append(panel, append(card, list));
  }
  if (audit.transport_errors?.length) {
    const card = el("section", "paper details-card"), list = el("ul", "audit-list"); card.append(el("h3", "", "Provider waits & failures"));
    for (const item of audit.transport_errors) {
      const reasons = { minute_window_cooldown: "rate limited, retried after the minute window", retry_wait_exceeds_60s_bound: "requested wait over 60 seconds, not retried",
        daily_request_limit_exhausted: "daily request limit reached", attempt_limit_reached: "retry limit reached", server_error_backoff: "server error, retried" };
      const details = [`${item.provider || "Provider"}, attempt ${item.attempt ?? "?"}`, item.http_status ? `HTTP ${item.http_status}` : item.error_type, reasons[item.retry_reason] || item.retry_reason];
      if (typeof item.provider_requested_wait_s === "number") details.push(`provider requested ${formatNumber(item.provider_requested_wait_s)} seconds`);
      if (typeof item.retry_delay_s === "number") details.push(`actual retry wait ${formatNumber(item.retry_delay_s)} seconds`);
      list.append(el("li", "audit-item", details.filter(Boolean).join(" · ")));
    }
    append(panel, append(card, list));
  }
  const other = [...(audit.warnings || []), ...(audit.structure_failures || []).map((item) => textValue(item.errors || item))];
  if (other.length) { const card = el("section", "paper details-card"); card.append(el("h3", "", "Warnings & structure")); const list = el("ul", "audit-list"); other.forEach((item) => list.append(el("li", "", textValue(item)))); append(panel, append(card, list)); }
  const exportLink = el("a", "button secondary", "Export audit JSON ↓"); exportLink.href = `/api/runs/${encodeURIComponent(run.id)}/audit.json`; exportLink.download = `${run.id}-audit.json`; panel.append(exportLink);
}

function switchTab(tab) {
  if (!["brief", "sources", "coverage", "details"].includes(tab)) return;
  state.tab = tab;
  for (const node of document.querySelectorAll("[data-tab]")) { const active = node.dataset.tab === tab; node.classList.toggle("active", active); node.setAttribute("aria-selected", String(active)); node.tabIndex = active ? 0 : -1; }
  for (const key of ["brief", "sources", "coverage", "details"]) $(`panel-${key}`).hidden = key !== tab;
  $("evidence-inspector").hidden = tab === "details";
  $("research-layout").classList.toggle("details-layout", tab === "details");
}

function renderProviderChoices() {
  const status = state.status; if (!status) return;
  setText("writer-model", status.writer?.model || "Writer model not configured");
  setText("writer-ready", status.writer?.configured ? "Writer credential configured" : "Writer credential missing. Saved runs remain available.");
  const current = document.querySelector('input[name="route"]:checked')?.value;
  const options = $("route-options"); options.replaceChildren(el("legend", "", "Decision provider"));
  const routes = (status.verifiers || []).filter((route) => ["free", "reference"].includes(route.id));
  const choice = routes.find((route) => route.id === current && route.configured)?.id || routes.find((route) => route.configured)?.id;
  for (const route of routes) {
    const label = el("label", "route-card"), input = el("input"); input.type = "radio"; input.name = "route"; input.value = route.id; input.disabled = !route.configured; input.checked = route.id === choice;
    const content = el("span"); append(content, el("strong", "", route.label || (route.id === "free" ? "Free Zen" : "TypeSafe reference")), el("small", "route-model", route.model), el("small", "", route.configured ? (route.id === "free" ? "Public route configured" : "Credential configured") : "Credential not configured"), el("small", "", route.id === "free" ? "Shared free quota. Requests may be unavailable." : "Uses your configured TypeSafe account; may use paid credit."));
    input.addEventListener("change", updateReadiness); append(options, append(label, input, content));
  }
  const limits = status.limits || {}; setText("file-limit-copy", `Up to ${limits.files || 24} files · ${Math.round((limits.file_bytes || 200000) / 1000)} KB each · ${Math.round((limits.total_bytes || 1000000) / 1000)} KB total`);
  updateReadiness();
}
function updateReadiness() {
  const route = document.querySelector('input[name="route"]:checked')?.value;
  const running = state.job && ["queued", "running"].includes(state.job.state);
  let message = "Ready to request a new brief. Provider availability is checked when the run starts.";
  if (!state.status) message = "Cannot read server configuration. Refresh the connection to start a run.";
  else if (!state.status.writer?.configured) message = "A writer credential is missing. Configure GROQ_API_KEY or WRITER_API_KEY on the local server; saved research remains usable.";
  else if (!route) message = "No decision provider is configured. Add the required server credential, then refresh the connection.";
  else if (running) message = "A run is already in progress. Wait for its recorded outcome before starting another.";
  else if (state.submitting) message = "Preparing your documents and starting the run…";
  setText("run-readiness", message);
  $("launch-run").disabled = !state.status?.writer?.configured || !route || Boolean(running) || state.submitting;
}
function updateFiles() {
  const list = $("file-list"); list.replaceChildren();
  for (const file of $("documents").files) list.append(append(el("li", ""), el("span", "", file.name), el("span", "", `${Math.ceil(file.size / 1000)} KB`)));
}
async function readDocuments() {
  const files = [...$("documents").files], limits = state.status?.limits || { files: 24, file_bytes: 200000, total_bytes: 1000000 };
  if (!files.length) throw new Error("Choose at least one Markdown document.");
  if (files.length > limits.files) throw new Error(`Choose no more than ${limits.files} documents.`);
  if (files.reduce((sum, file) => sum + file.size, 0) > limits.total_bytes) throw new Error("The selected documents exceed the total size limit.");
  const names = new Set(), documents = [];
  for (const file of files) {
    if (!file.name.endsWith(".md")) throw new Error(`${file.name}: use the lowercase .md extension for Markdown documents.`);
    if (names.has(file.name.toLowerCase())) throw new Error(`Duplicate filename: ${file.name}. Rename one of the documents first.`);
    if (file.size > limits.file_bytes) throw new Error(`${file.name} exceeds the per-file size limit.`);
    names.add(file.name.toLowerCase());
    let content;
    try { content = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(await file.arrayBuffer()); }
    catch { throw new Error(`${file.name} is not valid UTF-8 text. Save it as UTF-8 Markdown and try again.`); }
    documents.push({ name: file.name, content });
  }
  return documents;
}
async function submitResearch(event) {
  event.preventDefault(); if (state.submitting || $("launch-run").disabled) return;
  state.submitting = true; $("form-error").hidden = true; updateReadiness();
  try {
    const useExample = document.querySelector('input[name="pack"]:checked').value === "example";
    const payload = { ticker: $("ticker").value.trim(), as_of: $("as-of").value, route: document.querySelector('input[name="route"]:checked').value, use_example: useExample, documents: useExample ? [] : await readDocuments() };
    const job = await api("/api/runs", { method: "POST", body: JSON.stringify(payload) });
    state.jobEventCount = 0; startJob(job); $("job-panel").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) { setText("form-error", errorMessage(error)); $("form-error").hidden = false; }
  finally { state.submitting = false; updateReadiness(); }
}
function renderJob() {
  const job = state.job; if (!job) return;
  $("job-panel").hidden = false;
  const running = ["queued", "running"].includes(job.state);
  setText("job-title", running ? (job.state === "queued" ? "Research is queued" : "Research is running") : job.state === "failed" ? "The run stopped" : "Run finished");
  const label = running ? [job.state === "queued" ? "Queued" : "In progress", "neutral"] : job.state === "failed" ? ["Stopped", "bad"] : statusLabel(job.status);
  $("job-state").textContent = label[0]; $("job-state").className = `status-pill ${label[1]}`;
  const list = $("job-events"), events = job.events || [];
  if (events.length < state.jobEventCount) { list.replaceChildren(); state.jobEventCount = 0; }
  if (!state.jobEventCount) list.replaceChildren();
  for (const event of events.slice(state.jobEventCount)) {
    const item = el("li", "");
    if (event.at) { const date = new Date(event.at); if (!Number.isNaN(date.valueOf())) item.append(el("span", "event-time", date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" }))); }
    item.append(el("span", "", event.message || "Progress recorded")); list.append(item);
  }
  state.jobEventCount = events.length;
  $("job-error").hidden = !job.error; setText("job-error", textValue(job.error));
  $("job-view").hidden = running || !job.run_id;
  updateReadiness();
}
function startJob(job) {
  clearTimeout(state.jobTimer); state.job = job; renderJob();
  if (["queued", "running"].includes(job.state)) state.jobTimer = setTimeout(pollJob, 1500);
}
async function pollJob() {
  if (!state.job?.id) return;
  try {
    const job = await api(`/api/jobs/${encodeURIComponent(state.job.id)}`); state.job = job; renderJob();
    if (["queued", "running"].includes(job.state)) state.jobTimer = setTimeout(pollJob, 1800);
    else {
      const result = await api("/api/runs"); state.runs = result.runs || []; renderHistory();
      if (job.run_id) await loadRun(job.run_id);
      updateReadiness();
    }
  } catch (error) {
    if (error.status === 404) {
      state.job = { ...state.job, state: "failed", error: "The workspace no longer tracks this job. Check its saved run before starting another." };
      renderJob();
      await refreshConnection();
      return;
    }
    setText("job-error", `Progress connection interrupted: ${errorMessage(error)} Retrying the status request.`); $("job-error").hidden = false;
    state.jobTimer = setTimeout(pollJob, 5000);
  }
}
async function refreshConnection() {
  clearError();
  try {
    const [status, history] = await Promise.all([api("/api/status"), api("/api/runs")]);
    state.status = status; state.runs = history.runs || []; renderHistory(); renderProviderChoices();
    if (!$("ticker").value) $("ticker").value = status.defaults?.ticker || "";
    if (!$("as-of").value) $("as-of").value = status.defaults?.as_of || "";
    if (status.active_job) { state.jobEventCount = 0; startJob(status.active_job); }
    else if (state.job && ["queued", "running"].includes(state.job.state)) {
      clearTimeout(state.jobTimer);
      state.job = { ...state.job, state: "failed", error: "No active job was found on the server. Check the saved run for its last recorded outcome." };
      renderJob();
    }
    if (!state.data) {
      const demo = typeof status.demo_run === "string" ? status.demo_run : status.demo_run?.id;
      const id = state.runs.find((run) => run.id === demo)?.id || state.runs[0]?.id;
      if (id) await loadRun(id); else { setText("report-title", "No saved runs"); setText("report-description", "Start a new research run."); setText("report-status", "No saved runs"); }
    }
  } catch (error) { showError(error); updateReadiness(); }
}

$("new-research-button").addEventListener("click", () => { setMode("new"); $("ticker").focus({ preventScroll: true }); $("new-research").scrollIntoView({ behavior: "smooth", block: "start" }); });
$("back-to-report").addEventListener("click", () => setMode("report"));
$("runs-toggle").addEventListener("click", () => toggleRuns());
$("runs-close").addEventListener("click", () => toggleRuns(false));
$("runs-backdrop").addEventListener("click", () => toggleRuns(false));
$("run-drawer").addEventListener("keydown", (event) => {
  if (event.key === "Escape") { event.preventDefault(); toggleRuns(false); return; }
  if (event.key !== "Tab") return;
  const controls = [...$("run-drawer").querySelectorAll('button:not([disabled]), a[href], input:not([disabled]), [tabindex]:not([tabindex="-1"])')]
    .filter(node => node.getClientRects().length > 0);
  const first = controls[0], last = controls[controls.length - 1];
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
});
$("connection-refresh").addEventListener("click", refreshConnection);
$("history-filter").addEventListener("input", renderHistory);
$("documents").addEventListener("change", updateFiles);
$("research-form").addEventListener("submit", submitResearch);
$("job-view").addEventListener("click", () => { if (state.job?.run_id) loadRun(state.job.run_id); });
document.querySelectorAll('input[name="pack"]').forEach((input) => input.addEventListener("change", () => { $("upload-area").hidden = input.value !== "upload" || !input.checked; }));
document.querySelectorAll("[data-tab]").forEach((tab) => {
  tab.addEventListener("click", () => switchTab(tab.dataset.tab));
  tab.addEventListener("keydown", (event) => {
    const tabs = [...document.querySelectorAll("[data-tab]")], index = tabs.indexOf(tab);
    let next;
    if (event.key === "ArrowRight") next = tabs[(index + 1) % tabs.length];
    if (event.key === "ArrowLeft") next = tabs[(index + tabs.length - 1) % tabs.length];
    if (event.key === "Home") next = tabs[0]; if (event.key === "End") next = tabs[tabs.length - 1];
    if (next) { event.preventDefault(); next.focus(); switchTab(next.dataset.tab); }
  });
});
let resizeTimer; window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(renderHistory, 120); });
refreshConnection();
