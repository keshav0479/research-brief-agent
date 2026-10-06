import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from brief_agent.clients import ProviderError
from brief_agent.pipeline import run


QUESTIONS = json.loads((Path(__file__).resolve().parents[1] / 'prompts/jev_questions.json').read_text())


def claim(text='Revenue was Rs 220 crore.', cite='S1.u01'):
    return {'text': text, 'kind': 'reported_fact', 'cites': [cite]}


def output(*claims):
    return {'snapshot': list(claims), 'bull': [], 'bear': [], 'open_questions': []}


def complete_output(*claims):
    data = output(*claims, claim('Example Ltd makes widgets.', 'S1.u02'),
                  claim('Example Ltd reported results.', 'S1.u03'))
    for section in ('bull', 'bear', 'open_questions'):
        data[section] = [claim()]
    return data


def document(sid='S1', tier=2, age=1):
    body = 'Example Ltd (NSE: DEMO), a maker of widgets, reported results. Revenue was Rs 220 crore.'
    units = {f'{sid}.u01': 'Revenue was Rs 220 crore.',
             f'{sid}.u02': 'Example Ltd makes widgets.', f'{sid}.u03': 'Example Ltd reported results.'}
    return SimpleNamespace(sid=sid, filename=f'{sid}.md', source='Example IR',
        source_type='official company filing' if tier == 2 else 'news article',
        url=f'https://example.test/{sid}', published='2026-09-22', age_days=age,
        tier=tier, title='Results', raw_body=body + '<!-- hidden source text -->', body=body,
        units=units, quotes=units, windows={uid: body for uid in units},
        removals=[{'kind': 'html_comment', 'count': 27}],
        warnings=[], sha256='a' * 64)


def format_error(code='json_validate_failed'):
    error = ProviderError('BadRequestError', 400)
    error.provider_code = code
    return error


class Writer:
    model = 'test-writer'
    _gemini = False

    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def generate(self, system, payload, schema=None):
        self.calls.append({'request': payload, 'model': self.model, 'usage': {}, 'raw_response': {}})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return copy.deepcopy(response)


class Decider:
    model = 'test-decider'
    url = 'https://example.test/systemone'

    def __init__(self, decisions=(), screen_error=False):
        self.decisions, self.calls = list(decisions), []
        self.screen_error = screen_error

    def decide(self, state, questions):
        self.calls.append({'request': {'state': state, 'questions': questions},
                           'model': self.model, 'usage': {}, 'raw_response': {}})
        if 'relation' not in questions:
            if self.screen_error:
                raise ProviderError('screen_unavailable')
            return {name: ({'type': 'score', 'score': 2, 'confidence': 1}
                           if question['type'] == 'score' else {'type': 'noul', 'noul': 0})
                    for name, question in questions.items()}
        response = self.decisions.pop(0) if self.decisions else 'supports'
        if isinstance(response, Exception):
            raise response
        return {name: {'type': 'choice', 'choice': response if name == 'relation' else 'reported_fact',
                       'confidence': 0.99} for name in questions}


class UnconfirmedKindDecider(Decider):
    def __init__(self, uncertain_text):
        super().__init__()
        self.uncertain_text = uncertain_text

    def decide(self, state, questions):
        if 'kind' not in questions:
            return super().decide(state, questions)
        self.calls.append({'request': {'state': state, 'questions': questions}})
        return {name: {
            'type': 'choice', 'choice': 'supports' if name == 'relation' else 'reported_fact',
            'confidence': 0.5 if name == 'kind' and state['statement'] == self.uncertain_text else 1,
        } for name in questions}


