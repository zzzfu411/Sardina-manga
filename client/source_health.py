"""Passive, bounded observations. Empty searches are not failed sources."""
from datetime import datetime, timezone
import json
import re
import threading
import time
from client.request_budget import ImageCapacityError

CAPABILITIES = {'search', 'details', 'chapter', 'image', 'coverImage', 'popular', 'latest', 'cover', 'metadata'}


def recent_window(value):
    now = time.time()
    rows = value if isinstance(value, list) else []
    return [item for item in rows[-32:] if isinstance(item, dict)
            and type(item.get('at')) in (float, int) and now - 15 * 60 <= item['at'] <= now
            and isinstance(item.get('status'), str) and item['status'] in {'ok', 'empty', 'limited', 'error'}]


def counter(value):
    return min(100000, max(0, value)) if type(value) is int else 0


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
        except ImageCapacityError:
            raise
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
            recent = recent_window(previous.get('recent'))[-31:]
            recent.append({'at': time.time(), 'status': status})
            row = {**previous, 'status': status, 'checkedAt': now, 'elapsedMs': elapsed,
                   'samples': counter(counter(previous.get('samples')) + 1), 'error': '',
                   'recent': recent, 'windowFailures': sum(item['status'] == 'error' for item in recent),
                   'windowSuccesses': sum(item['status'] in {'ok', 'empty'} for item in recent)}
            if status == 'error':
                row['error'] = re.sub(r'https?://\S+', '[源站地址]', error)[:180]
                row['lastFailure'], row['lastFailureAt'] = row['error'], now
                row['failures'] = counter(counter(previous.get('failures')) + 1)
            elif status in {'ok', 'empty'}:
                row['lastSuccessAt'] = now
                row['successes'] = counter(counter(previous.get('successes')) + 1)
            self.records[site][capability] = row
            self.flush()

    def snapshot(self):
        with self.lock:
            records = json.loads(json.dumps(self.records))
            for capabilities in records.values():
                for row in capabilities.values():
                    row['recent'] = recent_window(row.get('recent'))
                    row['windowFailures'] = sum(item['status'] == 'error' for item in row['recent'])
                    row['windowSuccesses'] = sum(item['status'] in {'ok', 'empty'} for item in row['recent'])
            return records

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
