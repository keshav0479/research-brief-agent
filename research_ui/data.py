"""Present saved runs without editing reports or rerunning model decisions.

Source passages are reconstructed only when the document bytes and the source
parser match the run's saved hashes. Raw provider responses are never exposed.
"""

from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import unicodedata

from brief_agent.load import load_documents
from brief_agent.render import TERMS, TITLES
from brief_agent.writer import SECTIONS

from .coverage import build_coverage

RUN_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}')
MAX_ARTIFACT_BYTES = 10_000_000


def run_folder(root: Path, run_id: str) -> Path:
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        raise ValueError('Invalid run identifier.')
    parent = root / 'runs'
    folder = parent / run_id
    if parent.is_symlink() or folder.is_symlink() or not folder.is_dir():
        raise FileNotFoundError('Saved run not found.')
    return folder


def read_artifact(folder: Path, name: str, *, required=False):
    """Read a known artifact basename, never a client-supplied file path."""
    if name not in {'config.json', 'checks.json', 'timings-usage.json', 'brief.md'}:
        raise ValueError('Unknown artifact.')
    path = folder / name
    if path.is_symlink():
        raise ValueError('Linked artifacts cannot be opened in the workspace.')
    if not path.is_file():
        if required:
            raise FileNotFoundError('Saved artifact not found.')
        return '' if name.endswith('.md') else {}
    if path.stat().st_size > MAX_ARTIFACT_BYTES:
        raise ValueError('Saved artifact is too large to display.')
    text = path.read_text(encoding='utf-8')
    if name.endswith('.md'):
        return text
    result = json.loads(text)
    if not isinstance(result, dict):
        raise ValueError('Saved artifact is not a JSON object.')
    return result


def _summary(folder, config, checks, timings):
    accepted = checks.get('accepted') or {}
    return {
        'id': folder.name,
        'ticker': config.get('ticker', ''),
        'company': (checks.get('profile') or {}).get('name') or config.get('ticker', ''),
        'as_of': config.get('as_of', ''),
        'status': checks.get('status', 'incomplete'),
        'word_count': checks.get('word_count'),
        'claims_count': sum(len(items) for items in accepted.values() if isinstance(items, list)),
        'elapsed_s': timings.get('elapsed_s'),
        'created_at': datetime.fromtimestamp((folder / 'config.json').stat().st_mtime, timezone.utc).isoformat(),
        'arm': config.get('arm'),
        'writer_model': config.get('writer_requested_model'),
        'verifier_model': config.get('jev_requested_model') or (config.get('writer_requested_model') if config.get('arm') == 'C' else None),
    }


def list_runs(root: Path) -> list[dict]:
    parent = root / 'runs'
    if not parent.is_dir() or parent.is_symlink():
        return []
    runs = []
    for folder in parent.iterdir():
        if not RUN_ID.fullmatch(folder.name) or not folder.is_dir() or folder.is_symlink():
            continue
        try:
            config = read_artifact(folder, 'config.json', required=True)
            checks = read_artifact(folder, 'checks.json')
            timings = read_artifact(folder, 'timings-usage.json')
            runs.append(_summary(folder, config, checks, timings))
        except (ValueError, OSError, UnicodeError):
            # Partially written or damaged runs do not hide healthy history.
            continue
    return sorted(runs, key=lambda item: (item['created_at'], item['id']), reverse=True)


