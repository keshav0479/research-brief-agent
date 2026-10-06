"""Deterministic checks, not a substitute for source entailment review.

Numbers are checked for value and unit in the cited quotes. This cannot bind a
number to its financial metric, denominator, company or reporting period.
Only explicit approximation words permit rounding, using ROUND_HALF_UP.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
import math
import re
from typing import Protocol
import unicodedata


class Document(Protocol):
    sid: str
    age_days: int
    published: str
    units: dict[str, str]
    quotes: dict[str, str]


_NUMBER = re.compile(r"(?<![\w.])[-+]?(?:\d(?:[\d,]*\d)?(?:\.\d+)?|\.\d+)(?!\d|\.\d)")
_PERIOD = re.compile(r"\b(?:Q\s*[1-4]|H\s*[12]|FY\s*\d{2,4})\b", re.I)
_YEAR = re.compile(r"\b(?:19|20|21)\d{2}\b")
_APPROX = re.compile(r"(?:\b(?:about|approximately|approx\.?|around|roughly)\s*|~\s*)(?:₹\s*|Rs\.?\s*)?$", re.I)
_DOWN = re.compile(r"\b(?:down|fell|fallen|declined|decreased|contracted|dropped|reduced)(?:\s+by)?\s*$", re.I)
_UP = re.compile(r"\b(?:up|rose|risen|increased|grew|expanded)(?:\s+by)?\s*$", re.I)
_ADVICE = re.compile(r"\b(?:buy|sell|hold|target|upside|multibagger)\b", re.I)
_KINDS = {"reported_fact", "management_view_or_forecast", "third_party_view"}
_TABLE_MONEY = re.compile(r"(?:₹|\bRs\.?)\s*(crore|lakh)\b", re.I)


def _table_currencies(text: str) -> dict[int, str]:
    """Infer money units only for rows beneath their own Markdown header."""
    lines = text.splitlines()
    result = {}
    currency = None
    for index, line in enumerate(lines):
        if not line.lstrip().startswith("|"):
            currency = None
            continue
        following = lines[index + 1] if index + 1 < len(lines) else ""
        cells = following.strip().strip("|").split("|")
        header = bool(_TABLE_MONEY.search(line)) or (
            following.lstrip().startswith("|") and all(
                re.fullmatch(r"\s*:?-{3,}:?\s*", cell) for cell in cells))
        if header:
            caption = lines[index - 1] if index and not lines[index - 1].lstrip().startswith("|") else ""
            match = _TABLE_MONEY.search(caption + "\n" + line)
            currency = match.group(1).lower() if match else None
        elif currency:
            result[index] = currency
    return result


@dataclass(frozen=True)
class _Value:
    text: str
    raw: Decimal
    factor: Decimal
    dimension: str
    precision: int
    approximate: bool

    @property
    def value(self) -> Decimal:
        return self.raw * self.factor


def _normalise(text: str) -> str:
    return unicodedata.normalize("NFKC", text).replace("−", "-")


def _numbers(text: str) -> list[_Value]:
    text = _normalise(text)
    # Mask period labels in place so offsets and surrounding units remain valid.
    text = _PERIOD.sub(lambda m: " " * len(m.group()), text)
    # A digit-to-digit hyphen is a range separator, not a negative sign.
    text = re.sub(r"(?<=\d)[ \t]*[-–][ \t]*(?=\d)", " ", text)
    table_currencies = _table_currencies(text)
    result = []
    for match in _NUMBER.finditer(text):
        token = match.group()
        before, after = text[:match.start()], text[match.end():]
        table_currency = table_currencies.get(text.count("\n", 0, match.start()))
        unit_after = after
        # Share a unit across a simple range: '15 to 17 percent'.
        range_tail = re.match(r"\s+(?:to\s+)?[-+]?\d[\d,]*(?:\.\d+)?\s*(percent|%|bps|basis points|crore|lakh)(?!\w)", after, re.I)
        if range_tail:
            unit_after = " " + range_tail.group(1)
        dimension, factor = "scalar", Decimal(1)
        unit = re.match(r"\s*(%|percent\b|bps\b|basis\s+points?\b|crores?\b|lakhs?\b|times\b|x\b)", unit_after, re.I)
        if unit:
            label = unit.group(1).lower()
            if label in {"%", "percent"}:
                dimension = "percent"
            elif label == "bps" or label.startswith("basis"):
                dimension = "bps"
            elif label.startswith(("crore", "lakh")):
                dimension = "rupees"
                factor = Decimal(10_000_000 if label.startswith("crore") else 100_000)
            else:
                dimension = "ratio"
        elif re.search(r"(?:₹|\bRs\.?)\s*$", before, re.I):
            dimension = "rupees"
        elif table_currency:
            dimension = "rupees"
            factor = Decimal(10_000_000 if table_currency == "crore" else 100_000)
        if dimension == "scalar" and _YEAR.fullmatch(token):
            continue
        value = Decimal(token.replace(",", ""))
        if not token.startswith(("-", "+")):
            if _DOWN.search(before):
                value = -value
            elif _UP.search(before):
                value = abs(value)
        result.append(_Value(token, value, factor, dimension,
                             len(token.rsplit(".", 1)[1]) if "." in token else 0,
                             bool(_APPROX.search(before))))
    return result


def number_errors(text: str, evidence_text: str) -> list[str]:
    """Find unsupported numeric values without computing financial quantities."""
    evidence = _numbers(evidence_text)
    errors = []
    for claim in _numbers(text):
        candidates = [value for value in evidence if value.dimension == claim.dimension]
        supported = any(value.value == claim.value for value in candidates)
        if not supported and claim.approximate:
            quantum = Decimal(1).scaleb(-claim.precision)
            supported = any((value.value / claim.factor).quantize(quantum, rounding=ROUND_HALF_UP) == claim.raw
                            for value in candidates)
        if not supported:
            errors.append(f"Number {claim.text} ({claim.dimension}) is not supported by the cited quotes")
    return errors


def validate_claim(claim: dict, documents: list[Document], evidence_ids: set[str], section: str) -> list[str]:
    """Check citations and prose using only selected quotes, not wider windows."""
    errors = []
    text = claim.get("text", "")
    if not isinstance(text, str) or not text.strip():
        return ["Claim text must be a nonempty string"]
    if claim.get("kind") not in _KINDS:
        errors.append("Claim kind is invalid")
    if section == "snapshot" and claim.get("kind") != "reported_fact":
        errors.append("Snapshot claims must be reported_fact")
    if _ADVICE.search(text):
        errors.append("Claim contains an advice word")
    cites = claim.get("cites")
    if not isinstance(cites, list) or not cites:
        return errors + ["Claim must cite at least one unit"]
    owners = {uid: doc for doc in documents for uid in doc.units}
    quotes = []
    stale_seen = set()
    for uid in cites:
        if not isinstance(uid, str) or uid not in owners:
            errors.append(f"Unknown cited unit: {uid}")
            continue
        doc = owners[uid]
        if doc.sid not in evidence_ids:
            errors.append(f"Cited unit {uid} belongs to an excluded document")
            continue
        quotes.append(doc.quotes[uid])
        if doc.age_days > 365 and doc.sid not in stale_seen:
            stale_seen.add(doc.sid)
            year, month = doc.published.split("-")[:2]
            month_names = (calendar.month_name[int(month)], calendar.month_abbr[int(month)])
            if not any(re.search(r"\b" + re.escape(term) + r"\b", text, re.I) for term in (year, *month_names)):
                errors.append(f"Claim citing stale source {doc.sid} must include its month or year")
    errors.extend(number_errors(text, "\n".join(quotes)))
    return errors


def word_count(text: str) -> int:
    """Count the rendered body before the Sources heading."""
    body = re.split(r"(?im)^#{1,6}\s+Sources\s*$", text, maxsplit=1)[0]
    return len(body.split())


def wilson(k: int, n: int) -> dict:
    """Return observed counts and a Wilson 95% interval as proportions."""
    if n < 0 or k < 0 or k > n:
        raise ValueError("Expected 0 <= k <= n")
    if not n:
        return {"k": k, "n": n, "low": None, "high": None}
    z = 1.959963984540054
    p = k / n
    divisor = 1 + z * z / n
    center = (p + z * z / (2 * n)) / divisor
    radius = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / divisor
    return {"k": k, "n": n, "low": max(0.0, center - radius), "high": min(1.0, center + radius)}
