"""Writer output contract and source-labelled input."""

SECTIONS = ('snapshot', 'bull', 'bear', 'open_questions')
KINDS = ('reported_fact', 'management_view_or_forecast', 'third_party_view')
CLAIM = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'text': {'type': 'string'},
        'kind': {'type': 'string', 'enum': list(KINDS)},
        'cites': {'type': 'array', 'items': {'type': 'string'}},
    },
    'required': ['text', 'kind', 'cites'],
}
SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {s: {'type': 'array', 'items': CLAIM} for s in SECTIONS},
    'required': list(SECTIONS),
}


def payload(documents, profile, as_of, routes):
    return {
        'target_company': profile, 'as_of': as_of,
        'evidence_documents': [
            {'id': d.sid, 'source': d.source, 'url': d.url, 'type': d.source_type,
             'tier': d.tier, 'published': d.published, 'age_days': d.age_days,
             'title': d.title, 'units': d.quotes}
            for d in documents if routes[d.sid]['status'] == 'evidence'
        ],
        'excluded_documents': [
            {'name': d.filename, 'reason': routes[d.sid]['reason']}
            for d in documents if routes[d.sid]['status'] != 'evidence'
        ],
    }


def shape_errors(data):
    if not isinstance(data, dict) or set(data) != set(SECTIONS):
        return ['Output must contain exactly the four section arrays.']
    errors = []
    for section, claims in data.items():
        if not isinstance(claims, list):
            errors.append(f'{section}: expected an array')
            continue
        for index, c in enumerate(claims):
            if (not isinstance(c, dict) or set(c) != {'text', 'kind', 'cites'}
                    or not isinstance(c['text'], str) or not c['text'].strip()
                    or c['kind'] not in KINDS or not isinstance(c['cites'], list)
                    or not all(isinstance(uid, str) for uid in c['cites'])):
                errors.append(f'{section}[{index}]: invalid claim shape')
    return errors
