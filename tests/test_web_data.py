"""Saved reports must retain their identity and never borrow changed evidence."""

from datetime import date
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from brief_agent.load import load_documents
from research_ui.data import list_runs, load_run, run_folder


class SavedRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        parser = self.root / 'brief_agent/load.py'
        parser.parent.mkdir()
        parser.write_bytes((Path(__file__).resolve().parents[1] / 'brief_agent/load.py').read_bytes())
        self.docs = self.root / 'research_pack'
        self.docs.mkdir()
        (self.docs / 'results.md').write_text('''---
source: Example company release
url: https://example.test/ir/results
published: 2026-08-12
type: official company filing
---
# Example Ltd results
Example Ltd (NSE: EXAMPLE) reported revenue of Rs 900 crore, up 12% year-on-year.
''')
        self.folder = self.root / 'runs/example-run'
        self.folder.mkdir(parents=True)
        config = {'ticker': 'EXAMPLE', 'as_of': '2026-09-23', 'arm': 'D',
                  'writer_requested_model': 'test-writer', 'jev_requested_model': 'test-checker',
                  'pack_hashes': {'results.md': hashlib.sha256((self.docs / 'results.md').read_bytes()).hexdigest()},
                  'source_manifest': {'brief_agent/load.py': hashlib.sha256(parser.read_bytes()).hexdigest()},
                  'manual_edits_to_brief': False}
        self.claim = {'text': 'Revenue was Rs 900 crore.', 'kind': 'reported_fact', 'cites': ['S1.u01']}
        checks = {'status': 'completed', 'profile': {'name': 'Example Ltd'},
                  'routes': {'S1': {'status': 'evidence', 'reason': 'Matched issuer.'}},
                  'accepted': {'snapshot': [self.claim]},
                  'passes': [{'stage': 'draft', 'results': [{'section': 'snapshot', 'accepted': True,
                     'claim': self.claim, 'final_claim': self.claim, 'code_errors': [],
                     'verification': {'errors': [], 'kind_confirmed': False, 'unavailable': False}}],
                     'failures': [], 'repair_requests': []}]}
        self.write('config.json', config)
        self.write('checks.json', checks)
        self.markdown = '# EXAMPLE research brief\n\nOriginal saved text, including ₹.\n'
        (self.folder / 'brief.md').write_text(self.markdown)
        # Private provider payloads must not be read or returned by the adapter.
        (self.folder / 'writer-responses.json').write_text('{"secret":"DO_NOT_DISPLAY"}')

    def write(self, name, value):
        (self.folder / name).write_text(json.dumps(value))

    def test_report_and_quotes_match_saved_identity(self):
        result = load_run(self.root, 'example-run')
        self.assertEqual(result['brief_markdown'], self.markdown)
        self.assertTrue(result['evidence_available'])
        c = result['sections'][0]['claims'][0]
        self.assertEqual(c['text'], self.claim['text'])
        original = load_documents(self.docs, date(2026, 9, 23))[0]
        self.assertEqual(c['evidence'][0]['quote'], original.quotes['S1.u01'])
        self.assertEqual(c['checks']['support'], 'supported')
        self.assertIs(c['checks']['kind_confirmed'], False)
        self.assertNotIn('DO_NOT_DISPLAY', json.dumps(result))
        self.assertEqual(result['run']['company'], 'Example Ltd')

    def test_changed_source_cannot_supply_old_report_quotes(self):
        p = self.docs / 'results.md'
        p.write_text(p.read_text().replace('900', '999'))
        result = load_run(self.root, 'example-run')
        self.assertFalse(result['evidence_available'])
        self.assertEqual(result['sections'][0]['claims'][0]['evidence'], [])
        self.assertEqual(result['brief_markdown'], self.markdown)
        self.assertIn('changed', result['evidence_note'])

    def test_changed_parser_cannot_reattach_source_ids(self):
        (self.root / 'brief_agent/load.py').write_text('changed parser')
        result = load_run(self.root, 'example-run')
        self.assertFalse(result['evidence_available'])
        self.assertIn('parser differs', result['evidence_note'])

    def test_extra_document_cannot_shift_old_citation_ids(self):
        (self.docs / 'earlier.md').write_bytes((self.docs / 'results.md').read_bytes())
        result = load_run(self.root, 'example-run')
        self.assertFalse(result['evidence_available'])

    def test_ui_upload_snapshot_takes_precedence_over_changed_demo_pack(self):
        saved = self.root / '.web-workspace/inputs/example-run'
        saved.mkdir(parents=True)
        (saved / 'results.md').write_bytes((self.docs / 'results.md').read_bytes())
        (self.docs / 'results.md').write_text('changed original')
        self.assertTrue(load_run(self.root, 'example-run')['evidence_available'])

    def test_missing_checks_is_an_incomplete_run_not_a_success(self):
        (self.folder / 'checks.json').unlink()
        result = load_run(self.root, 'example-run')
        self.assertEqual(result['run']['status'], 'incomplete')
        self.assertTrue(all(not section['claims'] for section in result['sections']))

    def test_saved_failure_keeps_its_sanitized_reason(self):
        self.write('checks.json', {'status': 'failed',
                                  'error': 'Provider error: RateLimitError (HTTP 429)',
                                  'repair_error': 'Provider error: BadRequestError (HTTP 400)'})
        result = load_run(self.root, 'example-run')
        self.assertEqual(result['run']['status'], 'failed')
        self.assertEqual(result['audit']['error'], 'Provider error: RateLimitError (HTTP 429)')
        self.assertEqual(result['audit']['repair_error'], 'Provider error: BadRequestError (HTTP 400)')

    def test_transport_diagnostics_do_not_expose_provider_payloads(self):
        self.write('writer-responses.json', [{'status': 'error', 'http_status': 429,
                   'retry_delay_s': 60, 'provider_requested_wait_s': 12,
                   'raw_response': {'secret': 'DO_NOT_DISPLAY'}, 'request_sha256': 'private-fingerprint'}])
        result = load_run(self.root, 'example-run')
        self.assertEqual(result['audit']['transport_errors'], [
            {'provider': 'writer', 'http_status': 429, 'retry_delay_s': 60, 'provider_requested_wait_s': 12}])
        self.assertNotIn('DO_NOT_DISPLAY', json.dumps(result))
        self.assertNotIn('private-fingerprint', json.dumps(result))

    def test_history_skips_partial_json_and_symlink_directories(self):
        broken = self.root / 'runs/broken'
        broken.mkdir()
        (broken / 'config.json').write_text('{')
        (self.root / 'runs/alias').symlink_to(self.folder, target_is_directory=True)
        self.assertEqual([r['id'] for r in list_runs(self.root)], ['example-run'])

    def test_run_id_is_not_a_path(self):
        for name in ('../example-run', '/etc/passwd', '..', 'a/b', 'a\\b'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                run_folder(self.root, name)

    def test_symlinked_document_is_not_loaded(self):
        original = self.docs / 'results.md'
        copy = self.root / 'outside.md'
        copy.write_bytes(original.read_bytes())
        original.unlink()
        original.symlink_to(copy)
        self.assertFalse(load_run(self.root, 'example-run')['evidence_available'])

    def test_symlinked_artifact_is_not_returned(self):
        (self.folder / 'brief.md').unlink()
        (self.folder / 'brief.md').symlink_to(self.docs / 'results.md')
        with self.assertRaises(ValueError):
            load_run(self.root, 'example-run')

    def test_renderer_notices_are_not_lost_or_labeled_generated_claims(self):
        saved = '# EXAMPLE research brief\n\n## Open questions\n\n- Verification was unavailable; inspect [S1].\n\n## Sources\n'
        (self.folder / 'brief.md').write_text(saved)
        result = load_run(self.root, 'example-run')
        question = next(s for s in result['sections'] if s['key'] == 'open_questions')
        self.assertEqual(question['notices'], ['Verification was unavailable; inspect [S1].'])
        self.assertEqual(question['claims'], [])
        self.assertEqual(result['brief_markdown'], saved)

    def test_historical_control_characters_are_flagged_without_rewriting(self):
        saved = self.markdown + '\x15'
        (self.folder / 'brief.md').write_text(saved)
        result = load_run(self.root, 'example-run')
        self.assertEqual(result['brief_markdown'], saved)
        self.assertTrue(any('control characters' in w for w in result['audit']['warnings']))


if __name__ == '__main__':
    unittest.main()
