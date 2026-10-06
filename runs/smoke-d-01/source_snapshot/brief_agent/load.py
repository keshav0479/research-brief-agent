"""Load source documents and attach citeable units and local quote windows."""

from dataclasses import dataclass
from datetime import date
import hashlib
from pathlib import Path
import re
import unicodedata


@dataclass
class Document:
    sid: str
    filename: str
    source: str
    url: str
    published: str
    age_days: int
    tier: int
    source_type: str
    raw_body: str
    body: str
    title: str
    units: dict[str, str]
    windows: dict[str, str]
    quotes: dict[str, str]
    sha256: str
    removals: list[dict]
    warnings: list[str]


TIERS = {
    "official exchange filing": 1,
    "official company filing": 2,
    "official company transcript": 2,
    "news article": 3,
    "blog post": 4,
}
ABBREVIATIONS = ("Mr.", "Ms.", "Mrs.", "Dr.", "No.", "Ltd.", "Rs.")
FRONTMATTER = re.compile(r"\A\ufeff?---\s*\n(.*?)\n---[ \t]*(?:\n|$)", re.S)
BULLET = re.compile(r"^\s*(?:[-*+] |\d+[.)] )")
HEADING = re.compile(r"^(#{1,6})\s+(.+)$")


def clean_text(text: str) -> tuple[str, list[dict]]:
    """Remove hidden text before normalization, counting removed characters."""
    removals = []
    comments = list(re.finditer(r"<!--.*?(?:-->|$)", text, re.S))
    if comments:
        removals.append({"kind": "html_comment", "count": sum(len(m[0]) for m in comments)})
        text = re.sub(r"<!--.*?(?:-->|$)", "", text, flags=re.S)
    counts = {"unicode_format": 0, "unicode_private_use": 0, "unicode_blank": 0}
    kept = []
    for char in text:
        category = unicodedata.category(char)
        kind = {"Cf": "unicode_format", "Co": "unicode_private_use"}.get(category)
        if char in ("\u3164", "\u2800"):
            kind = "unicode_blank"
        if kind:
            counts[kind] += 1
        else:
            kept.append(char)
    removals.extend({"kind": kind, "count": count} for kind, count in counts.items() if count)
    return unicodedata.normalize("NFKC", "".join(kept)), removals


def _frontmatter(text: str, filename: str) -> tuple[dict[str, str], str]:
    match = FRONTMATTER.match(text)
    if not match:
        raise ValueError(f"{filename}: missing or unterminated front matter")
    fields = {}
    for line in match[1].splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition(":")
        if not separator or not key.strip():
            raise ValueError(f"{filename}: invalid front matter entry")
        key, value = key.strip(), value.strip()
        if key in fields:
            raise ValueError(f"{filename}: duplicate front matter field {key}")
        if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        fields[key] = value
    missing = [key for key in ("source", "url", "published", "type") if not fields.get(key)]
    if missing:
        raise ValueError(f"{filename}: missing front matter fields: {', '.join(missing)}")
    try:
        date.fromisoformat(fields["published"])
    except ValueError as exc:
        raise ValueError(f"{filename}: published must be an ISO date") from exc
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", fields["published"]):
        raise ValueError(f"{filename}: published must be an ISO date")
    return fields, text[match.end():].strip()


def sentences(paragraph: str) -> list[str]:
    parts = re.split(r'(?:(?<=[.!?])|(?<=[.!?]["”*]))\s+(?=[A-Z0-9"“*])', paragraph.replace("\n", " "))
    result = []
    for part in parts:
        if result and result[-1].rstrip('"”*').endswith(ABBREVIATIONS):
            result[-1] += " " + part
        else:
            result.append(part)
    return result


def _separator(line: str) -> bool:
    cells = line.strip().strip("|").split("|")
    return bool(cells) and all(re.fullmatch(r"\s*:?-{3,}:?\s*", cell) for cell in cells)


