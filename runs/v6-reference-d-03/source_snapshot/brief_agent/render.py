"""Render accepted claims and explicit unresolved checks as Markdown."""

import re

from .writer import SECTIONS

TITLES = dict(snapshot='Snapshot', bull='Bull case', bear='Bear case', open_questions='Open questions')

# Educational definitions are separate from source-dependent company claims.
TERMS = (
    (r'\bEBITDA margin\b', 'EBITDA margin', 'earnings before interest, tax, depreciation and amortisation (spreading asset costs over time), as a share of revenue'),
    (r'\b(?:basis points?|bps)\b', 'Basis points', '100 basis points equal one percentage point'),
    (r'\breceivable days\b', 'Receivable days', 'how long, on average, customers take to pay'),
    (r'\b(?:pledged|pledging|pledge)\b', 'Pledged shares', 'shares offered as security for a loan'),
    (r'\border book\b', 'Order book', 'orders received but not yet completed'),
    (r'\bprice[- ]variation clauses?\b', 'Price-variation clause', 'a contract term allowing prices to change with specified costs'),
    (r'\bcrores?\b', 'Crore', '10 million'),
)


def render(data, documents, routes, ticker, as_of, notices=(), unavailable=False):
    lines = [f'# {ticker} research brief', '']
    if unavailable:
        lines += ['**Verification was unavailable for part of this run. The unresolved items below are not verified findings.**', '']
    for section in SECTIONS:
        lines += [f'## {TITLES[section]}', '']
        for claim in data[section]:
            sources = sorted({uid.split('.')[0] for uid in claim['cites']}, key=lambda s: int(s[1:]))
            lines += [f"- {claim['text']} " + ' '.join(f'[{sid}]' for sid in sources)]
        if section == 'open_questions':
            lines += [f'- {notice}' for notice in dict.fromkeys(notices)]
        if not data[section] and not (section == 'open_questions' and notices):
            lines.append('No verified item available.')
        lines.append('')
    lines += ['## Sources', '']
    for doc in documents:
        route = routes[doc.sid]
        suffix = '' if route['status'] == 'evidence' else f" Excluded: {route['reason']}."
        lines.append(f'- [{doc.sid}] {doc.source}. {doc.source_type}. Published {doc.published}. {doc.url}.{suffix}')
    claim_text = ' '.join(claim['text'] for section in SECTIONS for claim in data[section])
    definitions = [f'{name}: {meaning}.' for pattern, name, meaning in TERMS
                   if re.search(pattern, claim_text, re.I)]
    if definitions:
        lines += ['', '**Terms used (general definitions):** ' + ' '.join(definitions)]
    removed = sum(bool(doc.removals) for doc in documents)
    if removed:
        lines += ['', f'Data handling: hidden content was removed from {removed} source document(s) before writing and verification. Removal details are recorded in checks.json.']
    if any(r['status'] == 'excluded' and 'AI-directed instructions' in r['reason'] for r in routes.values()):
        lines += ['', 'Data handling: a source containing instructions to AI tools was excluded from the evidence.']
    lines += ['', f'As of {as_of}. Not investment advice.', '']
    return '\n'.join(lines)