def _documents(root, folder, config):
    hashes = config.get('pack_hashes') or {}
    parser_hash = (config.get('source_manifest') or {}).get('brief_agent/load.py')
    parser = root / 'brief_agent/load.py'
    if not parser_hash or not parser.is_file() or hashlib.sha256(parser.read_bytes()).hexdigest() != parser_hash:
        return [], 'The source parser differs from this saved run. Original quotes are unavailable; the saved report and check records remain readable.'
    if not hashes or any(not isinstance(name, str) or Path(name).name != name or '\\' in name or not name.endswith('.md') for name in hashes):
        return [], 'This run has no usable document manifest. Source passages cannot be reconstructed.'
    candidates = [root / '.web-workspace/inputs' / folder.name, root / 'research_pack']
    for candidate in candidates:
        if any(p.is_symlink() for p in [candidate, *candidate.parents][:4]) or not candidate.is_dir():
            continue
        paths = list(candidate.glob('*.md'))
        if {p.name for p in paths} != set(hashes) or any(p.is_symlink() for p in paths):
            continue
        if any(p.stat().st_size > MAX_ARTIFACT_BYTES or hashlib.sha256(p.read_bytes()).hexdigest() != hashes[p.name] for p in paths):
            continue
        try:
            return load_documents(candidate, date.fromisoformat(config['as_of'])), 'Source bytes and passage parser match the saved run.'
        except (ValueError, OSError, KeyError):
            continue
    return [], 'Original documents are missing or have changed since this run. Quotes are withheld rather than attached to different source text.'


def _claim_key(section, claim):
    return section, json.dumps(claim, ensure_ascii=False, sort_keys=True)


def _system_notices(markdown, accepted):
    """Preserve renderer-added open questions separately from generated claims."""
    if '## Open questions\n' not in markdown:
        return []
    body = markdown.split('## Open questions\n', 1)[1].split('\n## ', 1)[0]
    generated = set()
    for claim in accepted.get('open_questions', []):
        source_ids = sorted({uid.split('.')[0] for uid in claim.get('cites', [])},
                            key=lambda sid: int(sid[1:]) if sid[1:].isdigit() else 0)
        generated.add(claim['text'] + ' ' + ' '.join(f'[{sid}]' for sid in source_ids))
    return [line[2:] for line in body.splitlines() if line.startswith('- ') and line[2:] not in generated]


def _transport_errors(folder):
    """Expose only recorded transport metadata, excluding requests and responses."""
    errors = []
    allowed = ('attempt', 'error_type', 'http_status', 'provider_error_code',
               'retry_reason', 'retry_delay_s', 'provider_requested_wait_s', 'elapsed_s')
    for provider, name in [('writer', 'writer-responses.json'), ('verifier', 'jev-responses.json')]:
        path = folder / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_ARTIFACT_BYTES:
            continue
        try:
            calls = json.loads(path.read_text(encoding='utf-8'))
        except (ValueError, OSError, UnicodeError):
            continue
        if not isinstance(calls, list):
            continue
        for call in calls:
            if isinstance(call, dict) and call.get('status') == 'error':
                errors.append({'provider': provider, **{key: call[key] for key in allowed if key in call}})
    return errors


