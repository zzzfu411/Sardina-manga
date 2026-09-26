from http.client import HTTPResponse
import socket
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from client.request_budget import ImageBudget
from client.source_health import SourceHealth
from server import Application, Handler, SardinaHTTPServer, ROOT


class LocalConnectionTests(unittest.TestCase):
    def test_static_module_burst_survives_accept_loop_pause(self):
        class QuietHandler(Handler):
            def log_message(self, *args):
                pass
        http = SardinaHTTPServer(('127.0.0.1', 0), QuietHandler)
        sockets, serving = [], None
        try:
            # Model the browser opening several module connections while the
            # interpreter briefly cannot schedule its accept loop.
            for index in range(12):
                connection = socket.create_connection(http.server_address, timeout=2)
                sockets.append(connection)
                connection.sendall(f'GET /library-store.js?fixture={index} HTTP/1.1\r\nHost: 127.0.0.1:{http.server_port}\r\nConnection: close\r\n\r\n'.encode())
            serving = threading.Thread(target=http.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
            serving.start()
            expected = (ROOT / 'web/library-store.js').read_bytes()
            for connection in sockets:
                with HTTPResponse(connection) as response:
                    response.begin()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.read(), expected)
        finally:
            for connection in sockets:
                connection.close()
            if serving:
                http.shutdown()
                serving.join(timeout=3)
            http.server_close()


class ChapterRecoveryTests(unittest.TestCase):
    def test_invalid_manifest_is_not_cached_or_recorded_as_success(self):
        body = {'siteId': 'mangabz', 'chapterUrl': 'https://www.mangabz.com/m910026/'}
        valid = ['https://image.mangabz.com/ok.png']
        for invalid in ([], None, ['javascript:bad'], [None], ['https://'], ['https://u:p@image.mangabz.com/x']):
            with self.subTest(invalid=invalid):
                app = Application()
                with patch('server.providers.images', side_effect=[invalid, valid]) as upstream:
                    with self.assertRaises(RuntimeError):
                        app.post('/api/chapter-images', body)
                    self.assertEqual(app.health.snapshot()['mangabz']['chapter']['status'], 'error')
                    self.assertEqual(app.post('/api/chapter-images', body), {'images': valid})
                    self.assertEqual(upstream.call_count, 2)
                    row = app.health.snapshot()['mangabz']['chapter']
                    self.assertEqual(row['status'], 'ok')
                    self.assertEqual(row['windowFailures'], 1)
                    self.assertEqual(row['windowSuccesses'], 1)


