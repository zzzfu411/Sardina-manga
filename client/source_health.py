"""Passive, bounded observations. Empty searches are not failed sources."""
from datetime import datetime, timezone
import json
import re
import threading
import time

CAPABILITIES = {'search', 'details', 'chapter', 'image', 'popular', 'latest', 'cover', 'metadata'}


class SourceHealth:
    def __init__(self, path=None):
        self.path, self.lock, self.records, self.saved_at = path, threading.RLock(), {}, 0
        if path:
            try:
                values = json.loads(path.read_text())
                for site, modes in list(values.items())[:50]:
                    if re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', site) and isinstance(modes, dict):
                        self.records[site] = {key: row for key, row in modes.items() if key in CAPABILITIES and isinstance(row, dict) and type(row.get('samples', 0)) is int}
            except (OSError, ValueError, TypeError, AttributeError):
                pass

    def observe(self, site, capability, load):
        start = time.monotonic()
        try:
            value = load()
        except Exception as error:
            self.record(site, capability, 'error', start, str(error))
            raise
        rows = value.get('items', value.get('chapters')) if isinstance(value, dict) else value
        status = 'empty' if capability in {'search', 'popular', 'latest'} and rows == [] else 'ok'
        if isinstance(value, dict) and value.get('unavailableReason'):
            status = 'limited'
        self.record(site, capability, status, start)
        return value

    def record(self, site, capability, status, start, error=''):
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', site) or capability not in CAPABILITIES:
            return
        now = datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
        elapsed = round((time.monotonic() - start) * 1000)
        with self.lock:
            if site not in self.records and len(self.records) >= 50:
                return
            previous = self.records.setdefault(site, {}).get(capability, {})
            row = {**previous, 'status': status, 'checkedAt': now, 'elapsedMs': elapsed,
                   'samples': min(100000, previous.get('samples', 0) + 1), 'error': ''}
            if status == 'error':
                row['error'] = re.sub(r'https?://\S+', '[源站地址]', error)[:180]
            else:
                row['lastSuccessAt'] = now
            self.records[site][capability] = row
            self.flush()

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(self.records))

    def flush(self, force=False):
        if not self.path or not force and time.monotonic() - self.saved_at < 5:
            return
        with self.lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temp = self.path.with_suffix('.tmp')
                temp.write_text(json.dumps(self.records, ensure_ascii=False))
                temp.replace(self.path)
                self.saved_at = time.monotonic()
            except OSError:
                pass  # Health history must not prevent reading.
