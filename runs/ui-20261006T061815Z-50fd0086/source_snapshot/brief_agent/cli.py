"""Command line entry and reproducible, credential-free run artifacts."""

import argparse
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

from .clients import JevClient, ProviderError, WriterClient
from .load import load_documents
from .pipeline import run

ROOT = Path(__file__).resolve().parents[1]


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def persisted_calls(calls):
    """Keep provider responses, replacing supplied source requests with hashes."""
    records = []
    for call in calls:
        record = {key: value for key, value in call.items() if key != 'request'}
        if 'request' in call:
            encoded = json.dumps(call['request'], ensure_ascii=False, sort_keys=True,
                                 separators=(',', ':')).encode('utf-8')
            record['request_sha256'] = hashlib.sha256(encoded).hexdigest()
        records.append(record)
    return records


def manifest():
    files = [*ROOT.joinpath('brief_agent').glob('*.py'), *ROOT.joinpath('prompts').glob('*'),
             ROOT / 'requirements.txt']
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(files) if p.is_file()}


def git_info():
    def git(*args):
        result = subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True)
        return result.stdout.strip() if result.returncode == 0 else None
    return {'commit': git('rev-parse', '--verify', 'HEAD'),
            'has_uncommitted_changes': bool(git('status', '--porcelain'))}


def load_env():
    """Accept plain KEY=value .env entries without executing shell code."""
    envfile = ROOT / '.env'
    if not envfile.exists():
        return
    keys = {'WRITER_BASE_URL', 'WRITER_MODEL', 'WRITER_API_KEY', 'GROQ_API_KEY',
            'JEV_URL', 'JEV_MODEL', 'JEV_API_KEY'}
    for line in envfile.read_text().splitlines():
        key, separator, value = line.removeprefix('export ').partition('=')
        if separator and key.strip() in keys:
            os.environ.setdefault(key.strip(), value.strip().strip('\"\''))


def main(argv=None):
    parser = argparse.ArgumentParser(description='Generate a source-checked company research brief.')
    parser.add_argument('--ticker', required=True)
    parser.add_argument('--docs', required=True, type=Path)
    parser.add_argument('--as-of', required=True, type=date.fromisoformat)
    parser.add_argument('--label', required=True)
    parser.add_argument('--arm', choices='ABCD', default='D')
    args = parser.parse_args(argv)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', args.label):
        parser.error('label must be a short name using letters, numbers, dot, underscore or hyphen')
    load_env()
    key = os.getenv('WRITER_API_KEY') or os.getenv('GROQ_API_KEY')
    if not key:
        parser.error('Set WRITER_API_KEY or GROQ_API_KEY before running')
    documents = load_documents(args.docs, args.as_of)
    questions = json.loads((ROOT / 'prompts/jev_questions.json').read_text())
    writer = WriterClient(os.getenv('WRITER_BASE_URL', 'https://api.groq.com/openai/v1'), key,
                          os.getenv('WRITER_MODEL', 'qwen/qwen3.8-27b'))
    jev = JevClient(os.getenv('JEV_URL', 'https://opencode.ai/zen/v1/systemone'),
                    os.getenv('JEV_API_KEY', 'public'), os.getenv('JEV_MODEL', 'jev-1.13-free'))
    folder = ROOT / 'runs' / args.label
    folder.mkdir(parents=True, exist_ok=False)
    config = {'arm': args.arm, 'ticker': args.ticker, 'as_of': args.as_of.isoformat(),
              'writer_requested_model': writer.model, 'jev_requested_model': jev.model if args.arm == 'D' else None,
              'writer_base_url': os.getenv('WRITER_BASE_URL', 'https://api.groq.com/openai/v1'),
              'jev_url': jev.url if args.arm == 'D' else None, 'thresholds': questions['thresholds'],
              'pack_hashes': {d.filename: d.sha256 for d in documents},
              'source_manifest': manifest(), 'git': git_info(), 'manual_edits_to_brief': False,
              'writer_settings': {'temperature': 0.2, 'max_tokens': 2500, 'reasoning_effort': 'minimal' if writer._gemini else 'none'},
              'design': 'C uses the writer for the same triage and claim decisions used by D; B has no semantic decider.'}
    # Preserve the exact runnable implementation while this repository has no commit.
    for relative in config['source_manifest']:
        target = folder / 'source_snapshot' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    write_json(folder / 'config.json', config)
    started = time.monotonic()
    checks, artifacts, exit_code = {}, {}, 0
    try:
        brief, checks, artifacts = run(documents, args.ticker, args.as_of.isoformat(), args.arm, writer,
            writer if args.arm == 'C' else jev if args.arm == 'D' else None,
            (ROOT / 'prompts/system.md').read_text(), questions, (ROOT / 'prompts/naive.md').read_text(),
            audit=checks, artifacts=artifacts, on_progress=lambda message: print(message, flush=True),
            repair_system=(ROOT / 'prompts/repair.md').read_text())
        (folder / 'brief.md').write_text(brief, encoding='utf-8')
        if checks['status'] in {'verification_unavailable', 'incomplete'}:
            exit_code = 2
    except ProviderError as error:
        checks.update(status='failed', error=str(error))
        exit_code = 2
    except Exception as error:
        # Preserve available evidence without putting arbitrary exception text in artifacts.
        checks.update(status='failed', error_type=type(error).__name__)
        exit_code = 2
    finally:
        for name, value in artifacts.items():
            write_json(folder / f'{name}.json', value)
        config['returned_models'] = {
            'writer': sorted({c['model'] for c in writer.calls if c.get('model')}),
            'jev': sorted({c['model'] for c in jev.calls if c.get('model')})}
        write_json(folder / 'config.json', config)
        write_json(folder / 'writer-responses.json', persisted_calls(writer.calls))
        write_json(folder / 'jev-responses.json', persisted_calls(jev.calls))
        write_json(folder / 'checks.json', checks)
        write_json(folder / 'timings-usage.json', {
            'elapsed_s': round(time.monotonic() - started, 3),
            'writer_attempts': len(writer.calls), 'jev_attempts': len(jev.calls),
            'usage': [dict(provider=provider, usage=c['usage']) for provider, records in
                      [('writer', writer.calls), ('jev', jev.calls)] for c in records if c.get('usage')],
            'billed_cost_usd': None, 'cost_note': 'Provider billing not returned. Requested routes are recorded in config.json; no automatic provider fallback was invoked.'})
    print(f"{args.label}: {checks.get('status', 'failed')}; artifacts in runs/{args.label}")
    return exit_code