class ReadingBudgetTests(unittest.TestCase):
    def test_slow_covers_leave_capacity_for_current_reader(self):
        app, entered, release = Application(), threading.Condition(), threading.Event()
        covers = 0
        def fetch(site, url):
            nonlocal covers
            if 'cover' in url:
                with entered:
                    covers += 1
                    entered.notify_all()
                if not release.wait(3):
                    raise RuntimeError('test did not release cover')
            return b'fixture', 'image/png'
        with patch.object(app, '_image', side_effect=fetch), ThreadPoolExecutor(max_workers=9) as pool:
            jobs = [pool.submit(app.image, 'mangabz', f'https://image.mangabz.com/cover-{i}.png') for i in range(8)]
            try:
                with entered:
                    self.assertTrue(entered.wait_for(lambda: covers == 6, timeout=1))
                current = pool.submit(app.image, 'mangabz', 'https://image.mangabz.com/current.png', purpose='reader')
                self.assertEqual(current.result(1), (b'fixture', 'image/png'))
                self.assertEqual(covers, 6)
            finally:
                release.set()
            for job in jobs:
                job.result(2)

    def test_current_page_promotes_shared_queued_cover_without_duplicate_fetch(self):
        app, release, entered = Application(), threading.Event(), threading.Condition()
        calls = []
        def fetch(site, url):
            with entered:
                calls.append(url)
                entered.notify_all()
            if '/hold-' in url and not release.wait(3):
                raise RuntimeError('test did not release')
            return b'image', 'image/png'
        shared = 'https://image.mangabz.com/shared.png'
        with patch.object(app, '_image', side_effect=fetch), ThreadPoolExecutor(max_workers=8) as pool:
            holds = [pool.submit(app.image, 'mangabz', f'https://image.mangabz.com/hold-{i}.png') for i in range(6)]
            try:
                with entered:
                    self.assertTrue(entered.wait_for(lambda: len(calls) == 6, timeout=1))
                cover = pool.submit(app.image, 'mangabz', shared)
                with app.image_slots.condition:
                    self.assertTrue(app.image_slots.condition.wait_for(lambda: bool(app.image_slots.waiting), timeout=1))
                page = pool.submit(app.image, 'mangabz', shared, purpose='reader')
                self.assertEqual(page.result(1), (b'image', 'image/png'))
                self.assertEqual(cover.result(1), (b'image', 'image/png'))
                self.assertEqual(calls.count(shared), 1)
            finally:
                release.set()
            for job in holds:
                job.result(2)

    def test_background_fifo_survives_new_foreground_requests_and_timeouts(self):
        budget = ImageBudget(limit=3, foreground_reserved=1)
        first = budget.acquire('first')
        second = budget.acquire('second')
        reader = budget.acquire('reader', foreground=True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            background = pool.submit(budget.acquire, 'background', timeout=2)
            with budget.condition:
                self.assertTrue(budget.condition.wait_for(lambda: len(budget.waiting) == 1, timeout=1))
            later = pool.submit(budget.acquire, 'later', foreground=True, timeout=2)
            with budget.condition:
                self.assertTrue(budget.condition.wait_for(lambda: len(budget.waiting) == 2, timeout=1))
            budget.release(first)
            lease = background.result(1)
            self.assertIsNotNone(lease)
            self.assertFalse(later.done())
            budget.release(reader)
            next_lease = later.result(1)
            budget.release(lease)
            budget.release(next_lease)
        extra = budget.acquire('extra')
        self.assertIsNone(budget.acquire('blocked', timeout=.01))
        budget.release(extra)
        budget.release(second)
        self.assertFalse(budget.waiting)
        self.assertFalse(budget.active)


class ReadingHealthTests(unittest.TestCase):
    def test_local_capacity_is_not_a_source_failure(self):
        app = Application()
        with patch.object(app.image_slots, 'acquire', return_value=None), patch.object(app, '_image') as fetch:
            with self.assertRaisesRegex(RuntimeError, '请求较多'):
                app.image('mangabz', 'https://image.mangabz.com/x.png', purpose='reader')
        fetch.assert_not_called()
        self.assertEqual(app.health.snapshot(), {})

    def test_empty_image_is_not_cached_and_retry_recovers(self):
        app = Application()
        with patch.object(app, '_image', side_effect=[(b'', 'image/png'), (b'fixture', 'image/png')]) as fetch:
            with self.assertRaises(RuntimeError):
                app.image('mangabz', 'https://image.mangabz.com/x.png', purpose='reader')
            self.assertEqual(app.image('mangabz', 'https://image.mangabz.com/x.png', purpose='reader')[0], b'fixture')
            self.assertEqual(fetch.call_count, 2)

    def test_damaged_optional_history_does_not_block_successful_reading(self):
        health = SourceHealth()
        health.records = {'mangabz': {'image': {'recent': [{'at': time.time(), 'status': []}], 'samples': 0, 'failures': 'bad'}}}
        self.assertEqual(health.observe('mangabz', 'image', lambda: 'success'), 'success')
        self.assertEqual(health.snapshot()['mangabz']['image']['windowSuccesses'], 1)

    def test_cover_cannot_clear_page_failure_and_success_preserves_recent_failure(self):
        app = Application()
        with patch.object(app, '_image', side_effect=[RuntimeError('blocked page'), (b'cover', 'image/png'), (b'page', 'image/png')]):
            with self.assertRaises(RuntimeError):
                app.image('mangabz', 'https://image.mangabz.com/page.png', purpose='reader')
            app.image('mangabz', 'https://image.mangabz.com/cover.png')
            health = app.health.snapshot()['mangabz']
            self.assertEqual(health['image']['status'], 'error')
            self.assertEqual(health['coverImage']['status'], 'ok')
            app.image('mangabz', 'https://image.mangabz.com/page.png', purpose='reader')
            recovered = app.health.snapshot()['mangabz']['image']
            self.assertEqual(recovered['status'], 'ok')
            self.assertEqual(recovered['windowFailures'], 1)
            self.assertEqual(recovered['lastFailure'], 'blocked page')

    def test_health_window_is_bounded_and_limited_is_not_full_success(self):
        health = SourceHealth()
        health.observe('mangabz', 'details', lambda: {'unavailableReason': 'restricted', 'chapters': []})
        limited = health.snapshot()['mangabz']['details']
        self.assertNotIn('lastSuccessAt', limited)
        for _ in range(40):
            health.observe('mangabz', 'image', lambda: True)
        self.assertEqual(len(health.snapshot()['mangabz']['image']['recent']), 32)

    def test_unknown_purpose_fails_before_work_or_health(self):
        app = Application()
        with patch.object(app, '_image') as fetch, self.assertRaises(ValueError):
            app.image('mangabz', 'https://image.mangabz.com/x.png', purpose='unbounded')
        fetch.assert_not_called()
        self.assertEqual(app.health.snapshot(), {})