def _units(body: str, sid: str) -> tuple[str, dict, dict, dict]:
    lines = body.splitlines()
    title, heading = "", ""
    # (text, heading, quote, is_table). Metadata stays beside each unit.
    entries = []
    index = 0
    caption = ""
    while index < len(lines):
        line = lines[index].strip()
        if not line:
            index += 1
            continue
        match = HEADING.match(line)
        if match:
            if len(match[1]) == 1 and not title:
                title = match[2]
            else:
                heading = match[2]
            index += 1
            continue
        following = index + 1
        while following < len(lines) and not lines[following].strip():
            following += 1
        if line.startswith("**") and line.endswith("**") and following < len(lines) and lines[following].lstrip().startswith("|"):
            caption = line
            index = following
            continue
        if line.startswith("|"):
            table = []
            while index < len(lines) and lines[index].lstrip().startswith("|"):
                table.append(lines[index].strip())
                index += 1
            has_header = len(table) > 1 and _separator(table[1])
            header = table[:2] if has_header else []
            for row in table[2:] if has_header else table:
                if not _separator(row):
                    quote = "\n".join(part for part in [caption, *header, row] if part)
                    entries.append((row, heading, quote, True))
            caption = ""
            continue
        if BULLET.match(line):
            entries.append((line, heading, line, False))
            index += 1
            continue
        paragraph = [line]
        index += 1
        while index < len(lines):
            next_line = lines[index].strip()
            if not next_line or HEADING.match(next_line) or BULLET.match(next_line) or next_line.startswith("|"):
                break
            paragraph.append(next_line)
            index += 1
        for sentence in sentences(" ".join(paragraph)):
            entries.append((sentence, heading, sentence, False))
    units, windows, quotes = {}, {}, {}
    for index, (text, section, quote, table) in enumerate(entries):
        uid = f"{sid}.u{index + 1:02d}"
        units[uid], quotes[uid] = text, quote
        if table:
            context = [quote]
        else:
            context = []
            last_section = None
            for _, neighbor_section, neighbor_quote, _ in entries[max(0, index - 1):index + 2]:
                if neighbor_section and neighbor_section != last_section:
                    context.append(neighbor_section)
                context.append(neighbor_quote)
                last_section = neighbor_section
        prefix = [title] if title else []
        if table and section:
            prefix.append(section)
        windows[uid] = "\n\n".join(prefix + context)
    return title, units, windows, quotes


def load_documents(path: Path, as_of: date) -> list[Document]:
    """Read only Markdown files directly inside the supplied document directory."""
    if not path.is_dir():
        raise ValueError("Document directory does not exist")
    pending = []
    for file in sorted(path.glob("*.md")):
        content = file.read_bytes()
        original = content.decode("utf-8-sig").replace("\r\n", "\n")
        cleaned, removals = clean_text(original)
        metadata, body = _frontmatter(cleaned, file.name)
        raw_match = FRONTMATTER.match(original)
        raw_body = original[raw_match.end():].strip() if raw_match else original
        pending.append((metadata, body, raw_body, file.name, hashlib.sha256(content).hexdigest(), removals))
    if not pending:
        raise ValueError("Document directory contains no Markdown files")
    pending.sort(key=lambda item: (item[0]["published"], item[0]["url"], item[4]))
    documents = []
    for number, (metadata, body, raw_body, filename, digest, removals) in enumerate(pending, 1):
        sid = f"S{number}"
        source_type = metadata["type"].lower()
        warnings = []
        if source_type not in TIERS:
            warnings.append(f"Unknown source type '{source_type}'; defaulted to tier 3")
        age = (as_of - date.fromisoformat(metadata["published"])).days
        if age < 0:
            warnings.append("Publication date is after the requested as-of date")
        title, units, windows, quotes = _units(body, sid)
        documents.append(Document(sid, filename, metadata["source"], metadata["url"], metadata["published"], age,
                                  TIERS.get(source_type, 3), source_type, raw_body, body, title, units, windows,
                                  quotes, digest, removals, warnings))
    return documents


def target_profile(documents: list[Document], ticker: str) -> dict:
    profile = {"ticker": ticker, "name": "", "business": ""}
    marker = re.compile(r"\(NSE:\s*" + re.escape(ticker) + r"\s*\)", re.I)
    for document in sorted(documents, key=lambda doc: (doc.tier, doc.sid)):
        match = marker.search(document.body)
        if not match:
            continue
        before = document.body[:match.start()].rstrip()
        name = re.search(r"([A-Z][\w&'’.-]*(?:[ \t]+(?:[A-Z][\w&'’.-]*|of|and|the)){0,12})$", before)
        if name:
            profile["name"] = name[1]
        after = document.body[match.end():]
        business = re.match(r",\s*(an?\s+.+?)(?:,\s*(?:today|has|announced|reported)\b|\.\s|$)", after, re.S | re.I)
        if business:
            profile["business"] = business[1].strip().rstrip(".")
        return profile
    return profile
