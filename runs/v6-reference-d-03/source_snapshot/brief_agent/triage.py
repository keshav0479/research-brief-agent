"""Route sources using explicit identifiers and optional typed decisions."""

import re
from urllib.parse import urlsplit

from .clients import ProviderError


def triage(documents, profile, questions, decider=None):
    """Return source routes. Diagnostic raw text never becomes writer evidence."""
    ticker = re.escape(profile["ticker"])
    token = re.compile(r"(?<![A-Za-z0-9_])" + ticker + r"(?![A-Za-z0-9_])", re.I)
    marker = re.compile(r"\(NSE:\s*" + ticker + r"\s*\)", re.I)
    hosts = {urlsplit(doc.url).hostname for doc in documents
             if doc.age_days >= 0 and marker.search(doc.body)} - {None, ""}
    thresholds = questions["thresholds"]
    routes = {}
    for doc in documents:
        diagnostics = {}
        route = {"status": "evidence", "reason": "", "diagnostics": diagnostics}
        routes[doc.sid] = route
        if doc.age_days < 0:
            route.update(status="excluded", reason="Published after the requested as-of date")
            continue
        hard = ("ticker" if token.search(doc.body) or token.search(doc.url) else
                "issuer_host" if urlsplit(doc.url).hostname in hosts else None)
        diagnostics["hard_identifier"] = hard
        if decider is None:
            route["reason"] = (f"Company matched by {hard}; semantic screening not run" if hard else
                               "Entity not checked; semantic screening not run")
            continue

        names = ["promotional_stock_tip", "addresses_ai_tools"]
        if not hard:
            names += ["same_company", "same_line_of_business"]
        try:
            answers = decider.decide(
                {"target_company": profile, "document": doc.body},
                {name: questions["triage"][name] for name in names})
            diagnostics["clean"] = answers
            diagnostics["clean_addresses_ai_tools"] = answers["addresses_ai_tools"]["noul"]
            if doc.removals:
                raw = decider.decide(
                    {"target_company": profile, "document": doc.raw_body},
                    {"addresses_ai_tools": questions["triage"]["addresses_ai_tools"]})
                diagnostics["raw_addresses_ai_tools"] = raw["addresses_ai_tools"]["noul"]
                diagnostics["raw_screen_reused"] = False
            else:
                diagnostics["raw_addresses_ai_tools"] = diagnostics["clean_addresses_ai_tools"]
                diagnostics["raw_screen_reused"] = True
        except ProviderError:
            diagnostics["verifier_unavailable"] = True
            route.update(status="unclear", reason="Document screening unavailable")
            continue

        exclusions = []
        if answers["promotional_stock_tip"]["noul"] >= thresholds["promotional"]:
            exclusions.append("Promotional stock-tip content")
        if answers["addresses_ai_tools"]["noul"] >= thresholds["ai_directed"]:
            exclusions.append("AI-directed instructions remain after hidden-content removal")
        if exclusions:
            route.update(status="excluded", reason="; ".join(exclusions))
        elif hard:
            route["reason"] = f"Company matched by {hard}; document screening passed"
        else:
            score = answers["same_company"]["score"]
            if score < thresholds["same_company_low"]:
                route.update(status="excluded", reason="Different company or business")
            elif score <= thresholds["same_company_high"]:
                route.update(status="unclear", reason="Company identity could not be confirmed")
            else:
                route["reason"] = "Company identity and document screening passed"
    return routes
