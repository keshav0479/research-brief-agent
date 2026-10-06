"""Ground each claim in selected passages, then check source-tier conflicts."""

from copy import deepcopy

from .clients import ProviderError


def _source_text(doc, citation, text):
    """Keep disclosure timing and source identity beside the retrieved text."""
    return (f"[{citation}]\nSource: {doc.source}\nSource type: {doc.source_type}\n"
            f"Published: {doc.published} (publication date, not necessarily the event date)\n\n{text}")


def _decide(state, selected, thresholds, decider, diagnostics, stage):
    """Recheck uncertain choices once. Agreement alone never clears the gate."""
    answers = decider.decide(state, selected)
    record = {"stage": stage, "answers": deepcopy(answers)}
    diagnostics.append(record)
    low = {name: deepcopy(question) for name, question in selected.items()
           if answers[name]["confidence"] < thresholds[name]}
    if low:
        for question in low.values():
            question["criteria"] = dict(reversed(list(question["criteria"].items())))
        retry = decider.decide(state, low)
        record["reversed_questions"] = list(low)
        record["retry_answers"] = deepcopy(retry)
        for name in low:
            if (retry[name]["choice"] == answers[name]["choice"]
                    and retry[name]["confidence"] >= thresholds[name]):
                answers[name] = retry[name]
            else:
                answers[name] = None
    return answers


def verify_claim(claim, documents, evidence_ids, section, questions, decider):
    """Return checks and a verified kind; never turn verifier failure into approval."""
    result = {"errors": [], "kind": claim["kind"], "diagnostics": [],
              "unavailable": False, "conflicts": []}
    by_unit = {uid: doc for doc in documents if doc.sid in evidence_ids
               for uid in doc.windows}
    cites = list(dict.fromkeys(claim["cites"]))
    if not cites or any(uid not in by_unit for uid in cites):
        result["errors"].append("Verification requires valid admitted evidence citations.")
        return result

    state = {
        "claim": claim["text"], "statement": claim["text"],
        "source_document": "\n\n".join(
            _source_text(by_unit[uid], uid, by_unit[uid].windows[uid]) for uid in cites),
    }
    thresholds = questions["thresholds"]
    relation_questions = questions["verify_open_questions" if section == "open_questions" else "verify"]
    kind_questions = questions["open_question_kinds" if section == "open_questions" else "kinds"]
    selected = {**deepcopy(relation_questions), **deepcopy(kind_questions)}
    try:
        answers = _decide(state, selected, thresholds, decider,
                          result["diagnostics"], "cited_evidence")
        relation, kind = answers["relation"], answers["kind"]
        if relation is None:
            result["errors"].append("Evidence support is unresolved after an order check.")
        elif relation["choice"] != "supports":
            result["errors"].append(f"Cited evidence relation: {relation['choice']}.")
        if kind is None:
            result["errors"].append("Statement kind is unresolved after an order check.")
        else:
            result["kind"] = kind["choice"]
            if section == "snapshot" and result["kind"] != "reported_fact":
                result["errors"].append("Snapshot statements must be reported facts.")

        if all(by_unit[uid].tier >= 3 for uid in cites):
            for doc in documents:
                if doc.sid not in evidence_ids or doc.tier > 2:
                    continue
                comparison = dict(state, source_document=_source_text(doc, doc.sid, doc.body))
                answer = _decide(comparison, deepcopy(relation_questions),
                                 thresholds, decider, result["diagnostics"],
                                 f"higher_tier:{doc.sid}")["relation"]
                if answer is None:
                    result["errors"].append(
                        f"Comparison with higher-tier source {doc.sid} is unresolved.")
                elif answer["choice"] == "contradicts":
                    conflict = {"higher_tier_source": doc.sid,
                                "cited_sources": sorted({by_unit[uid].sid for uid in cites}),
                                "confidence": answer["confidence"]}
                    result["conflicts"].append(conflict)
                    result["errors"].append(f"Higher-tier source {doc.sid} contradicts the claim.")
    except ProviderError as error:
        result["unavailable"] = True
        result["errors"].append("Verification unavailable; claim cannot be accepted.")
        result["diagnostics"].append({"stage": "provider_failure", "error": error.kind,
                                       "http_status": error.status})
    return result
