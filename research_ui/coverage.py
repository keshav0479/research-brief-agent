"""Keyword-based topic review, never a completeness or factual-accuracy score."""

import re
import unicodedata


TOPICS = (
    ('revenue_level', 'Revenue level'),
    ('revenue_growth', 'Revenue growth'),
    ('profit_level', 'Profit level'),
    ('profit_growth', 'Profit growth'),
    ('margins', 'Margins'),
    ('cash_collection', 'Cash collection / working capital'),
    ('debt_funding', 'Debt / funding'),
    ('ownership_pledges', 'Ownership / pledged shares'),
    ('guidance_outlook', 'Guidance / outlook'),
    ('material_legal_risk', 'Material / legal risk'),
)
FLAGS = re.I
NUMBER = r'[-+\u2212]?\d[\d,]*(?:\.\d+)?'
PERCENT = re.compile(NUMBER + r'\s*(?:%|percent\b|per\s+cent\b)', FLAGS)
MONEY = re.compile(
    r'(?:\u20b9|\$|\b(?:Rs\.?|INR|USD|rupees?))\s*' + NUMBER
    + r'|' + NUMBER + r'\s*(?:crores?|lakhs?|millions?|billions?|thousands?)\b', FLAGS)
NUMERIC = re.compile(NUMBER)
GROWTH = re.compile(
    r'\b(?:grew|grown|growth|rose|risen|rise|rises|rising|increas\w*|up|fell|fallen|fall|falls|'
    r'decreas\w*|declin\w*|down|contract\w*|expand\w*|doubled|tripled|halved|yoy|year.on.year)\b', FLAGS)
MULTIPLE = re.compile(r'\b(?:doubled|tripled|halved)\b', FLAGS)
FUTURE = re.compile(
    r'\b(?:guidance|forecast\w*|expect\w*|plan(?:s|ned|ning)?|anticipat\w*|projected|'
    r'projection\w*|will|would|could|may|might|can|aim\w*|target\w*)\b|\bon\s+track\s+to\b', FLAGS)
METRIC = re.compile(
    r'\b(?:(?P<revenue>revenues?|(?:net\s+)?sales|turnover)|'
    r'(?P<profit>profits?(?:\s+after\s+tax)?|PAT|EBITDA|EBIT|earnings)|'
    r'(?P<other>margins?|exports?|receivables?|debt|borrowings?|capex|capacity|'
    r'order\s+book|promoters?|shareholding|ownership|cash\s+flow))\b', FLAGS)
LEVEL_CUE = re.compile(r'\b(?:was|were|is|at|to|reached|recorded|reported|totalled|totaled)\s*$', FLAGS)
BARE_LEVEL = re.compile(
    r'^\s*(?:(?:from\s+operations\s*)?(?:was|were|is|of|at|reached|recorded|totalled|totaled|'
    r'stood\s+at|amounted\s+to)\s*|[:=]\s*)?(' + NUMBER + r')', FLAGS)
SPLIT = re.compile(
    r'\n+|[.!?;]\s+|\s+(?:and|but|while)\s+(?=(?:management|the\s+company|we|it|expects?|plans?)\b)', FLAGS)
SIMPLE = {
    'margins': re.compile(r'\bmargins?\b', FLAGS),
    'cash_collection': re.compile(
        r'\b(?:receivables?|working\s+capital|cash\s+(?:collection|conversion|flow)|'
        r'collection\s+(?:days|period)|debtor\s+days|trade\s+debtors?|customer\s+payments?)\b', FLAGS),
    'debt_funding': re.compile(
        r'\b(?:debt|borrowings?|funding|financing|leverage|liquidity|credit\s+facilit\w*|'
        r'capex|capital\s+(?:expenditure|spending))\b', FLAGS),
    'ownership_pledges': re.compile(r'\b(?:promoters?|sharehold\w*|pledge\w*|ownership|stakes?)\b', FLAGS),
    'guidance_outlook': re.compile(
        r'\b(?:guidance|outlook|forecast\w*|expect\w*|plan(?:s|ned|ning)?|anticipat\w*|'
        r'projected|projections?|prospects?)\b|\b(?:think|believe)\w*\b.{0,70}\b(?:can|could|may|might)\b|'
        r'\b(?:will|would|could|may|might|can)\s+(?:grow|rise|double|increase|expand|improve)\b|\bon\s+track\s+to\b', FLAGS),
    'material_legal_risk': re.compile(
        r'\b(?:risks?|litigation|lawsuits?|regulator\w*|legal|tax\s+demand|demand\s+order|'
        r'penalt\w*|fines?|investigation\w*|defaults?|contingen\w*|material\s+impact)\b', FLAGS),
}


