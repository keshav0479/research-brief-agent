"""One draft, at most one repair, then retain only claims that pass their checks."""

import copy
import json

from .checks import validate_claim, word_count, wilson
from .clients import ProviderError
from .load import target_profile
from .render import render
from .triage import triage
from .verify import verify_claim
from .writer import SCHEMA, SECTIONS, payload, shape_errors


def run(documents, ticker, as_of, arm, writer, decider, system, questions, naive_system,
        *, audit=None, artifacts=None, on_progress=None):
    # The CLI owns these containers so partial evidence survives a provider failure.
    audit = {} if audit is None else audit
    artifacts = {} if artifacts is None else artifacts
    progress = on_progress or (lambda message: None)
    progress(f'Loaded {len(documents)} source document(s).')
    profile = target_profile(documents, ticker)
    audit.update({'arm': arm, 'profile': profile, 'passes': [], 'dropped': [],
             'removals': {d.sid: d.removals for d in documents if d.removals},
             'warnings': [f'{d.sid}: {w}' for d in documents for w in d.warnings],
             'manual_supported_review': 'pending', 'manual_trap_review': 'pending'})
    if arm == 'A':
        progress('Generating the raw baseline draft.')
        draft = writer.generate(naive_system, {
            'ticker': ticker, 'as_of': as_of,
            'documents': [{'source': d.source, 'url': d.url, 'published': d.published,
                           'type': d.source_type, 'text': d.raw_body} for d in documents],
        })
        artifacts['draft'] = draft
        audit.update(status='completed_unchecked', word_count=word_count(draft['markdown']))
        progress('Raw baseline ready to write; claim checks were not run.')
        return draft['markdown'], audit, artifacts

    progress('Triaging source documents.')
    routes = triage(documents, profile, questions, decider)
    audit['routes'] = routes
    evidence_ids = {sid for sid, route in routes.items() if route['status'] == 'evidence'}
    unavailable = any(route['diagnostics'].get('verifier_unavailable') for route in routes.values())
    notices = [f"Could not confirm whether source {sid} concerns this company: {route['reason']}."
               for sid, route in routes.items() if route['status'] == 'unclear']
    inputs = payload(documents, profile, as_of, routes)
    accepted = {s: [] for s in SECTIONS}
    cache = {}

    def check(data):
        errors = shape_errors(data)
        if errors:
            progress('Draft shape is invalid; claim checks skipped.')
            return [], [{'claim': None, 'errors': errors}], {s: [] for s in SECTIONS}
        progress(f'Checking {sum(len(data[section]) for section in SECTIONS)} claim(s).')
        results, failures = [], []
        kept = {s: [] for s in SECTIONS}
        for section in SECTIONS:
            for index, original in enumerate(data[section]):
                claim = copy.deepcopy(original)
                errors = validate_claim(claim, documents, evidence_ids, section)
                if '\u2014' in claim['text']:
                    errors.append('Use a comma or period instead of an em dash')
                result = {'section': section, 'index': index, 'claim': original,
                          'code_errors': list(errors), 'verification': None}
                if not errors and decider is not None:
                    # Unchanged claims reuse this run's decision evidence during repair.
                    key = json.dumps([section, claim], sort_keys=True)
                    if key not in cache:
                        cache[key] = verify_claim(claim, documents, evidence_ids, section, questions, decider)
                    checked = cache[key]
                    result['verification'] = checked
                    claim['kind'] = checked['kind']
                    errors.extend(checked['errors'])
                    errors.extend(validate_claim(claim, documents, evidence_ids, section))
                result.update(errors=list(dict.fromkeys(errors)), accepted=not errors, final_claim=claim)
                results.append(result)
                if errors:
                    failures.append(result)
                else:
                    kept[section].append(claim)
        return results, failures, kept

    if evidence_ids:
        progress(f'Generating a draft from {len(evidence_ids)} admitted source document(s).')
        draft = writer.generate(system, inputs, SCHEMA)
        artifacts['draft'] = draft
        results, failures, accepted = check(draft)
        audit['passes'].append({'stage': 'draft', 'results': results, 'failures': failures})
        progress(f'Draft checks found {len(failures)} failure(s).')
        if failures:
            progress('Requesting the single repair attempt.')
            try:
                repaired = writer.generate(system, {**inputs, 'previous_output': draft,
                    'repair_failures': [{'section': f.get('section'), 'claim': f.get('claim'),
                                         'errors': f['errors']} for f in failures],
                    'task': 'Repair the listed failures. Return the complete brief JSON. This is the only repair attempt.'}, SCHEMA)
                artifacts['repair'] = repaired
                results, failures, accepted = check(repaired)
                audit['passes'].append({'stage': 'repair', 'results': results, 'failures': failures})
                progress(f'Repair checks found {len(failures)} failure(s).')
            except ProviderError as error:
                audit['repair_error'] = str(error)
                progress('Repair unavailable; retaining only previously accepted claims.')
        audit['dropped'] = failures
        for result in (result for stage in audit['passes'] for result in stage['results']):
            verification = result.get('verification') or {}
            unavailable |= verification.get('unavailable', False)
            if verification.get('unavailable'):
                sources = sorted({uid.split('.')[0] for uid in result['claim']['cites']})
                notices.append('Verification was unavailable for a proposed statement from ' +
                               ', '.join(f'[{sid}]' for sid in sources) +
                               '; see checks.json before using that statement.')
            for conflict in verification.get('conflicts', []):
                # The conflict is an audit finding, not a newly generated factual claim.
                sources = sorted(set(conflict['cited_sources'] + [conflict['higher_tier_source']]))
                notices.append('Sources disagree on a proposed statement; reconcile ' +
                               ', '.join(f'[{sid}]' for sid in sources) +
                               ' before relying on it. See checks.json for the exact statement.')
    else:
        notices.append('No documents were admitted as evidence; the company brief could not be verified.')

    audit['unavailable'] = unavailable
    audit['accepted'] = accepted
    final_results = audit['passes'][-1]['results'] if audit['passes'] else []
    n = len(final_results)
    audit['metrics'] = {
        'cited_ids_valid': wilson(sum(not any('cited unit' in e.lower() or 'cite at least' in e.lower()
                                            for e in r['code_errors']) for r in final_results), n),
        'numbers_matched': wilson(sum(not any(e.startswith('Number ') for e in r['code_errors'])
                                     for r in final_results), n),
        'accepted_claims': sum(map(len, accepted.values())),
        'dropped_claims': len(audit['dropped']), 'system_open_questions': len(set(notices)),
    }
    missing_sections = [section for section in SECTIONS if not accepted[section]]
    audit['warnings'].extend(f'No accepted claims in required section: {section}'
                             for section in missing_sections)
    audit['status'] = ('verification_unavailable' if unavailable else
                       'incomplete' if missing_sections else
                       'completed_with_drops' if audit['dropped'] else 'completed')
    progress(f"Accepted {audit['metrics']['accepted_claims']} claim(s); rendering brief.")
    brief = render(accepted, documents, routes, ticker, as_of, notices, unavailable)
    audit['word_count'] = word_count(brief)
    if audit['word_count'] > 600:
        audit['warnings'].append('Body exceeds 600 words')
    if len(accepted['snapshot']) not in (3, 4):
        audit['warnings'].append('Snapshot does not contain three or four items')
    return brief, audit, artifacts
