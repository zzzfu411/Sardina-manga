#!/usr/bin/env python3
"""Offline checks with isolated Chromium and persistent failure evidence."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import threading
import uuid

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ('downloads', 'reader-search', 'performance', 'reaudit', 'search-grouping', 'search-titles', 'reader-touch')
DIAGNOSTIC_MARKER = 'SARDINA_CHECK_DIAGNOSTICS:'


class CheckRun:
    def __init__(self, output):
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.summary = {'status': 'running', 'startedAt': datetime.now(timezone.utc).isoformat()}
        self.save()

    def save(self):
        target = self.output / 'summary.json'
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.summary, ensure_ascii=False, indent=2) + '\n')
        temporary.replace(target)

    def finish(self, error=None):
        self.summary['status'] = 'failed' if error else 'passed'
        self.summary['finishedAt'] = datetime.now(timezone.utc).isoformat()
        if error:
            self.summary['error'] = str(error)
        self.save()

    def diagnostics(self, label, stdout):
        if DIAGNOSTIC_MARKER not in stdout:
            return
        raw = stdout.split(DIAGNOSTIC_MARKER, 1)[1]
        try:
            value, _ = json.JSONDecoder().raw_decode(raw)
        except ValueError:
            return
        for index, page in enumerate(value.get('pages', [])):
            html = page.pop('html', None)
            if html is not None:
                name = f'{label}-failure-{index}.html'
                (self.output / name).write_text(html)
                page['domFile'] = name
        name = label + '-failure.json'
        (self.output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
        self.summary[label]['diagnostics'] = name
        self.summary[label]['stage'] = value.get('stage')

    def run(self, label, command, timeout=180, *, tool_output=False):
        print(f'Checking {label}…', flush=True)
        try:
            result = subprocess.run(command, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
            stdout, code = result.stdout, result.returncode
        except subprocess.TimeoutExpired as error:
            stdout = error.stdout or ''
            if isinstance(stdout, bytes):
                stdout = stdout.decode(errors='replace')
            stdout += f'\nCheck process exceeded {timeout} seconds.\n'
            code = -1
        except OSError as error:
            stdout, code = str(error), -1
        (self.output / (label + '.log')).write_text(stdout)
        if code or tool_output and '### Error' in stdout:
            self.summary[label] = {'status': 'failed', 'returncode': code, 'log': label + '.log'}
            self.diagnostics(label, stdout)
            self.save()
            print(stdout[-6000:], file=sys.stderr)
            raise RuntimeError(f'{label} failed; see {self.output / (label + ".log")}')
        self.summary[label] = 'passed'
        if '### Result\n' in stdout:
            raw = stdout.split('### Result\n', 1)[1].split('\n### ', 1)[0].strip()
            try:
                value = json.loads(raw)
                self.summary[label] = value
                (self.output / (label + '.json')).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
            except ValueError:
                pass
        self.save()
        print(f'{label}: passed', flush=True)
        return stdout


def instrument_browser(source, name, output):
    """The fixture names meaningful stages through its second argument."""
    directory = json.dumps(str(output))
    label = json.dumps(name)
    marker = json.dumps(DIAGNOSTIC_MARKER)
    return '''async driverPage => {
  // Each scenario owns its cookies, storage, routes and init scripts. Reusing
  // the CLI's page lets earlier API mocks silently change later checks.
  const context=await driverPage.context().browser().newContext();
  const page=await context.newPage();
  const directory=__DIRECTORY__, name=__LABEL__;
  const diagnostic={scenario:name,stage:'setup',stages:[],console:[],requestFailures:[],pageErrors:[],pages:[]};
  const step=value=>{diagnostic.stage=value;diagnostic.stages.push({name:value,at:Date.now()});};
  const listeners=[];
  function attach(p){
    const events={console:msg=>{if(diagnostic.console.length<200)diagnostic.console.push({type:msg.type(),text:msg.text()});},
      requestfailed:request=>{if(diagnostic.requestFailures.length<200)diagnostic.requestFailures.push({url:request.url(),error:request.failure()?.errorText,stage:diagnostic.stage});},
      pageerror:error=>diagnostic.pageErrors.push(error.message)};
    for(const [event,handler] of Object.entries(events)){p.on(event,handler);listeners.push([p,event,handler]);}
  }
  for(const p of context.pages())attach(p);context.on('page',attach);
  let traced=false;
  try{await context.tracing.start({screenshots:true,snapshots:true,sources:true});traced=true;}catch(error){diagnostic.traceError=String(error);}
  const scenario=(__SOURCE__);
  try{return await scenario(page,step);}
  catch(error){
    diagnostic.error=String(error);
    for(const [index,p] of context.pages().entries()){
      const entry={url:p.url()};diagnostic.pages.push(entry);
      try{entry.html=(await p.content()).slice(0,200000);}catch(cause){entry.domError=String(cause);}
      try{entry.screenshot=name+'-failure-'+index+'.png';await p.screenshot({path:directory+'/'+entry.screenshot,timeout:5000});}catch(cause){entry.screenshotError=String(cause);}
    }
    if(traced){try{await context.tracing.stop({path:directory+'/'+name+'-failure-trace.zip'});diagnostic.trace=name+'-failure-trace.zip';}catch(cause){diagnostic.traceError=String(cause);}traced=false;}
    throw new Error(__MARKER__+JSON.stringify(diagnostic));
  }finally{
    if(traced)await context.tracing.stop().catch(()=>{});
    context.off('page',attach);for(const [p,event,handler] of listeners)p.off(event,handler);
    await context.close();
  }
}
'''.replace('__DIRECTORY__', directory).replace('__LABEL__', label).replace('__MARKER__', marker).replace('__SOURCE__', source.strip().rstrip(';'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', action='store_true')
    parser.add_argument('--browser-only', action='store_true')
    parser.add_argument('--scenario', action='append', choices=(*SCENARIOS, 'diagnostic-failure'), help='run selected browser scenarios; diagnostic-failure intentionally fails')
    parser.add_argument('--output', type=Path, default=ROOT / 'output' / 'checks' / datetime.now().strftime('%Y%m%d-%H%M%S'))
    args = parser.parse_args()
    output = args.output.resolve()
    checks = CheckRun(output)
    failure = None
    try:
        if not args.browser_only:
            python = str(ROOT / '.venv/bin/python') if (ROOT / '.venv/bin/python').exists() else sys.executable
            checks.run('python', [python, '-m', 'unittest', 'discover', '-s', 'tests'])
            checks.run('javascript', ['node', '--test', *[str(path) for path in sorted((ROOT / 'tests').glob('*.test.js'))]])
            for path in sorted((ROOT / 'web').glob('*.js')):
                result = subprocess.run(['node', '--check', str(path)], cwd=ROOT, capture_output=True, text=True)
                if result.returncode:
                    checks.summary['syntax'] = {'status': 'failed', 'file': str(path), 'error': result.stderr}
                    raise RuntimeError(result.stderr)
            checks.summary['syntax'] = 'passed'
            checks.save()
        if args.browser or args.browser_only or args.scenario:
            supplied = os.environ.get('PLAYWRIGHT_CLI')
            wrapper = Path.home() / '.codex/skills/playwright/scripts/playwright_cli.sh'
            cli = shlex.split(supplied) if supplied else [shutil.which('playwright-cli')] if shutil.which('playwright-cli') else [str(wrapper)] if wrapper.exists() else None
            if not cli:
                raise RuntimeError('Install @playwright/cli@0.1.21 and Chrome, or set PLAYWRIGHT_CLI.')
            sys.path.insert(0, str(ROOT))
            from server import Application, Handler, SardinaHTTPServer
            http = SardinaHTTPServer(('127.0.0.1', 0), Handler)
            http.daemon_threads = True
            http.app = Application()
            thread = threading.Thread(target=http.serve_forever, daemon=True)
            thread.start()
            base = f'http://127.0.0.1:{http.server_port}'
            browser = [*cli, '-s=sardina-check-' + uuid.uuid4().hex[:10]]
            try:
                checks.run('browser-open', [*browser, 'open', 'about:blank'], tool_output=True)
                for name in args.scenario or SCENARIOS:
                    source = (ROOT / 'tests/browser' / (name + '.js')).read_text()
                    for key, value in {'__BASE_URL__': base, '__ROOT__': str(ROOT), '__OUTPUT_DIR__': str(output)}.items():
                        source = source.replace(key, value.replace('\\', '\\\\').replace("'", "\\'"))
                    script = output / (name + '.js')
                    script.write_text(instrument_browser(source, name, output))
                    checks.run(name, [*browser, 'run-code', '--filename', str(script)], tool_output=True)
            finally:
                try:
                    subprocess.run([*browser, 'close'], cwd=ROOT, capture_output=True, timeout=20)
                except subprocess.SubprocessError:
                    pass
                http.shutdown()
                http.server_close()
                thread.join(timeout=5)
    except BaseException as error:
        failure = error
        raise
    finally:
        checks.finish(failure)
    print(f'All checks passed. Evidence: {output}', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        print(error, file=sys.stderr)
        raise SystemExit(1)
