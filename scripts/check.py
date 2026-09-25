#!/usr/bin/env python3
"""Repeatable offline checks, optionally with real Chromium/IndexedDB scenarios.

Uses an ephemeral localhost port and an in-memory browser profile. No requests
are sent to manga sites and the user's running Sardina service is untouched.
"""
from __future__ import annotations
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import threading
import uuid

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', action='store_true', help='include real browser and IndexedDB checks')
    parser.add_argument('--browser-only', action='store_true')
    parser.add_argument('--output', type=Path, default=ROOT / 'output' / 'checks' / datetime.now().strftime('%Y%m%d-%H%M%S'))
    args = parser.parse_args()
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=True)
    summary = {}

    def run(label, command, timeout=180):
        print(f'Checking {label}…', flush=True)
        result = subprocess.run(command, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        (output / (label + '.log')).write_text(result.stdout)
        if result.returncode or '### Error' in result.stdout:
            print(result.stdout[-6000:], file=sys.stderr)
            raise RuntimeError(f'{label} failed; see {output / (label + ".log")}')
        summary[label] = 'passed'
        if '### Result\n' in result.stdout:
            raw = result.stdout.split('### Result\n', 1)[1].split('\n### ', 1)[0].strip()
            try:
                value = json.loads(raw); summary[label] = value
                (output / (label + '.json')).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
            except ValueError:
                pass
        print(f'{label}: passed', flush=True)
        return result.stdout

    if not args.browser_only:
        python = str(ROOT / '.venv/bin/python') if (ROOT / '.venv/bin/python').exists() else sys.executable
        run('python', [python, '-m', 'unittest', 'discover', '-s', 'tests'])
        run('javascript', ['node', '--test', *[str(path) for path in sorted((ROOT / 'tests').glob('*.test.js'))]])
        for path in sorted((ROOT / 'web').glob('*.js')):
            result = subprocess.run(['node', '--check', str(path)], cwd=ROOT, capture_output=True, text=True)
            if result.returncode:
                raise RuntimeError(result.stderr)
        summary['syntax'] = 'passed'

    if args.browser or args.browser_only:
        supplied = os.environ.get('PLAYWRIGHT_CLI')
        wrapper = Path.home() / '.codex/skills/playwright/scripts/playwright_cli.sh'
        cli = shlex.split(supplied) if supplied else [shutil.which('playwright-cli')] if shutil.which('playwright-cli') else [str(wrapper)] if wrapper.exists() else None
        if not cli:
            raise RuntimeError('Install @playwright/cli@0.1.21 and Chrome, or set PLAYWRIGHT_CLI to an existing CLI wrapper.')
        sys.path.insert(0, str(ROOT))
        from http.server import ThreadingHTTPServer
        from server import Application, Handler
        http = ThreadingHTTPServer(('127.0.0.1', 0), Handler); http.daemon_threads = True; http.app = Application()
        thread = threading.Thread(target=http.serve_forever, daemon=True); thread.start()
        base = f'http://127.0.0.1:{http.server_port}'
        session = 'sardina-check-' + uuid.uuid4().hex[:10]
        browser = [*cli, '-s=' + session]
        try:
            run('browser-open', [*browser, 'open', 'about:blank'])
            for name in ('downloads', 'reader-search', 'performance'):
                source = (ROOT / 'tests/browser' / (name + '.js')).read_text()
                for key, value in {'__BASE_URL__': base, '__ROOT__': str(ROOT), '__OUTPUT_DIR__': str(output)}.items():
                    source = source.replace(key, value.replace('\\', '\\\\').replace("'", "\\'"))
                script = output / (name + '.js'); script.write_text(source)
                run(name, [*browser, 'run-code', '--filename', str(script)])
        finally:
            subprocess.run([*browser, 'close'], cwd=ROOT, capture_output=True, timeout=20)
            http.shutdown(); http.server_close(); thread.join(timeout=5)
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    print(f'All checks passed. Evidence: {output}', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        print(error, file=sys.stderr)
        raise SystemExit(1)
