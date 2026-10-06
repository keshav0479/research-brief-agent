"""Render accepted claims and explicit unresolved checks as Markdown."""

from .writer import SECTIONS

TITLES = dict(snapshot='Snapshot', bull='Bull case', bear='Bear case', open_questions='Open questions')


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
    removed = sum(bool(doc.removals) for doc in documents)
    if removed:
        lines += ['', f'Data handling: hidden content was removed from {removed} source document(s) before writing and verification. Removal details are recorded in checks.json.']
    if any(r['diagnostics'].get('clean_addresses_ai_tools', 0) >= 0.5 for r in routes.values()):
        lines += ['', 'Data handling: a source containing instructions to AI tools was excluded from the evidence.']
    lines += ['', f'As of {as_of}. Not investment advice.', '']
    return '\n'.join(lines)
