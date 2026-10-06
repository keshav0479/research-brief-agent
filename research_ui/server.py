"""A loopback-only web workspace around the existing research CLI."""

import argparse
import copy
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import threading
from urllib.parse import unquote, urlsplit

from brief_agent.load import load_documents
from .data import list_runs, load_run, read_artifact, run_folder


ROOT = Path(__file__).resolve().parents[1]
LIMITS = {'files': 24, 'file_bytes': 200_000, 'total_bytes': 1_000_000}
RUN_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}')
TICKER = re.compile(r'[A-Za-z0-9][A-Za-z0-9&_.-]{0,29}')
ROUTES = {
    'free': ('Free Zen route', 'https://opencode.ai/zen/v1/systemone', 'jev-1.13-free'),
    'reference': ('TypeSafe reference route', 'https://api.typesafe.ai/v1/systemone', 'jev-1.13.0'),
}


def now():
    return datetime.now(timezone.utc).isoformat()


class RequestError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


class Workspace:
    def __init__(self, root, env=None, popen=None):
        self.root = Path(root).resolve()
        self.env = dict(os.environ if env is None else env)
        self.popen = subprocess.Popen if popen is None else popen
        self.jobs = {}
        self.lock = threading.RLock()
        self.process = None
        self.worker = None
        self.stopping = False

    def status(self):
        with self.lock:
            active = next((copy.deepcopy(job) for job in self.jobs.values()
                           if job['state'] in {'queued', 'running'}), None)
        return {
            'writer': {'configured': bool(self.env.get('WRITER_API_KEY') or self.env.get('GROQ_API_KEY')),
                       'model': self.env.get('WRITER_MODEL', 'qwen/qwen3.8-27b')},
            'verifiers': [{'id': key, 'label': values[0], 'model': values[2],
                           'configured': key == 'free' or bool(self.env.get('TYPESAFE_API_KEY'))}
                          for key, values in ROUTES.items()],
            'active_job': active, 'defaults': {'ticker': 'SRVCABLE', 'as_of': '2026-09-23'},
            'limits': LIMITS, 'demo_run': 'v6.1-reference-d-01',
        }

    def job(self, job_id):
        if not RUN_ID.fullmatch(job_id):
            raise RequestError(400, 'Invalid job identifier.')
        with self.lock:
            if job_id not in self.jobs:
                raise RequestError(404, 'Job not found.')
            return copy.deepcopy(self.jobs[job_id])

    def event(self, job, message):
        for name in ('WRITER_API_KEY', 'GROQ_API_KEY', 'TYPESAFE_API_KEY', 'JEV_API_KEY'):
            value = self.env.get(name)
            if value and value != 'public':
                message = message.replace(value, '[redacted]')
        message = message.replace(str(self.root), '<workspace>')[:2000]
        with self.lock:
            job['events'].append({'message': message, 'at': now()})

    def start(self, request):
        if not isinstance(request, dict) or set(request) - {'ticker', 'as_of', 'route', 'use_example', 'documents'}:
            raise RequestError(400, 'Use the documented run fields only.')
        ticker, as_of, route = request.get('ticker'), request.get('as_of'), request.get('route')
        if not isinstance(ticker, str) or not TICKER.fullmatch(ticker):
            raise RequestError(400, 'Ticker must contain letters, numbers, &, dot, underscore or hyphen.')
        if not isinstance(as_of, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', as_of):
            raise RequestError(400, 'As-of date must use YYYY-MM-DD.')
        try:
            as_of_date = date.fromisoformat(as_of)
        except ValueError:
            raise RequestError(400, 'As-of date is not valid.') from None
        if not isinstance(route, str) or route not in ROUTES:
            raise RequestError(400, 'Select the free or reference verifier route.')
        if not (self.env.get('WRITER_API_KEY') or self.env.get('GROQ_API_KEY')):
            raise RequestError(503, 'Writer API key is not configured.')
        if route == 'reference' and not self.env.get('TYPESAFE_API_KEY'):
            raise RequestError(503, 'TypeSafe reference API key is not configured.')
        example, uploads = request.get('use_example', False), request.get('documents', [])
        if not isinstance(example, bool) or not isinstance(uploads, list) or (example and uploads):
            raise RequestError(400, 'Choose the example pack or uploaded documents.')
        files = []
        if example:
            pack = self.root / 'research_pack'
            if pack.is_symlink():
                raise RequestError(400, 'The example pack must be a local document folder.')
            for path in sorted(pack.glob('*.md')):
                if path.is_symlink() or not path.is_file():
                    raise RequestError(400, 'The example pack contains an unsupported file.')
                if path.stat().st_size > LIMITS['file_bytes']:
                    raise RequestError(413, 'A document exceeds the per-file limit.')
                files.append((path.name, path.read_bytes()))
        else:
            if len(uploads) > LIMITS['files']:
                raise RequestError(413, 'Too many documents.')
            for upload in uploads:
                if not isinstance(upload, dict) or set(upload) != {'name', 'content'}:
                    raise RequestError(400, 'Each document needs a name and UTF-8 content.')
                name, content = upload['name'], upload['content']
                if (not isinstance(name, str) or not name.endswith('.md') or len(name) > 200
                        or name != Path(name).name or '/' in name or '\\' in name
                        or any(ord(char) < 32 for char in name)):
                    raise RequestError(400, 'Document names must be Markdown basenames, without folders.')
                if not isinstance(content, str):
                    raise RequestError(400, 'Document content must be UTF-8 text.')
                try:
                    files.append((name, content.encode('utf-8')))
                except UnicodeError:
                    raise RequestError(400, 'Document content must be UTF-8 text.') from None
        if not files:
            raise RequestError(400, 'Provide at least one Markdown document.')
        if len(files) > LIMITS['files'] or any(len(body) > LIMITS['file_bytes'] for _, body in files):
            raise RequestError(413, 'Document count or size exceeds the upload limits.')
        if sum(len(body) for _, body in files) > LIMITS['total_bytes']:
            raise RequestError(413, 'Documents exceed the total upload limit.')
        if len({name.casefold() for name, _ in files}) != len(files):
            raise RequestError(400, 'Document filenames must be unique.')
        with self.lock:
            if self.stopping:
                raise RequestError(503, 'The workspace is shutting down.')
            if any(job['state'] in {'queued', 'running'} for job in self.jobs.values()):
                raise RequestError(409, 'Another run is already active.')
            run_id = 'ui-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + secrets.token_hex(4)
            storage = self.root / '.web-workspace'
            inputs = storage / 'inputs'
            if storage.is_symlink() or inputs.is_symlink():
                raise RequestError(400, 'Input storage must use local folders, without links.')
            folder = inputs / run_id
            folder.mkdir(parents=True, exist_ok=False)
            for name, body in files:
                (folder / name).write_bytes(body)
            try:
                load_documents(folder, as_of_date)
            except (ValueError, UnicodeError, OSError):
                shutil.rmtree(folder, ignore_errors=True)
                raise RequestError(400, 'Invalid document pack. Each Markdown file needs source, url, published and type front matter.') from None
            job = {'id': run_id, 'run_id': run_id, 'state': 'queued', 'events': [],
                   'error': None, 'status': None}
            self.jobs[run_id] = job
            self.event(job, 'Document pack validated. Run queued.')
            self.worker = threading.Thread(target=self._run, args=(job, ticker.upper(), as_of, route, folder), daemon=True)
            self.worker.start()
            return copy.deepcopy(job)

    def _run(self, job, ticker, as_of, route, folder):
        env = dict(self.env)
        env.update(JEV_URL=ROUTES[route][1], JEV_MODEL=ROUTES[route][2],
                   JEV_API_KEY='public' if route == 'free' else env['TYPESAFE_API_KEY'])
        python = self.root / '.venv' / 'bin' / 'python'
        command = [str(python) if python.is_file() else sys.executable, '-u', '-B', '-m', 'brief_agent',
                   '--ticker', ticker, '--docs', str(folder), '--as-of', as_of,
                   '--label', job['run_id'], '--arm', 'D']
        process = None
        try:
            with self.lock:
                if self.stopping:
                    raise RuntimeError('Shutdown')
                process = self.popen(command, cwd=self.root, env=env, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, encoding='utf-8', errors='replace', bufsize=1)
                self.process = process
                job['state'] = 'running'
            self.event(job, 'Research process started with ' + ROUTES[route][0] + '.')
            for line in process.stdout:
                if line.strip():
                    self.event(job, line.strip())
            returncode = process.wait()
            try:
                checks = read_artifact(run_folder(self.root, job['run_id']), 'checks.json', required=True)
                status = checks.get('status')
            except (OSError, ValueError, UnicodeError):
                status = None
            with self.lock:
                job['status'] = status if status in {'completed', 'completed_with_drops', 'incomplete', 'verification_unavailable', 'failed'} else None
                success = returncode == 0 and job['status'] in {'completed', 'completed_with_drops'} and not self.stopping
                job['state'] = 'completed' if success else 'failed'
                job['error'] = None if success else ('Run stopped because the workspace shut down.' if self.stopping else
                                                    'Run did not complete successfully. Saved artifacts remain available.')
            self.event(job, 'Run completed.' if success else job['error'])
        except Exception:
            with self.lock:
                job.update(state='failed', error='Research process could not complete. Saved artifacts remain available.')
            self.event(job, job['error'])
        finally:
            self._terminate(process)
            if process is not None and process.stdout is not None:
                process.stdout.close()
            with self.lock:
                if self.process is process:
                    self.process = None

    @staticmethod
    def _terminate(process):
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                pass

    def stop(self):
        with self.lock:
            self.stopping = True
            process, worker = self.process, self.worker
        self._terminate(process)
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=4)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        super().end_headers()

    def _origin(self, post=False):
        port = self.server.server_port
        authorities = {f'127.0.0.1:{port}', f'localhost:{port}'}
        host = self.headers.get('Host')
        if host not in authorities:
            raise RequestError(403, f'Open this workspace at http://127.0.0.1:{port}.')
        origin = self.headers.get('Origin')
        if (post and origin != 'http://' + host) or (origin and origin != 'http://' + host):
            raise RequestError(403, 'Cross-origin requests are not allowed.')

    def _send(self, status, body, content_type='application/json; charset=utf-8', filename=None):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        if filename:
            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)

    def _handle(self, action):
        try:
            action()
        except RequestError as error:
            self._send(error.status, {'error': error.message})
        except FileNotFoundError:
            self._send(404, {'error': 'Saved run or file not found.'})
        except (ValueError, UnicodeError):
            self._send(400, {'error': 'The request or saved data is invalid.'})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self._send(500, {'error': 'The workspace could not complete the request.'})

    def do_GET(self):
        self._handle(self._get)

    def _get(self):
        self._origin()
        path = unquote(urlsplit(self.path).path)
        workspace = self.server.workspace
        if path == '/api/status':
            return self._send(200, workspace.status())
        if path == '/api/runs':
            return self._send(200, {'runs': list_runs(workspace.root)})
        if path.startswith('/api/jobs/'):
            return self._send(200, workspace.job(path.removeprefix('/api/jobs/')))
        if path.startswith('/api/runs/'):
            pieces = path.removeprefix('/api/runs/').split('/')
            run_id = pieces[0]
            if not RUN_ID.fullmatch(run_id):
                raise RequestError(400, 'Invalid run identifier.')
            if len(pieces) == 1:
                return self._send(200, load_run(workspace.root, run_id))
            if len(pieces) == 2 and pieces[1] == 'brief.md':
                folder = run_folder(workspace.root, run_id)
                read_artifact(folder, 'brief.md', required=True)
                return self._send(200, (folder / 'brief.md').read_bytes(), 'text/markdown; charset=utf-8', run_id + '-brief.md')
            if len(pieces) == 2 and pieces[1] == 'audit.json':
                return self._send(200, load_run(workspace.root, run_id), filename=run_id + '-audit.json')
            raise RequestError(404, 'Artifact not available in the workspace.')
        static = {'/': 'index.html', '/index.html': 'index.html', '/app.js': 'app.js', '/app.css': 'app.css',
                  '/static/app.js': 'app.js', '/static/app.css': 'app.css'}
        if path not in static:
            raise RequestError(404, 'Page not found.')
        name = static[path]
        content_type = {'index.html': 'text/html; charset=utf-8', 'app.js': 'text/javascript; charset=utf-8', 'app.css': 'text/css; charset=utf-8'}[name]
        self._send(200, (workspace.root / 'research_ui' / 'static' / name).read_bytes(), content_type)

    def do_POST(self):
        self._handle(self._post)

    def _post(self):
        self._origin(post=True)
        if urlsplit(self.path).path != '/api/runs':
            raise RequestError(404, 'Endpoint not found.')
        if self.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/json':
            raise RequestError(415, 'Send application/json.')
        try:
            length = int(self.headers.get('Content-Length', ''))
        except ValueError:
            raise RequestError(411, 'Content-Length is required.') from None
        if length <= 0 or length > 6_100_000:
            raise RequestError(413, 'Request body is empty or too large.')
        self.connection.settimeout(10)
        request = json.loads(self.rfile.read(length).decode('utf-8'))
        self._send(202, self.server.workspace.start(request))


class ResearchServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, root=ROOT, port=8765, env=None, popen=None):
        self.workspace = Workspace(root, env, popen)
        super().__init__(('127.0.0.1', port), Handler)

    def shutdown(self):
        self.workspace.stop()
        super().shutdown()

    def server_close(self):
        self.workspace.stop()
        super().server_close()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Open the local research workspace.')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('Port must be between 1 and 65535.')
    from brief_agent.cli import load_env
    load_env()
    server = ResearchServer(port=args.port)
    print(f'Research workspace: http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