def _text(value):
    if not isinstance(value, str):
        return ''
    return unicodedata.normalize('NFKC', value).replace('**', '').replace('`', '')


def _amount_cell(cell):
    cell = cell.strip()
    return bool(MONEY.search(cell) or NUMERIC.fullmatch(cell)) and not PERCENT.search(cell)


def _table_topics(text):
    found, header = set(), []
    future = bool(FUTURE.search(text))
    for line in text.splitlines():
        if '|' not in line:
            continue
        cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
        if all(re.fullmatch(r':?-+:?', cell) for cell in cells):
            continue
        metric = METRIC.search(cells[0]) if cells else None
        if not metric or metric.lastgroup == 'other':
            header = cells
            continue
        key = metric.lastgroup
        if future or re.search(r'\b(?:margin|share|contribution|mix|volume|units|tonnage)\b', cells[0], FLAGS):
            continue
        amounts = any(_amount_cell(cell) for cell in cells[1:])
        growth_columns = [index for index, cell in enumerate(header)
                          if GROWTH.search(cell) or re.search(r'\bchange\b', cell, FLAGS)]
        growth = any(index < len(cells) and (NUMERIC.search(cells[index]) or PERCENT.search(cells[index]))
                     for index in growth_columns)
        growth |= bool(GROWTH.search(cells[0]) and any(PERCENT.search(cell) for cell in cells[1:]))
        growth |= amounts and any(re.match(r'^[+\-\u2212]', cell) and PERCENT.search(cell)
                                  for cell in cells[1:])
        if amounts and not GROWTH.search(cells[0]):
            found.add(key + '_level')
        if growth:
            found.add(key + '_growth')
    return found


def _level_mentioned(after):
    money = list(MONEY.finditer(after))
    if money:
        return not GROWTH.search(after) or any(
            LEVEL_CUE.search(after[:match.start()]) or re.fullmatch(r'\s*[:=]?\s*', after[:match.start()])
            for match in money)
    match = BARE_LEVEL.match(after)
    if not match:
        return False
    suffix = after[match.end():]
    if re.match(r'\s*(?:%|percent\b|per\s+cent\b|bps\b|basis\s+points?\b|days?\b|quarters?\b|years?\b)', suffix, FLAGS):
        return False
    number = float(match[1].replace(',', '').replace('\u2212', '-'))
    return not 1900 <= number <= 2099  # Bare years are not monetary levels.


def _topics(text):
    text = _text(text)
    found = {key for key, pattern in SIMPLE.items() if pattern.search(text)}
    found.update(_table_topics(text))
    for clause in SPLIT.split(text):
        if '|' in clause or FUTURE.search(clause):
            continue
        metrics = list(METRIC.finditer(clause))
        for index, metric in enumerate(metrics):
            key = metric.lastgroup
            if key == 'other':
                continue
            before = clause[:metric.start()]
            end = metrics[index + 1].start() if index + 1 < len(metrics) else len(clause)
            after = clause[metric.end():end]
            if (re.search(r'\bof\s+(?:total\s+)?$', before, FLAGS)
                    or re.match(r'\s*(?:share|contribution|mix|volume|units|tonnage)\b', after, FLAGS)):
                continue
            if _level_mentioned(after):
                found.add(key + '_level')
            if GROWTH.search(after) and (PERCENT.search(after) or MONEY.search(after) or MULTIPLE.search(after)):
                found.add(key + '_growth')
    return found


PERIOD_LABEL = re.compile(
    r'\b(?:(?:(?:Q\s*[1-4]|H\s*[12])\s*)?FY\s*\d{2,4}(?:\s*[-–]\s*\d{2,4})?'
    r'|Q\s*[1-4]|H\s*[12])\b', FLAGS)
FULL_DATE = re.compile(
    r'\b\d{1,2}\s+[A-Z][a-z]+\.?\s+\d{4}\b|\b[A-Z][a-z]+\.?\s+\d{1,2},?\s+\d{4}\b|\b\d{4}-\d{2}-\d{2}\b', FLAGS)