class PipelineTests(unittest.TestCase):
    def run_brief(self, responses, decider=None, docs=None, arm='B', **kwargs):
        writer = Writer(responses)
        result = run(docs or [document()], 'DEMO', '2026-09-23', arm, writer, decider,
                     'Generic writer prompt', QUESTIONS, 'Naive prompt', **kwargs)
        return (*result, writer)

    def test_valid_draft_needs_one_writer_call_and_uses_clean_units(self):
        brief, audit, _, writer = self.run_brief([complete_output(claim())])
        self.assertEqual(len(writer.calls), 1)
        self.assertEqual(audit['status'], 'completed')
        self.assertIn('Revenue was Rs 220 crore. [S1]', brief)
        self.assertNotIn('hidden source text', json.dumps(writer.calls))
        self.assertEqual(writer.calls[0]['request']['as_of'], '2026-09-23')

    def test_bad_citation_gets_only_one_repair_and_never_renders(self):
        invalid = claim('Unsupported statement.', 'S999.u01')
        brief, audit, artifacts, writer = self.run_brief([
            complete_output(claim(), invalid), complete_output(claim(), invalid)])
        self.assertEqual(len(writer.calls), 2)
        self.assertEqual(set(artifacts), {'draft', 'repair'})
        self.assertEqual(audit['status'], 'completed_with_drops')
        self.assertNotIn('Unsupported statement', brief)
        self.assertNotIn('S999', brief)
        self.assertEqual(len(audit['dropped']), 1)

    def test_malformed_draft_can_be_repaired_and_failed_repair_preserves_valid_draft_claim(self):
        brief, audit, _, writer = self.run_brief([{'bad': []}, complete_output(claim())])
        self.assertEqual(audit['status'], 'completed')
        self.assertEqual(len(writer.calls), 2)
        brief, audit, _, writer = self.run_brief([
            output(claim(), claim('Revenue was Rs 999 crore.')), ProviderError('repair_unavailable')])
        self.assertIn('Revenue was Rs 220 crore.', brief)
        self.assertNotIn('999', brief)
        self.assertIn('repair_unavailable', audit['repair_error'])

    def test_draft_outage_remains_explicit_when_repair_drops_item(self):
        decider = Decider([ProviderError('timeout')])
        brief, audit, _, writer = self.run_brief([output(claim()), output()], decider, arm='D')
        self.assertEqual(audit['status'], 'verification_unavailable')
        self.assertTrue(audit['unavailable'])
        self.assertIn('Verification was unavailable', brief)
        self.assertNotIn('Revenue was Rs 220 crore.', brief)
        self.assertEqual(len(writer.calls), 2)

    def test_repair_outage_remains_explicit_and_unchanged_claim_is_cached(self):
        decider = Decider(['supports', ProviderError('timeout')])
        invalid = claim('Revenue was Rs 999 crore.')
        repaired = output(claim(), claim('Reported revenue was Rs 220 crore.'))
        brief, audit, _, _ = self.run_brief([output(claim(), invalid), repaired], decider, arm='D')
        self.assertEqual(audit['status'], 'verification_unavailable')
        semantic = [call for call in decider.calls if 'relation' in call['request']['questions']]
        self.assertEqual(len(semantic), 2)
        self.assertIn('Revenue was Rs 220 crore. [S1]', brief)
        self.assertNotIn('Reported revenue was Rs 220 crore.', brief)

    def test_conflict_from_draft_retains_both_source_ids_after_repair(self):
        decider = Decider(['supports', 'contradicts', 'supports'])
        docs = [document(tier=3), document('S2')]
        brief, audit, _, _ = self.run_brief([
            output(claim()), output(claim(cite='S2.u01'))], decider, docs, 'D')
        self.assertEqual(audit['status'], 'incomplete')
        self.assertIn('reconcile [S1], [S2]', brief)
        self.assertEqual(audit['passes'][0]['results'][0]['verification']['conflicts'][0]['higher_tier_source'], 'S2')

    def test_future_sources_and_screening_outage_never_reach_writer(self):
        for docs, decider, status in [([document(age=-1)], None, 'incomplete'),
                                      ([document()], Decider(screen_error=True), 'verification_unavailable')]:
            brief, audit, _, writer = self.run_brief([], decider, docs, 'D')
            self.assertEqual(audit['status'], status)
            self.assertFalse(writer.calls)
            self.assertNotIn('Revenue was Rs 220 crore.', brief)

    def test_partial_audit_survives_initial_writer_error(self):
        audit, artifacts = {}, {}
        with self.assertRaises(ProviderError):
            self.run_brief([ProviderError('writer_unavailable')], audit=audit, artifacts=artifacts)
        self.assertIn('S1', audit['routes'])
        self.assertIn('S1', audit['removals'])

    def test_naive_baseline_receives_raw_pack_with_as_of(self):
        brief, audit, _, writer = self.run_brief([{'markdown': 'Raw baseline.'}], arm='A')
        self.assertEqual(audit['status'], 'completed_unchecked')
        self.assertIn('hidden source text', json.dumps(writer.calls))
        self.assertEqual(writer.calls[0]['request']['as_of'], '2026-09-23')

    def test_missing_only_open_questions_is_incomplete(self):
        data = complete_output(claim())
        data['open_questions'] = []
        brief, audit, _, writer = self.run_brief([data])
        self.assertEqual(audit['status'], 'incomplete')
        self.assertEqual(len(writer.calls), 1)
        self.assertIn('No accepted claims in required section: open_questions', audit['warnings'])
        self.assertIn('## Open questions\n\nNo verified item available.', brief)

    def test_snapshot_with_two_valid_claims_triggers_repair(self):
        repaired = complete_output(claim())
        draft = copy.deepcopy(repaired)
        draft['snapshot'].pop()
        _, audit, _, writer = self.run_brief([draft, repaired])
        self.assertEqual(len(writer.calls), 2)
        failure = audit['passes'][0]['failures'][0]
        self.assertEqual(failure['section'], 'snapshot')
        self.assertIsNone(failure['claim'])
        self.assertIn('retained 2', failure['errors'][0])
        self.assertEqual(writer.calls[1]['request']['repair_failures'], [failure])
        self.assertEqual(audit['status'], 'completed')
        self.assertFalse(audit['structure_failures'])
        self.assertEqual(audit['metrics']['dropped_claims'], 0)

    def test_snapshot_count_uses_only_claims_that_survive_filtering(self):
        repaired = complete_output(claim())
        draft = copy.deepcopy(repaired)
        draft['snapshot'][0] = claim('Revenue was Rs 999 crore.')
        _, audit, _, writer = self.run_brief([draft, repaired])
        first = audit['passes'][0]
        self.assertEqual(len(first['results']), 6)
        self.assertEqual(len(first['failures']), 2)
        structural = next(failure for failure in first['failures'] if failure['claim'] is None)
        self.assertIn('retained 2', structural['errors'][0])
        self.assertEqual(audit['status'], 'completed')
        self.assertEqual(len(writer.calls), 2)

    def test_unrepaired_snapshot_count_is_incomplete_without_bogus_claim_metrics(self):
        draft = complete_output(claim())
        draft['snapshot'].pop()
        _, audit, _, writer = self.run_brief([draft, draft])
        self.assertEqual(len(writer.calls), 2)
        self.assertEqual(audit['status'], 'incomplete')
        self.assertEqual(len(audit['structure_failures']), 1)
        self.assertEqual(audit['dropped'], [])
        self.assertEqual(audit['metrics']['accepted_claims'], 5)
        self.assertEqual(audit['metrics']['dropped_claims'], 0)
        for key in ('cited_ids_valid', 'numbers_matched'):
            self.assertEqual(audit['metrics'][key]['n'], 5)
            self.assertEqual(audit['metrics'][key]['k'], 5)

    def test_snapshot_upper_bound_requires_repair_and_accepts_four(self):
        draft = complete_output(claim())
        draft['snapshot'].extend([
            claim('The reported revenue was Rs 220 crore.'),
            claim('Results included revenue of Rs 220 crore.')])
        for final_count in (4, 5):
            with self.subTest(final_count=final_count):
                repaired = copy.deepcopy(draft)
                repaired['snapshot'] = repaired['snapshot'][:final_count]
                _, audit, _, writer = self.run_brief([draft, repaired])
                self.assertEqual(len(writer.calls), 2)
                self.assertEqual(audit['status'], 'completed' if final_count == 4 else 'incomplete')
                self.assertEqual(audit['metrics']['dropped_claims'], 0)

    def test_shape_failures_are_not_counted_as_dropped_claims(self):
        _, audit, _, writer = self.run_brief([{'bad': []}, {'bad': []}])
        self.assertEqual(len(writer.calls), 2)
        self.assertEqual(audit['status'], 'incomplete')
        self.assertTrue(audit['structure_failures'])
        self.assertEqual(audit['metrics']['dropped_claims'], 0)
        self.assertEqual(audit['metrics']['cited_ids_valid']['n'], 0)

    def test_provider_format_failure_uses_only_repair_slot_without_inventing_draft(self):
        _, audit, artifacts, writer = self.run_brief([format_error(), complete_output(claim())])
        self.assertEqual(len(writer.calls), 2)
        self.assertEqual(audit['status'], 'completed')
        self.assertEqual(audit['draft_error']['provider_code'], 'json_validate_failed')
        self.assertEqual([stage['stage'] for stage in audit['passes']], ['draft', 'format_repair'])
        self.assertEqual(audit['passes'][0]['results'], [])
        self.assertNotIn('draft', artifacts)
        self.assertIn('repair', artifacts)
        repair_payload = writer.calls[1]['request']
        self.assertNotIn('previous_output', repair_payload)
        self.assertIn('No draft content is available', repair_payload['task'])
        self.assertEqual(audit['metrics']['dropped_claims'], 0)
        self.assertEqual(audit['metrics']['cited_ids_valid']['n'], 6)

    def test_format_repair_cannot_trigger_another_repair_for_claim_failures(self):
        repaired = complete_output(claim())
        _, audit, _, writer = self.run_brief(
            [format_error(), repaired, repaired], Decider(['says_nothing']), arm='D')
        self.assertEqual(len(writer.calls), 2)
        self.assertEqual(len(writer.responses), 1)
        self.assertEqual(audit['status'], 'incomplete')
        self.assertEqual(audit['metrics']['dropped_claims'], 1)
        self.assertEqual(len(audit['structure_failures']), 1)

    def test_format_repair_cannot_trigger_another_repair_for_structure(self):
        repaired = complete_output(claim())
        repaired['snapshot'].pop()
        _, audit, _, writer = self.run_brief([format_error(), repaired, complete_output(claim())])
        self.assertEqual(len(writer.calls), 2)
        self.assertEqual(audit['status'], 'incomplete')
        self.assertEqual(audit['metrics']['dropped_claims'], 0)
        self.assertTrue(audit['structure_failures'])

    def test_second_provider_format_failure_is_terminal_and_auditable(self):
        audit, artifacts = {}, {}
        writer = Writer([format_error(), format_error(), complete_output(claim())])
        with self.assertRaises(ProviderError):
            run([document()], 'DEMO', '2026-09-23', 'B', writer, None,
                'System', QUESTIONS, 'Naive', audit=audit, artifacts=artifacts)
        self.assertEqual(len(writer.calls), 2)
        self.assertIn('draft_error', audit)
        self.assertIn('repair_error', audit)
        self.assertEqual(artifacts, {})

    def test_other_provider_errors_cannot_use_format_repair(self):
        for error in (format_error('invalid_request_error'), ProviderError('json_validate_failed', 400)):
            with self.subTest(error=error):
                writer = Writer([error, complete_output(claim())])
                with self.assertRaises(ProviderError):
                    run([document()], 'DEMO', '2026-09-23', 'B', writer, None,
                        'System', QUESTIONS, 'Naive')
                self.assertEqual(len(writer.calls), 1)

    def test_provider_format_failure_during_ordinary_repair_keeps_valid_draft(self):
        draft = complete_output(claim())
        draft['bull'].append(claim('Revenue was Rs 999 crore.'))
        brief, audit, artifacts, writer = self.run_brief([draft, format_error()])
        self.assertEqual(len(writer.calls), 2)
        self.assertEqual(audit['status'], 'completed_with_drops')
        self.assertIn('repair_error', audit)
        self.assertNotIn('draft_error', audit)
        self.assertIn('draft', artifacts)
        self.assertNotIn('repair', artifacts)
        self.assertIn('Revenue was Rs 220 crore.', brief)
        self.assertNotIn('999', brief)

    def test_draft_unconfirmed_kind_requests_repair_without_failing_supported_claim(self):
        text = 'The company reported revenue of Rs 220 crore.'
        draft = complete_output(claim())
        draft['bull'] = [claim(text)]
        repaired = complete_output(claim())
        _, audit, _, writer = self.run_brief([draft, repaired], UnconfirmedKindDecider(text), arm='D')
        self.assertEqual(len(writer.calls), 2)
        first = audit['passes'][0]
        self.assertFalse(first['failures'])
        self.assertTrue(all(result['accepted'] for result in first['results']))
        self.assertEqual(len(first['repair_requests']), 1)
        self.assertEqual(first['repair_requests'][0]['section'], 'bull')
        self.assertEqual(first['repair_requests'][0]['claim']['text'], text)
        self.assertEqual(writer.calls[1]['request']['repair_failures'], [])
        self.assertEqual(writer.calls[1]['request']['repair_requests'], first['repair_requests'])
        self.assertEqual(audit['status'], 'completed')
        self.assertEqual(audit['metrics']['dropped_claims'], 0)
        self.assertEqual(audit['metrics']['accepted_claims'], 6)
        self.assertEqual(audit['metrics']['numbers_matched']['n'], 6)

    def test_unconfirmed_supported_draft_remains_fallback_when_repair_is_unavailable(self):
        text = 'The company reported revenue of Rs 220 crore.'
        draft = complete_output(claim())
        draft['bull'] = [claim(text)]
        brief, audit, _, writer = self.run_brief(
            [draft, ProviderError('repair_unavailable')], UnconfirmedKindDecider(text), arm='D')
        self.assertEqual(len(writer.calls), 2)
        self.assertIn(text, brief)
        self.assertEqual(audit['accepted']['bull'], draft['bull'])
        self.assertEqual(audit['status'], 'completed')
        self.assertIn('repair_error', audit)
        self.assertFalse(audit['dropped'])
        self.assertFalse(audit['structure_failures'])

    def test_unconfirmed_kind_after_repair_is_retained_without_a_third_generation(self):
        text = 'The company reported revenue of Rs 220 crore.'
        draft = complete_output(claim())
        draft['bull'] = [claim(text)]
        decider = UnconfirmedKindDecider(text)
        brief, audit, _, writer = self.run_brief([draft, draft, draft], decider, arm='D')
        self.assertEqual(len(writer.calls), 2)
        self.assertEqual(len(writer.responses), 1)
        self.assertIn(text, brief)
        self.assertEqual(audit['status'], 'completed')
        self.assertEqual(audit['passes'][1]['repair_requests'], [])
        result = next(r for r in audit['passes'][1]['results'] if r['section'] == 'bull')
        self.assertTrue(result['accepted'])
        self.assertFalse(result['verification']['kind_confirmed'])
        self.assertEqual(audit['metrics']['dropped_claims'], 0)
        matching_calls = [call for call in decider.calls
                          if call['request']['state'].get('statement') == text]
        self.assertEqual(len(matching_calls), 2)  # First check plus order retry; repair reuses them.

    def test_format_repair_unconfirmed_kind_does_not_create_another_repair_request(self):
        text = 'The company reported revenue of Rs 220 crore.'
        repaired = complete_output(claim())
        repaired['bull'] = [claim(text)]
        brief, audit, _, writer = self.run_brief(
            [format_error(), repaired, repaired], UnconfirmedKindDecider(text), arm='D')
        self.assertEqual(len(writer.calls), 2)
        self.assertEqual(audit['status'], 'completed')
        self.assertIn(text, brief)
        self.assertEqual(audit['passes'][1]['stage'], 'format_repair')
        self.assertEqual(audit['passes'][1]['repair_requests'], [])
        self.assertEqual(audit['metrics']['dropped_claims'], 0)

    def test_snapshot_unconfirmed_kind_is_a_failure_not_a_soft_request(self):
        text = 'The company reported revenue of Rs 220 crore.'
        draft = complete_output(claim(text))
        _, audit, _, writer = self.run_brief([draft, draft], UnconfirmedKindDecider(text), arm='D')
        self.assertEqual(len(writer.calls), 2)
        self.assertEqual(audit['passes'][0]['repair_requests'], [])
        self.assertTrue(audit['passes'][0]['failures'])
        self.assertEqual(audit['metrics']['dropped_claims'], 1)
        self.assertEqual(audit['status'], 'incomplete')

    def test_wrong_date_counts_as_failed_number_date_check_per_claim(self):
        draft = complete_output(claim())
        draft['bull'] = [claim('On 23 September 2026, the company reported revenue of Rs 220 crore.')]
        _, audit, _, _ = self.run_brief([draft, draft])
        failed = audit['dropped'][0]
        self.assertTrue(any(error.startswith('Date ') for error in failed['code_errors']))
        metric = audit['metrics']['numbers_matched']
        self.assertEqual((metric['k'], metric['n']), (5, 6))

    def test_progress_reports_stage_counts_without_source_content(self):
        progress = []
        invalid = claim('Unsupported private source content.', 'S999.u01')
        self.run_brief([complete_output(claim(), invalid), complete_output(claim())],
                       on_progress=progress.append)
        self.assertEqual(progress, [
            'Loaded 1 source document(s).',
            'Triaging source documents.',
            'Generating a draft from 1 admitted source document(s).',
            'Checking 7 claim(s).',
            'Draft checks found 1 failure(s).',
            'Requesting the single repair attempt.',
            'Checking 6 claim(s).',
            'Repair checks found 0 failure(s).',
            'Accepted 6 claim(s); rendering brief.',
        ])
        self.assertNotIn('private source content', '\n'.join(progress))


if __name__ == '__main__':
    unittest.main()
