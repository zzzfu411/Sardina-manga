from concurrent.futures import ThreadPoolExecutor
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error

from client import dm5_family as dm, native_sources as native
from server import Cache


class TrustedParsingTests(unittest.TestCase):
    def test_unknown_eval_cannot_reach_a_process(self):
        payload = 'eval((()=>require("node:fs").writeFileSync("should-not-exist", "x"))())'
        with patch('subprocess.check_output', side_effect=AssertionError('remote code executed')) as execute:
            with self.assertRaisesRegex(dm.Dm5FamilyError, '格式'):
                dm.unpack(payload)
            execute.assert_not_called()

    def test_known_packer_is_decoded_as_data(self):
        source = "eval(function(p,a,c,k,e,d){return p;}('0 1=\"2\";',3,3,'var|pix|https://image.mangabz.com/'.split('|'),0,{}))"
        self.assertEqual(dm.unpack(source), 'var pix="https://image.mangabz.com/";')
        with self.assertRaises(dm.Dm5FamilyError):
            dm.unpack(source.replace(',3,3,', ',3,999999999,'))
        # The output budget includes separators, not only decoded word tokens.
        expanded = "eval(function(p,a,c,k,e,d){return p;}('" + '0 ' * 2048 + "',2,1,'" + 'x' * 4096 + "'.split('|'),0,{}))"
        with self.assertRaises(dm.Dm5FamilyError):
            dm.unpack(expanded)

    def test_certificate_failure_does_not_retry_unverified(self):
        failure = urllib.error.URLError('CERTIFICATE_VERIFY_FAILED')
        with patch.object(native.urllib.request, 'urlopen', side_effect=failure) as request:
            with self.assertRaisesRegex(urllib.error.URLError, '证书'):
                native._urlopen('https://www.mangabz.com/', 1)
            self.assertEqual(request.call_count, 1)
        session = dm.Session()
        original = session.opener
        with patch.object(original, 'open', side_effect=failure) as request:
            with self.assertRaisesRegex(dm.Dm5FamilyError, '证书'):
                session.get('https://www.mangabz.com/')
            self.assertEqual(request.call_count, 1)
            self.assertIs(session.opener, original)


class CacheIsolationTests(unittest.TestCase):
    def test_hot_and_unrelated_cold_keys_do_not_wait_for_slow_key(self):
        cache, entered, release = Cache(), threading.Event(), threading.Event()
        cache.get(64, 60, lambda: 'cached')
        def slow():
            entered.set()
            release.wait(2)
            return 'slow'
        with ThreadPoolExecutor(max_workers=3) as pool:
            waiting = pool.submit(cache.get, 0, 60, slow)
            self.assertTrue(entered.wait(1))
            try:
                self.assertEqual(pool.submit(cache.get, 64, 60, lambda: 'wrong').result(.3), 'cached')
                self.assertEqual(pool.submit(cache.get, 128, 60, lambda: 'fresh').result(.3), 'fresh')
            finally:
                release.set()
            self.assertEqual(waiting.result(), 'slow')

    def test_failed_load_is_shared_and_then_retryable(self):
        cache, calls = Cache(), []
        def fail():
            calls.append(1)
            time.sleep(.05)
            raise ValueError('upstream')
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(cache.get, 'same', 60, fail) for _ in range(4)]
            for future in futures:
                with self.assertRaises(ValueError):
                    future.result()
        self.assertEqual(len(calls), 1)
        self.assertEqual(cache.get('same', 60, lambda: 'recovered'), 'recovered')
        self.assertFalse(cache.pending)

    def test_image_cache_is_byte_bounded(self):
        cache = Cache(max_bytes=5)
        cache.get('one', 60, lambda: (b'1234', 'image/png'))
        cache.get('two', 60, lambda: (b'5678', 'image/png'))
        self.assertEqual(cache.bytes, 4)
        self.assertNotIn('one', cache.data)


