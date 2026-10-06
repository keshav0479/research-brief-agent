import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from brief_agent import cli
from brief_agent.clients import ProviderError
from tests.test_pipeline import Decider, Writer, claim, complete_output, document, output


class CliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'prompts').mkdir()
        for name in ('system.md', 'naive.md', 'repair.md', 'jev_questions.json'):
            (self.root / 'prompts' / name).write_bytes((cli.ROOT / 'prompts' / name).read_bytes())
        self.args = ['--ticker', 'DEMO', '--docs', 'research_pack', '--as-of', '2026-09-23',
                     '--label', 'test-run', '--arm', 'B']

    def invoke(self, writer):
        with patch.object(cli, 'ROOT', self.root), patch.object(cli, 'load_documents', return_value=[document()]), \
                patch.object(cli, 'WriterClient', return_value=writer), \
                patch.object(cli, 'JevClient', return_value=Decider()), \
                patch.dict(cli.os.environ, {'GROQ_API_KEY': 'test-key'}, clear=True), \
                contextlib.redirect_stdout(io.StringIO()):
            return cli.main(self.args)

    def read(self, name):
        return json.loads((self.root / 'runs/test-run' / name).read_text())

    def test_success_writes_reproducible_artifacts_without_requests_or_machine_paths(self):
        writer = Writer([complete_output(claim())])
        self.assertEqual(self.invoke(writer), 0)
        calls = self.read('writer-responses.json')
        self.assertNotIn('request', calls[0])
        self.assertEqual(len(calls[0]['request_sha256']), 64)
        self.assertIn('request', writer.calls[0])
        self.assertNotIn('a maker of widgets', json.dumps(calls))
        self.assertEqual(self.read('config.json')['as_of'], '2026-09-23')
        self.assertEqual(self.read('checks.json')['status'], 'completed')
        for path in (self.root / 'runs/test-run').glob('*.json'):
            self.assertNotIn(str(self.root), path.read_text())
            self.assertNotIn('test-key', path.read_text())
        self.assertTrue((self.root / 'runs/test-run/source_snapshot/prompts/system.md').is_file())

    def test_provider_failure_keeps_partial_audit_and_logs(self):
        self.assertEqual(self.invoke(Writer([ProviderError('writer_unavailable')])), 2)
        checks = self.read('checks.json')
        self.assertEqual(checks['status'], 'failed')
        self.assertIn('S1', checks['routes'])
        self.assertIn('writer_unavailable', checks['error'])
        self.assertEqual(len(self.read('writer-responses.json')), 1)
        self.assertFalse((self.root / 'runs/test-run/brief.md').exists())

    def test_unexpected_failure_is_recorded_without_arbitrary_exception_text(self):
        self.assertEqual(self.invoke(Writer([RuntimeError('private-machine-text')])), 2)
        checks = self.read('checks.json')
        self.assertEqual(checks['error_type'], 'RuntimeError')
        self.assertIn('S1', checks['routes'])
        self.assertNotIn('private-machine-text', json.dumps(checks))

    def test_existing_label_is_not_overwritten(self):
        self.invoke(Writer([complete_output(claim())]))
        before = (self.root / 'runs/test-run/brief.md').read_bytes()
        with self.assertRaises(FileExistsError):
            self.invoke(Writer([output()]))
        self.assertEqual((self.root / 'runs/test-run/brief.md').read_bytes(), before)

    def test_request_hashing_keeps_raw_response_unchanged(self):
        calls = [{'request': {'state': 'entire private supplied source'},
                  'raw_response': {'answers': {'label': 'unchanged'}}}]
        persisted = cli.persisted_calls(calls)
        self.assertEqual(persisted[0]['raw_response'], calls[0]['raw_response'])
        self.assertNotIn('entire private supplied source', json.dumps(persisted))
        self.assertIn('request', calls[0])


if __name__ == '__main__':
    unittest.main()
