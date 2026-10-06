import http.client
import json
from pathlib import Path
import queue
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from research_ui.server import ResearchServer


SOURCE = ('---\r\nsource: Example IR\r\nurl: https://example.test/results\r\n'
          'published: 2026-09-22\r\ntype: official company filing\r\n---\r\n'
          '# Results\r\n\r\nExample Ltd (NSE: DEMO) reported revenue of ₹220 crore.\r\n')


class StubProcess:
    def __init__(self, blocked=False, returncode=0):
        self.returncode = None if blocked else returncode
        self.final_returncode = returncode
        self.terminated = False
        self.lines = queue.Queue()
        self.lines.put('Loading documents.\n')
        self.lines.put('Provider test-writer-key is configured.\n')
        if not blocked:
            self.lines.put(None)
        self.stdout = StubOutput(self.lines)

    def wait(self, timeout=None):
        return self.returncode if self.returncode is not None else self.final_returncode

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15
        self.lines.put(None)

    def kill(self):
        self.terminate()


class StubOutput:
    def __init__(self, lines):
        self.lines, self.closed = lines, False

    def __iter__(self):
        while True:
            line = self.lines.get(timeout=5)
            if line is None:
                return
            yield line

    def close(self):
        self.closed = True


class WebServerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        static = self.root / 'research_ui/static'
        static.mkdir(parents=True)
        (static / 'index.html').write_text('<!doctype html><title>Local workspace</title>')
        self.calls = []
        self.processes = []
        self.blocked = False
        self.exitcode = 0
        self.run_status = 'completed'
        self.env = {'GROQ_API_KEY': 'test-writer-key', 'TYPESAFE_API_KEY': 'test-reference-key'}
        self.server = ResearchServer(self.root, port=0, env=self.env, popen=self.spawn)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)
        self.origin = f'http://127.0.0.1:{self.server.server_port}'

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def spawn(self, command, **kwargs):
        self.calls.append((command, kwargs))
        run_id = command[command.index('--label') + 1]
        folder = self.root / 'runs' / run_id
        folder.mkdir(parents=True)
        (folder / 'checks.json').write_text(json.dumps({'status': self.run_status}))
        (folder / 'config.json').write_text('{}')
        (folder / 'brief.md').write_bytes(b'# Exact report\r\n\r\nSaved bytes.\r\n')
        process = StubProcess(self.blocked, self.exitcode)
        self.processes.append(process)
        return process

    def request(self, method, path, body=None, headers=None):
        headers = dict(headers or {})
        if body is not None:
            body = json.dumps(body).encode('utf-8')
            headers.setdefault('Content-Type', 'application/json')
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        data = response.read()
        result = response.status, dict(response.getheaders()), data
        connection.close()
        return result

    def payload(self, **changes):
        value = {'ticker': 'DEMO', 'as_of': '2026-09-23', 'route': 'free', 'use_example': False,
                 'documents': [{'name': 'results.md', 'content': SOURCE}]}
        value.update(changes)
        return value

    def start(self, **changes):
        status, headers, body = self.request('POST', '/api/runs', self.payload(**changes), {'Origin': self.origin})
        return status, json.loads(body)

    def settled(self, job_id):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status, _, body = self.request('GET', '/api/jobs/' + job_id)
            self.assertEqual(status, 200)
            job = json.loads(body)
            if job['state'] in {'completed', 'failed'}:
                return job
            time.sleep(0.01)
        self.fail('Stub job did not settle')

    def test_status_static_and_no_secret_exports(self):
        status, headers, body = self.request('GET', '/api/status')
        self.assertEqual(status, 200)
        value = json.loads(body)
        self.assertTrue(value['writer']['configured'])
        self.assertEqual(value['demo_run'], 'v6.1-reference-d-01')
        self.assertEqual(value['limits']['files'], 24)
        self.assertNotIn('test-writer-key', body.decode())
        self.assertNotIn('test-reference-key', body.decode())
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        self.assertNotIn('Access-Control-Allow-Origin', headers)
        self.assertEqual(self.request('GET', '/')[0], 200)
        self.assertEqual(self.server.server_address[0], '127.0.0.1')

    def test_real_http_upload_preserves_bytes_and_explicit_reference_route(self):
        status, queued = self.start(route='reference')
        self.assertEqual(status, 202)
        job = self.settled(queued['id'])
        self.assertEqual((job['state'], job['status']), ('completed', 'completed'))
        command, options = self.calls[0]
        self.assertEqual(command[1:5], ['-u', '-B', '-m', 'brief_agent'])
        self.assertEqual(command[-2:], ['--arm', 'D'])
        self.assertEqual(options['cwd'], self.root)
        self.assertEqual(options['env']['JEV_URL'], 'https://api.typesafe.ai/v1/systemone')
        self.assertEqual(options['env']['JEV_MODEL'], 'jev-1.13.0')
        self.assertEqual(options['env']['JEV_API_KEY'], 'test-reference-key')
        folder = self.root / '.web-workspace/inputs' / job['run_id']
        self.assertEqual((folder / 'results.md').read_bytes(), SOURCE.encode('utf-8'))
        self.assertIn('Loading documents.', [event['message'] for event in job['events']])
        self.assertNotIn('test-writer-key', json.dumps(job))
        self.assertNotIn('test-reference-key', json.dumps(job))
        self.server.workspace.worker.join(timeout=1)
        self.assertTrue(self.processes[0].stdout.closed)

    def test_example_pack_and_free_route_ignore_reference_environment(self):
        pack = self.root / 'research_pack'
        pack.mkdir()
        (pack / 'source.md').write_bytes(SOURCE.encode('utf-8'))
        (pack / 'ignored.txt').write_text('not a source')
        status, queued = self.start(use_example=True, documents=[])
        self.assertEqual(status, 202)
        job = self.settled(queued['id'])
        env = self.calls[0][1]['env']
        self.assertEqual(env['JEV_API_KEY'], 'public')
        self.assertEqual(env['JEV_URL'], 'https://opencode.ai/zen/v1/systemone')
        folder = self.root / '.web-workspace/inputs' / job['run_id']
        self.assertEqual([p.name for p in folder.iterdir()], ['source.md'])
        self.assertEqual((folder / 'source.md').read_bytes(), SOURCE.encode('utf-8'))

    def test_missing_keys_prevent_launch(self):
        self.server.workspace.env.pop('GROQ_API_KEY')
        self.assertEqual(self.start()[0], 503)
        self.server.workspace.env['GROQ_API_KEY'] = 'test-writer-key'
        self.server.workspace.env.pop('TYPESAFE_API_KEY')
        self.assertEqual(self.start(route='reference')[0], 503)
        self.assertFalse(self.calls)

    def test_invalid_uploads_and_limits_never_launch(self):
        cases = [({'documents': [{'name': '../escape.md', 'content': SOURCE}]}, 400),
                 ({'documents': [{'name': 'sub\\escape.md', 'content': SOURCE}]}, 400),
                 ({'documents': [{'name': 'a.txt', 'content': SOURCE}]}, 400),
                 ({'documents': [{'name': 'a.md', 'content': '# Missing metadata'}]}, 400),
                 ({'documents': [{'name': 'a.md', 'content': SOURCE}] * 2}, 400),
                 ({'documents': [{'name': 'large.md', 'content': 'x' * 200001}]}, 413),
                 ({'documents': [{'name': f'{i}.md', 'content': SOURCE} for i in range(25)]}, 413),
                 ({'documents': [{'name': f'{i}.md', 'content': 'x' * 180000} for i in range(6)]}, 413),
                 ({'ticker': '../escape'}, 400), ({'as_of': '2026-02-30'}, 400),
                 ({'as_of': '20260923'}, 400), ({'route': 'https://untrusted.test'}, 400),
                 ({'route': []}, 400), ({'provider_url': 'https://untrusted.test'}, 400)]
        for changes, expected in cases:
            with self.subTest(changes=list(changes)):
                self.assertEqual(self.start(**changes)[0], expected)
        self.assertFalse(self.calls)
        inputs = self.root / '.web-workspace/inputs'
        self.assertEqual(list(inputs.iterdir()) if inputs.is_dir() else [], [])

    def test_cross_origin_host_and_non_json_requests_are_rejected(self):
        for headers, expected in [({}, 403), ({'Origin': 'https://elsewhere.test'}, 403),
                                  ({'Origin': self.origin, 'Host': 'attacker.test'}, 403),
                                  ({'Origin': self.origin, 'Content-Type': 'text/plain'}, 415)]:
            self.assertEqual(self.request('POST', '/api/runs', self.payload(), headers)[0], expected)
        self.assertEqual(self.request('GET', '/api/status', headers={'Host': 'attacker.test'})[0], 403)
        localhost = f'localhost:{self.server.server_port}'
        self.assertEqual(self.request('GET', '/api/status', headers={'Host': localhost})[0], 200)
        self.assertEqual(self.request('POST', '/api/runs', self.payload(),
                                      {'Host': localhost, 'Origin': self.origin})[0], 403)
        self.assertFalse(self.calls)

    def test_input_storage_symlinks_are_rejected_before_writing(self):
        outside = self.root / 'outside'
        outside.mkdir()
        storage = self.root / '.web-workspace'
        storage.symlink_to(outside, target_is_directory=True)
        self.assertEqual(self.start()[0], 400)
        self.assertEqual(list(outside.iterdir()), [])
        storage.unlink()
        storage.mkdir()
        (storage / 'inputs').symlink_to(outside, target_is_directory=True)
        self.assertEqual(self.start()[0], 400)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse(self.calls)

    def test_only_one_live_job_and_shutdown_terminates_it(self):
        self.blocked = True
        status, queued = self.start()
        self.assertEqual(status, 202)
        self.assertEqual(self.start()[0], 409)
        self.server.workspace.stop()
        job = self.settled(queued['id'])
        self.assertEqual(job['state'], 'failed')
        self.assertTrue(self.processes[0].terminated)
        self.assertEqual(self.start()[0], 503)

    def test_finished_job_cleanup_cannot_clear_new_jobs_process(self):
        workspace = self.server.workspace
        original_event = workspace.event
        first_terminal, release_first, second_started = (threading.Event() for _ in range(3))

        def event(job, message):
            original_event(job, message)
            if message == 'Run completed.' and not first_terminal.is_set():
                first_terminal.set()
                release_first.wait(timeout=3)
            if message.startswith('Research process started') and len(self.processes) == 2:
                second_started.set()

        with patch.object(workspace, 'event', side_effect=event):
            try:
                self.assertEqual(self.start()[0], 202)
                self.assertTrue(first_terminal.wait(timeout=1))
                first_worker = workspace.worker
                self.blocked = True
                status, second = self.start()
                self.assertEqual(status, 202)
                self.assertTrue(second_started.wait(timeout=1))
                second_process, second_worker = self.processes[1], workspace.worker
                release_first.set()
                first_worker.join(timeout=1)
                self.assertFalse(first_worker.is_alive())
                self.assertIs(workspace.process, second_process)
                workspace.stop()
                self.assertTrue(second_process.terminated)
                self.assertFalse(second_worker.is_alive())
                self.assertEqual(workspace.job(second['id'])['state'], 'failed')
            finally:
                release_first.set()

    def test_exit_code_and_audit_must_both_indicate_success(self):
        for exitcode, status in [(2, 'verification_unavailable'), (1, 'completed'), (0, 'failed')]:
            self.exitcode, self.run_status = exitcode, status
            code, queued = self.start()
            self.assertEqual(code, 202)
            job = self.settled(queued['id'])
            self.assertEqual(job['state'], 'failed')
            self.assertEqual(job['status'], status)
            self.assertTrue((self.root / 'runs' / job['run_id'] / 'checks.json').is_file())

    def test_brief_download_is_exact_and_audit_uses_sanitized_reader(self):
        _, queued = self.start()
        job = self.settled(queued['id'])
        prefix = '/api/runs/' + job['run_id']
        history = json.loads(self.request('GET', '/api/runs')[2])
        self.assertIn(job['run_id'], [run['id'] for run in history['runs']])
        status, headers, body = self.request('GET', prefix + '/brief.md')
        self.assertEqual(status, 200)
        self.assertEqual(body, (self.root / 'runs' / job['run_id'] / 'brief.md').read_bytes())
        self.assertIn('attachment;', headers['Content-Disposition'])
        with patch('research_ui.server.load_run', return_value={'safe': True}) as load:
            self.assertEqual(json.loads(self.request('GET', prefix)[2]), {'safe': True})
            status, headers, body = self.request('GET', prefix + '/audit.json')
            self.assertEqual(json.loads(body), {'safe': True})
            self.assertEqual(load.call_count, 2)
            self.assertIn('attachment;', headers['Content-Disposition'])
        self.assertEqual(self.request('GET', prefix + '/writer-responses.json')[0], 404)
        self.assertEqual(self.request('GET', '/api/runs/%2e%2e/brief.md')[0], 400)
        self.assertEqual(self.request('GET', '/api/jobs/unknown')[0], 404)


if __name__ == '__main__':
    unittest.main()