class ApplicationBudgetTests(unittest.TestCase):
    def test_image_budget_is_shared_across_distinct_client_requests(self):
        from server import Application
        app, entered, release = Application(), threading.Condition(), threading.Event()
        active = peak = 0
        def fetch(site, url):
            nonlocal active, peak
            with entered:
                active += 1; peak = max(peak, active); entered.notify_all()
            try:
                if not release.wait(3):
                    raise RuntimeError('test release timed out')
                return b'fixture', 'image/png'
            finally:
                with entered:
                    active -= 1
        with patch.object(app, '_image', side_effect=fetch), ThreadPoolExecutor(max_workers=12) as pool:
            jobs = [pool.submit(app.image, 'mangabz', f'https://image.mangabz.com/{i}.png') for i in range(12)]
            try:
                with entered:
                    self.assertTrue(entered.wait_for(lambda: active == 8, timeout=2))
                    self.assertEqual(peak, 8)
            finally:
                release.set()
            self.assertTrue(all(job.result(timeout=3) == (b'fixture', 'image/png') for job in jobs))
            self.assertEqual(peak, 8)

    def test_metadata_does_not_fetch_chapters_and_is_cached(self):
        from client import providers
        from server import Application
        from client import komiic
        with patch.object(komiic, '_query', return_value={'comicById': {'id': '42', 'title': '样本', 'authors': [{'name': '甲'}], 'description': '日常生活'}}) as query:
            result = providers.metadata('komiic', 'https://komiic.com/comic/42')
            self.assertEqual(result['author'], '甲')
            self.assertNotIn('chapters', result)
            self.assertNotIn('chaptersByComicId', query.call_args.args[0])
        app = Application()
        with patch.object(providers, 'metadata', return_value=result) as fetch:
            body = {'siteId': 'komiic', 'detailUrl': 'https://komiic.com/comic/42'}
            self.assertEqual(app.post('/api/book-metadata', body), result)
            self.assertEqual(app.post('/api/book-metadata', body), result)
            fetch.assert_called_once()
            with self.assertRaises(ValueError):
                app.post('/api/book-metadata', {**body, 'detailUrl': 'http://127.0.0.1/private'})
            fetch.assert_called_once()


class SourceMigrationTests(unittest.TestCase):
    def test_coco_migration_preserves_book_and_chapter_identity(self):
        from client import apk_vomic_html as source
        old = 'https://www.cocoecar.com/comic/12686'
        new = 'https://keke2026.com/comic/12686'
        self.assertEqual(source._book_url('cocoecar', new), old)
        self.assertEqual(source._network_url('cocoecar', old), new)
        self.assertEqual(source._check_redirect('cocoecar', old, new), old)
        self.assertEqual(source._chapter_url('cocoecar', 'https://keke2026.com/chapter/200'), 'https://www.cocoecar.com/chapter/200')
        for target in ['https://keke2026.com/comic/2', 'https://keke2026.com/', 'https://keke2026.com.evil.test/comic/12686']:
            with self.subTest(target=target), self.assertRaises((ValueError, RuntimeError)):
                source._check_redirect('cocoecar', old, target)

    def test_health_is_passive_empty_is_successful_and_errors_redact_ticket_urls(self):
        from client.source_health import SourceHealth
        health = SourceHealth()
        self.assertEqual(health.snapshot(), {})
        health.observe('mangabz', 'search', lambda: [])
        self.assertEqual(health.snapshot()['mangabz']['search']['status'], 'empty')
        def failure():
            raise RuntimeError('https://image.mangabz.com/p.png?key=private failed')
        with self.assertRaises(RuntimeError):
            health.observe('mangabz', 'image', failure)
        self.assertNotIn('private', health.snapshot()['mangabz']['image']['error'])
        health.observe('mangabz', 'image', lambda: (b'image','image/png'))
        self.assertEqual(health.snapshot()['mangabz']['image']['status'], 'ok')