def load_run(root: Path, run_id: str) -> dict:
    folder = run_folder(root, run_id)
    config = read_artifact(folder, 'config.json', required=True)
    checks = read_artifact(folder, 'checks.json')
    timings = read_artifact(folder, 'timings-usage.json')
    markdown = read_artifact(folder, 'brief.md')
    documents, evidence_note = _documents(root, folder, config)
    routes = checks.get('routes') or {}
    sources, units = [], {}
    for doc in documents:
        route = routes.get(doc.sid) or {}
        source_units = []
        for uid, text in doc.units.items():
            quote = doc.quotes[uid]
            context = doc.windows[uid]
            source_units.append({'id': uid, 'text': text, 'quote': quote, 'context': context})
            units[uid] = {'unit_id': uid, 'source_id': doc.sid, 'quote': quote, 'context': context}
        sources.append({
            'id': doc.sid, 'title': doc.title, 'name': doc.source, 'url': doc.url,
            'published': doc.published, 'tier': doc.tier, 'type': doc.source_type,
            'age_days': doc.age_days, 'status': route.get('status', 'not_checked'),
            'reason': route.get('reason', 'Source screening was not recorded for this run.'),
            'removals': doc.removals, 'warnings': doc.warnings, 'units': source_units,
        })
    records = {}
    stages = checks.get('passes') or []
    notices = _system_notices(markdown, checks.get('accepted') or {})
    for stage in stages:
        for result in stage.get('results', []):
            if result.get('accepted'):
                records[_claim_key(result.get('section'), result.get('final_claim') or result.get('claim'))] = result
    sections = []
    for section in SECTIONS:
        claims = []
        for index, claim in enumerate((checks.get('accepted') or {}).get(section, [])):
            result = records.get(_claim_key(section, claim))
            verification = (result or {}).get('verification')
            support = 'not_run'
            if verification:
                support = ('unavailable' if verification.get('unavailable') else
                           'failed' if verification.get('errors') else 'supported')
            confirmed = verification.get('kind_confirmed') if verification else None
            evidence = [units[uid] for uid in claim.get('cites', []) if uid in units]
            claims.append({
                'id': f'{section}-{index}', 'text': claim['text'], 'kind': claim.get('kind'),
                'kind_confirmed': confirmed, 'cites': claim.get('cites', []), 'evidence': evidence,
                'checks': {'code': ('failed' if result.get('code_errors') else 'passed') if result else 'not_run',
                           'support': support, 'kind_confirmed': confirmed},
            })
        sections.append({'key': section, 'title': TITLES[section], 'claims': claims,
                         'notices': notices if section == 'open_questions' else []})
    coverage = build_coverage(sources, sections)
    if not documents:
        coverage['notice'] = 'Coverage cannot be scanned because original source passages are unavailable. ' + coverage.get('notice', '')
    text = ' '.join(c['text'] for s in sections for c in s['claims'])
    presentation_warnings = []
    if any(unicodedata.category(char) == 'Cc' and char not in '\n\r\t' for char in markdown):
        presentation_warnings.append('This historical report contains unsupported control characters. Its saved text is preserved; use another report for presentation.')
    glossary = [{'term': name, 'definition': meaning} for pattern, name, meaning in TERMS if re.search(pattern, text, re.I)]
    conflicts = []
    for stage in stages:
        for result in stage.get('results', []):
            for conflict in (result.get('verification') or {}).get('conflicts', []):
                conflicts.append({'text': (result.get('claim') or {}).get('text', ''),
                                  'cited_sources': conflict.get('cited_sources', []),
                                  'higher_tier_source': conflict.get('higher_tier_source')})
    return {
        'run': _summary(folder, config, checks, timings), 'brief_markdown': markdown,
        'sections': sections, 'sources': sources, 'coverage': coverage, 'glossary': glossary,
        'evidence_available': bool(documents), 'evidence_note': evidence_note,
        'audit': {
            'error': checks.get('error'), 'error_type': checks.get('error_type'),
            'repair_error': checks.get('repair_error'), 'draft_error': checks.get('draft_error'),
            'transport_errors': _transport_errors(folder),
            'passes': [{'stage': p.get('stage'), 'accepted': sum(bool(r.get('accepted')) for r in p.get('results', [])),
                        'failed': len(p.get('failures', [])), 'repair_requested': len(p.get('repair_requests', []))} for p in stages],
            'dropped': [{'section': d.get('section'), 'text': (d.get('claim') or {}).get('text', ''),
                         'errors': d.get('errors', [])} for d in checks.get('dropped', [])],
            'warnings': [*checks.get('warnings', []), *presentation_warnings], 'removals': checks.get('removals', {}),
            'unavailable': checks.get('unavailable', False), 'structure_failures': checks.get('structure_failures', []),
            'conflicts': conflicts,
            'model_ids': {'writer_requested': config.get('writer_requested_model'),
                          'verifier_requested': config.get('jev_requested_model'),
                          'returned': config.get('returned_models', {})},
            'human_review': {'supported': checks.get('manual_supported_review', 'pending'),
                             'traps': checks.get('manual_trap_review', 'pending')},
            'writer_attempts': timings.get('writer_attempts'), 'verifier_attempts': timings.get('jev_attempts'),
            'elapsed_s': timings.get('elapsed_s'), 'billed_cost_usd': timings.get('billed_cost_usd'),
            'manual_edits_to_brief': config.get('manual_edits_to_brief'),
        },
    }