def _figures(text):
    """Normalize numeric tokens, ignoring period labels, dates and bare years."""
    text = FULL_DATE.sub(' ', PERIOD_LABEL.sub(' ', _text(text)))
    figures = set()
    for match in NUMERIC.finditer(text):
        value = match.group().replace(',', '').replace('−', '-').lstrip('+-')
        if re.fullmatch(r'(?:19|20)\d{2}', value):
            continue
        figures.add(value.rstrip('0').rstrip('.') if '.' in value else value)
    return figures


def build_coverage(sources, sections):
    """Compare topic cues, ignoring citations as evidence of a textual mention.

    The partial status flags unmatched numeric tokens in uncited passages for
    review. Tokens are compared across all accepted claims without aligning
    metrics, signs, units or scale; this does not establish missing facts.
    """
    source_hits = {key: [] for key, _ in TOPICS}
    claim_ids = {key: [] for key, _ in TOPICS}
    ignored = {'not_admitted': 0, 'not_primary': 0, 'future': 0, 'older': 0, 'unknown_age': 0}
    eligible_count = 0
    for source in sources:
        if source.get('status') != 'evidence':
            ignored['not_admitted'] += 1
            continue
        if source.get('tier') not in (1, 2):
            ignored['not_primary'] += 1
            continue
        age = source.get('age_days')
        if not isinstance(age, (int, float)) or isinstance(age, bool):
            ignored['unknown_age'] += 1
            continue
        if age < 0:
            ignored['future'] += 1
            continue
        if age > 365:
            ignored['older'] += 1
            continue
        eligible_count += 1
        for unit in source.get('units', []):
            quote = unit.get('quote') or unit.get('text') or ''
            for key in _topics(quote):
                hit = {'source_id': source['id'], 'unit_id': unit['id'], 'quote': quote}
                if not any(old['source_id'] == hit['source_id'] and old['unit_id'] == hit['unit_id']
                           for old in source_hits[key]):
                    source_hits[key].append(hit)
    cited_units, claim_figures = set(), set()
    for section in sections:
        for claim in section.get('claims', []):
            if claim.get('accepted') is False:
                continue
            cited_units.update(claim.get('cites') or [])
            claim_figures |= _figures(claim.get('text', ''))
            for key in _topics(claim.get('text', '')):
                if claim['id'] not in claim_ids[key]:
                    claim_ids[key].append(claim['id'])

    items = []
    for key, label in TOPICS:
        hits, mentions = source_hits[key], claim_ids[key]
        uncited_missing, all_missing = [], []
        for hit in hits:
            hit['cited'] = hit['unit_id'] in cited_units
            for figure in sorted(_figures(hit['quote']) - claim_figures, key=lambda v: float(v)):
                if figure not in all_missing:
                    all_missing.append(figure)
                if not hit['cited'] and figure not in uncited_missing:
                    uncited_missing.append(figure)
        # Mentioned topics flag only uncited passages; unmatched topics flag all passages.
        missing = uncited_missing if mentions else sorted(all_missing, key=lambda v: float(v))
        if hits and mentions and missing:
            status = 'partial'
            note = ('Figures to review in uncited passages: ' + ', '.join(missing)
                    + '. These numeric tokens did not match accepted claim text; compare their meaning and units.')
        elif hits and mentions:
            status = 'mentioned'
            note = 'Wording matched in source text and the brief. Review whether figures, periods and scope align.'
        elif hits:
            status = 'review_needed'
            note = 'Source wording matched, but no keyword match was found in accepted claim text. Review these passages; leaving them out may be justified.'
        else:
            status = 'not_found'
            note = 'No keyword match in eligible source text; this does not establish that the topic is absent.'
            if mentions:
                note += ' Brief wording did match, so inspect its evidence separately.'
        items.append({'id': key, 'label': label, 'status': status, 'missing_figures': missing,
                      'source_hits': hits, 'claim_ids': mentions, 'note': note})
    omissions = ', '.join(f'{count} {reason.replace("_", " ")}' for reason, count in ignored.items() if count)
    notice = (f'Checked {eligible_count} admitted primary source(s) aged 0 to 365 days. '
              'These keyword cues suggest review topics, not completeness, factual accuracy or materiality. '
              'Citations alone do not count as mentions. Paraphrases and mixed statements may be missed. '
              'Figure matching compares numeric tokens across all claims, without aligning metrics, signs, units or scale. '
              'Older unresolved issues can still matter and need separate review.')
    if omissions:
        notice += ' Sources outside this check: ' + omissions + '.'
    return {'method': 'Deterministic topic keywords with separate amount, growth and forecast cues.',
            'notice': notice, 'items': items}
