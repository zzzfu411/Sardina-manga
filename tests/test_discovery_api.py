import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import server


class DiscoveryApplicationTests(unittest.TestCase):
    def test_cover_http_route_runs_real_parser_caches_and_explicitly_retries(self):
        http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        http.app = server.Application()
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{http.server_port}'
        body = {'siteId': 'manhuagui', 'detailUrl': 'https://www.manhuagui.com/comic/43661/'}
        source = (Path(__file__).parent / 'fixtures/discovery/manhuagui-book-cover.html').read_text()
        changed = source.replace('/cpic/h/43661.jpg', '/cpic/h/43661-new.jpg')
        missing = source.replace('class="book-cover fl"', 'class="missing"')
        def post(value):
            request = Request(base + '/api/discovery/cover', data=json.dumps(value).encode(),
                              headers={'Content-Type': 'application/json'})
            with urlopen(request, timeout=3) as response:
                return json.load(response)['data']
        try:
            with patch.object(server.discovery, '_download', side_effect=[source, changed, missing]) as download, \
                    patch.object(server.providers, 'details') as details:
                first = post(body)
                self.assertEqual(first, {**body, 'coverUrl': 'https://cf.mhgui.com/cpic/h/43661.jpg'})
                self.assertEqual(post({**body, 'detailUrl': body['detailUrl'].rstrip('/')}), first)
                refreshed = post({**body, 'refresh': True})
                self.assertEqual(refreshed['coverUrl'], 'https://cf.mhgui.com/cpic/h/43661-new.jpg')
                self.assertEqual(post(body), refreshed)
                self.assertEqual(download.call_count, 2)
                for invalid in [{**body, 'refresh': 1}, {**body, 'siteId': 'manben'},
                                {**body, 'detailUrl': 'https://www.manhuagui.com/comic/43661/910038.html'},
                                {**body, 'url': 'https://other.test'}, []]:
                    with self.subTest(body=invalid), self.assertRaises(HTTPError) as error:
                        post(invalid)
                    self.assertEqual(error.exception.code, 400)
                with self.assertRaises(HTTPError) as error:
                    post({**body, 'refresh': True})
                self.assertEqual(error.exception.code, 502)
                self.assertIn('封面', json.load(error.exception)['error'])
                # A failed refresh reports the failure but cannot poison the
                # last successful cached cover used on the next normal visit.
                self.assertEqual(post(body), refreshed)
                self.assertEqual(download.call_count, 3)
                details.assert_not_called()
            with urlopen(base + '/discovery-covers.js') as response:
                self.assertIn('javascript', response.headers.get_content_type())
        finally:
            http.shutdown(); http.server_close(); thread.join()

    def test_cover_cache_is_separate_bounded_and_expires_after_six_hours(self):
        app = server.Application()
        app.cover_cache.limit = 2
        body = {'siteId': 'manhuagui', 'detailUrl': 'https://www.manhuagui.com/comic/1/'}
        stamp = 1000
        with patch.object(server.time, 'monotonic', side_effect=lambda: stamp), \
                patch.object(server.discovery, 'book_cover', side_effect=lambda site, url: {'siteId': site, 'detailUrl': url, 'coverUrl': 'cover'}) as fetch:
            app.cache.get('unrelated-search', 100000, lambda: 'unchanged')
            app.post('/api/discovery/cover', body)
            stamp += 6 * 3600 - 1
            app.post('/api/discovery/cover', body)
            self.assertEqual(fetch.call_count, 1)
            stamp += 1
            app.post('/api/discovery/cover', body)
            self.assertEqual(fetch.call_count, 2)
            for number in (2, 3):
                app.post('/api/discovery/cover', {**body, 'detailUrl': f'https://www.manhuagui.com/comic/{number}/'})
            self.assertEqual(len(app.cover_cache.data), 2)
            app.post('/api/discovery/cover', body)
            self.assertEqual(fetch.call_count, 5)
            self.assertEqual(app.cache.get('unrelated-search', 100000, lambda: 'wrong'), 'unchanged')

    def test_cover_errors_are_not_cached_release_slots_and_reject_compatibility(self):
        body = {'siteId': 'manben', 'detailUrl': 'https://www.manben.com/mh-yaoshenji/'}
        app = server.Application()
        with patch.object(server.discovery, 'book_cover', side_effect=[server.discovery.DiscoveryError('无有效封面'), {'ok': True}]) as fetch:
            with self.assertRaisesRegex(server.discovery.DiscoveryError, '封面'):
                app.post('/api/discovery/cover', body)
            self.assertEqual(len(app.cover_cache.data), 0)
            acquired = [app.cover_slots.acquire(blocking=False) for _ in range(5)]
            try:
                self.assertEqual(acquired, [True, True, True, True, False])
            finally:
                for success in acquired:
                    if success:
                        app.cover_slots.release()
            self.assertEqual(app.post('/api/discovery/cover', body), {'ok': True})
            self.assertEqual(fetch.call_count, 2)
        with patch.object(app.cover_slots, 'acquire', return_value=False), patch.object(server.discovery, 'book_cover') as fetch:
            with self.assertRaisesRegex(RuntimeError, '稍后重试'):
                app.post('/api/discovery/cover', {**body, 'refresh': True})
            fetch.assert_not_called()
        with patch.object(server.discovery, 'book_cover') as fetch:
            with self.assertRaisesRegex(ValueError, '兼容模式'):
                server.Application('mangayun').post('/api/discovery/cover', body)
            fetch.assert_not_called()

    def test_cover_requests_allow_only_four_concurrent_source_fetches(self):
        app = server.Application()
        condition, release = threading.Condition(), threading.Event()
        active = peak = 0
        # Distinct cache stripes let this test reach the source concurrency
        # limit, independently of accidental hash collisions in the cache.
        urls, stripes = [], set()
        for number in range(1, 1000):
            url = f'https://www.manhuagui.com/comic/{number}/'
            stripe = hash(('manhuagui', url)) % len(app.cover_cache.stripes)
            if stripe not in stripes:
                urls.append(url); stripes.add(stripe)
            if len(urls) == 8:
                break
        self.assertEqual(len(urls), 8)
        def load(site, url):
            nonlocal active, peak
            with condition:
                active += 1
                peak = max(peak, active)
                condition.notify_all()
            try:
                if not release.wait(3):
                    raise RuntimeError('test release timed out')
                return {'siteId': site, 'detailUrl': url, 'coverUrl': 'cover'}
            finally:
                with condition:
                    active -= 1
                    condition.notify_all()
        with patch.object(server.discovery, 'book_cover', side_effect=load) as fetch, ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(app.post, '/api/discovery/cover', {'siteId': 'manhuagui', 'detailUrl': url}) for url in urls]
            try:
                with condition:
                    self.assertTrue(condition.wait_for(lambda: active >= 4, timeout=2))
                    self.assertEqual(active, 4)
                self.assertTrue(all(not future.done() for future in futures))
            finally:
                release.set()
            self.assertEqual([future.result(timeout=3)['detailUrl'] for future in futures], urls)
            self.assertEqual(peak, 4)
            self.assertEqual(fetch.call_count, 8)

    def test_recommendations_get_caches_refreshes_and_rejects_unexpected_parameters(self):
        http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        http.app = server.Application()
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{http.server_port}/api/recommendations'
        try:
            with patch.object(server.recommendations, 'fetch', side_effect=[{'items': ['first']}, {'items': ['new']}]) as fetch:
                for suffix, expected in [('', 'first'), ('', 'first'), ('?refresh=1', 'new'), ('', 'new')]:
                    with urlopen(base + suffix) as response:
                        self.assertEqual(json.load(response)['data']['items'], [expected])
                self.assertEqual(fetch.call_count, 2)
                for suffix in ['?refresh=true', '?refresh=', '?refresh=1&refresh=1', '?url=https://other.test']:
                    with self.subTest(suffix=suffix), self.assertRaises(HTTPError) as error:
                        urlopen(base + suffix)
                    self.assertEqual(error.exception.code, 400)
                self.assertEqual(fetch.call_count, 2)
        finally:
            http.shutdown(); http.server_close(); thread.join()

    def test_recommendations_do_not_mix_native_items_into_compatibility_mode(self):
        app = server.Application('mangayun')
        with patch.object(server.recommendations, 'fetch') as fetch:
            with self.assertRaisesRegex(ValueError, '兼容模式'):
                app.recommendations()
            fetch.assert_not_called()

    def test_explicit_refresh_replaces_success_but_failed_refresh_keeps_good_cache(self):
        cache = server.Cache()
        self.assertEqual(cache.get('key', 300, lambda: 'first'), 'first')
        self.assertEqual(cache.get('key', 300, lambda: 'ignored'), 'first')
        self.assertEqual(cache.get('key', 300, lambda: 'fresh', refresh=True), 'fresh')
        def failure():
            raise RuntimeError('upstream unavailable')
        with self.assertRaises(RuntimeError):
            cache.get('key', 300, failure, refresh=True)
        self.assertEqual(cache.get('key', 300, lambda: 'unexpected'), 'fresh')

    def test_discovery_caches_by_normalized_selection_and_refreshes_selected_list(self):
        app = server.Application()
        request = {'siteId': 'manhuagui', 'kind': 'popular', 'period': 'day', 'page': 1}
        with patch.object(server.discovery, 'normalize_request', return_value=('manhuagui', 'popular', 'day', 1)), \
                patch.object(server.discovery, 'fetch', side_effect=[{'fetchedAt': 'first'}, {'fetchedAt': 'second'}]) as fetch:
            self.assertEqual(app.post('/api/discovery', request)['fetchedAt'], 'first')
            self.assertEqual(app.post('/api/discovery', request)['fetchedAt'], 'first')
            self.assertEqual(app.post('/api/discovery', {**request, 'refresh': True})['fetchedAt'], 'second')
            self.assertEqual(fetch.call_count, 2)
            fetch.assert_called_with('manhuagui', 'popular', 'day', 1)

    def test_refresh_requires_boolean_before_transport(self):
        app = server.Application()
        with patch.object(server.discovery, 'normalize_request', return_value=('manhuagui', 'popular', 'day', 1)), \
                patch.object(server.discovery, 'fetch') as fetch:
            with self.assertRaisesRegex(ValueError, '刷新'):
                app.post('/api/discovery', {'refresh': 'true'})
            fetch.assert_not_called()
        with patch.object(server.providers, 'details') as details:
            with self.assertRaisesRegex(ValueError, '刷新'):
                app.post('/api/details', {'siteId': 'manhuagui', 'detailUrl': 'https://www.manhuagui.com/comic/123/', 'refresh': 1})
            details.assert_not_called()

    def test_update_check_can_fetch_new_directory_without_changing_search_registry(self):
        app = server.Application()
        body = {'siteId': 'manhuagui', 'detailUrl': 'https://www.manhuagui.com/comic/123/'}
        with patch.object(server.providers, 'details', side_effect=[{'chapters': [1]}, {'chapters': [1, 2]}]) as details:
            self.assertEqual(app.post('/api/details', body)['chapters'], [1])
            self.assertEqual(app.post('/api/details', body)['chapters'], [1])
            self.assertEqual(app.post('/api/details', {**body, 'refresh': True})['chapters'], [1, 2])
            self.assertEqual(details.call_count, 2)
        self.assertEqual(app.sites(), server.providers.sites())

    def test_compatibility_mode_does_not_mix_native_discovery_links(self):
        app = server.Application('mangayun')
        with patch.object(server.discovery, 'fetch') as fetch, patch.object(server.discovery, 'sources') as sources:
            self.assertEqual(app.discovery_sources(), [])
            with self.assertRaisesRegex(ValueError, '兼容模式'):
                app.post('/api/discovery', {'siteId': 'manhuagui'})
            fetch.assert_not_called()
            sources.assert_not_called()

    def test_discovery_capabilities_http_endpoint(self):
        expected = [{'siteId': 'manhuagui', 'siteName': '漫画柜', 'modes': []}]
        http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        http.app = server.Application()
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(server.discovery, 'sources', return_value=expected), \
                    urlopen(f'http://127.0.0.1:{http.server_port}/api/discovery/sources') as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(json.load(response), {'data': expected})
        finally:
            http.shutdown()
            http.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
