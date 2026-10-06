"""Deterministic checks, not a substitute for source entailment review.

Numbers are checked for value and unit in the cited quotes. This cannot bind a
number to its financial metric, denominator, company or reporting period.
Only explicit approximation words permit rounding, using ROUND_HALF_UP.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import math
import re
from typing import Protocol
import unicodedata


class Document(Protocol):
    sid: str
    age_days: int
    published: str
    title: str
    units: dict[str, str]
    quotes: dict[str, str]


_NUMBER = re.compile(r"(?<![\w.])[-+]?(?:\d(?:[\d,]*\d)?(?:\.\d+)?|\.\d+)(?!\d|\.\d)")
_PERIOD = re.compile(r"\b(?:Q\s*[1-4]|H\s*[12]|FY\s*\d{2,4})\b", re.I)
_YEAR = re.compile(r"\b(?:19|20|21)\d{2}\b")
_APPROX = re.compile(r"(?:\b(?:about|approximately|approx\.?|around|roughly)\s*|~\s*)(?:₹\s*|Rs\.?\s*)?$", re.I)
_DOWN = re.compile(r"\b(?:(?:down|fell|fallen|declined|decreased|contracted|dropped|reduced)(?:\s+by)?|(?:decrease|decline|drop|fall|contraction|reduction)\s+of)\s*$", re.I)
_UP = re.compile(r"\b(?:(?:up|rose|risen|increased|grew|expanded)(?:\s+by)?|(?:increase|rise|growth)\s+of)\s*$", re.I)
# A small phrase screen, not a complete investment-advice classifier.
_ADVICE = re.compile(
    r"\b(?:upside|multibagger|price\s+targets?|target\s+prices?)\b"
    r"|\bstrong\s+(?:buy|sell)\b"
    r"|\b(?:stock|shares|equity)\s+(?:is|are)\s+(?:a\s+)?(?:buy|sell|hold)\b"
    r"|\b(?:buy|sell|hold)[\"'”’]?\s+(?:rating|recommendation|call)\b"
    r"|\b(?:rating|recommendation|call)\s*(?:(?:is|of)\s+|:\s*)?[\"'“‘]?(?:strong\s+)?(?:buy|sell|hold)\b"
    r"|\b(?:recommend(?:s|ed)?|advis(?:e|es|ed)|rated)\s+(?:(?:a|to|as|strong|it|investors|shareholders|you)\s+)*[\"'“‘]?(?:buy|sell|hold|buying|selling|holding)\b"
    r"(?=\s*(?:[.!?;,\"'”’]|$|(?:rating|call|recommendation)\b|(?:(?:this|the|these|those)\s+)?(?:stock|shares|equity)\b))"
    r"|\b(?:you|investors|shareholders)\s+(?:should|must|can|could)\s+(?:buy|sell|hold)\b"
    r"|(?:^|[.!?;:]\s*)(?:please\s+)?(?:buy|sell|hold)\s+(?:(?:the|this|these|those)\s+)?(?:(?:[a-z][\w.-]{0,29}\s+)?(?:stock|shares|equity)|now|today)\b",
    re.I,
)
_KINDS = {"reported_fact", "management_view_or_forecast", "third_party_view"}
_TABLE_MONEY = re.compile(r"(?:₹|\bRs\.?)\s*(crore|lakh)\b", re.I)
_MONTHS = {name.casefold(): number for names in (calendar.month_name, calendar.month_abbr)
           for number, name in enumerate(names) if name}
_MONTHS['sept'] = 9
_MONTH_PATTERN = '|'.join(sorted(_MONTHS, key=len, reverse=True))
_DATE = re.compile(
    rf"\b(?:\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}\s+(?:{_MONTH_PATTERN})\.?\s+\d{{4}}"
    rf"|(?:{_MONTH_PATTERN})\.?\s+\d{{1,2}},?\s+\d{{4}})\b", re.I)


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


def _extract_dates(text: str) -> tuple[str, list[tuple[str, date | None]]]:
    """Mask full dates so their components cannot become numeric evidence."""
    found = []

    def mask(match):
        raw = match.group()
        try:
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
                value = date.fromisoformat(raw)
            else:
                first, second, year = raw.replace(',', '').replace('.', '').split()
                day, month = (first, second) if first.isdigit() else (second, first)
                value = date(int(year), _MONTHS[month.casefold()], int(day))
        except ValueError:
            value = None
        found.append((raw, value))
        return ' ' * len(raw)

    return _DATE.sub(mask, _normalise(text)), found


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


def number_errors(text: str, evidence_text: str, *, date_context: str = '') -> list[str]:
    """Check values and full dates; metadata contributes dates only."""
    text, claim_dates = _extract_dates(text)
    evidence_text, evidence_dates = _extract_dates(evidence_text)
    _, metadata_dates = _extract_dates(date_context)
    supported_dates = {value for _, value in evidence_dates + metadata_dates if value is not None}
    evidence = _numbers(evidence_text)
    errors = [f"Date {raw} is not found in the cited source"
              for raw, value in claim_dates if value is None or value not in supported_dates]
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
    if any(unicodedata.category(char) == 'Cc' and char not in '\t\n\r' for char in text):
        errors.append('Claim contains a nonprinting control character; use plain words or a valid currency symbol')
    if claim.get("kind") not in _KINDS:
        errors.append("Claim kind is invalid")
    if section == "snapshot" and claim.get("kind") != "reported_fact":
        errors.append("Snapshot claims must be reported_fact")
    if _ADVICE.search(text):
        errors.append("Claim contains an investment-advice phrase")
    cites = claim.get("cites")
    if not isinstance(cites, list) or not cites:
        return errors + ["Claim must cite at least one unit"]
    owners = {uid: doc for doc in documents for uid in doc.units}
    quotes = []
    date_context = []
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
        date_context.extend((doc.title, doc.published))
        if doc.age_days > 365 and doc.sid not in stale_seen:
            stale_seen.add(doc.sid)
            year, month = doc.published.split("-")[:2]
            month_names = (calendar.month_name[int(month)], calendar.month_abbr[int(month)])
            if not any(re.search(r"\b" + re.escape(term) + r"\b", text, re.I) for term in (year, *month_names)):
                errors.append(f"Claim citing stale source {doc.sid} must include its month or year")
    errors.extend(number_errors(text, "\n".join(quotes), date_context="\n".join(date_context)))
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
