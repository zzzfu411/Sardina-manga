import argparse
import http.client
import json
import threading
import unittest
from unittest.mock import patch

from server import Application, Handler, SardinaHTTPServer, public_origin


class PublicDeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http = SardinaHTTPServer(('127.0.0.1', 0), Handler)
        cls.http.app = Application()
        cls.http.public_origin = public_origin('https://manga.example.com')
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join()

    def request(self, method='GET', headers=()):
        connection = http.client.HTTPConnection('127.0.0.1', self.http.server_port, timeout=5)
        try:
            connection.putrequest(method, '/api/config' if method == 'GET' else '/api/search', skip_host=True)
            for name, value in headers:
                connection.putheader(name, value)
            body = b'{}' if method == 'POST' else None
            if body:
                connection.putheader('Content-Length', str(len(body)))
                connection.putheader('Content-Type', 'application/json')
            connection.endheaders(body)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_https_host_and_origin_work_through_plain_http_upstream(self):
        headers = [('Host', 'manga.example.com'), ('Origin', 'https://manga.example.com')]
        with patch.object(self.http.app, 'post', return_value=['ok']) as post:
            self.assertEqual(self.request('POST', headers), (200, {'data': ['ok']}))
            post.assert_called_once_with('/api/search', {})
        for host in ('manga.example.com', 'manga.example.com:443', f'127.0.0.1:{self.http.server_port}'):
            with self.subTest(host=host):
                self.assertEqual(self.request(headers=[('Host', host)])[0], 200)

    def test_cross_origin_and_forwarded_headers_do_not_grant_access(self):
        cases = [
            [('Host', 'manga.example.com'), ('Origin', 'http://manga.example.com')],
            [('Host', 'manga.example.com'), ('Origin', 'https://evil.example.com')],
            [('Host', 'manga.example.com'), ('Origin', 'null')],
            [('Host', 'manga.example.com'), ('Origin', 'https://manga.example.com'), ('Origin', 'https://evil.test')],
            [('Host', 'evil.test'), ('X-Forwarded-Host', 'manga.example.com'), ('X-Forwarded-Proto', 'https')],
            [('Host', '127.0.0.1'), ('Origin', 'https://manga.example.com'), ('X-Forwarded-Proto', 'https')],
        ]
        with patch.object(self.http.app, 'post') as post:
            for headers in cases:
                with self.subTest(headers=headers):
                    self.assertEqual(self.request('POST', headers)[0], 403)
            post.assert_not_called()

    def test_missing_duplicate_malformed_or_foreign_host_is_rejected(self):
        cases = [[], [('Host', 'manga.example.com'), ('Host', 'evil.test')]]
        cases += [[('Host', host)] for host in (
            'manga.example.com.evil.test', 'manga.example.com:8443', 'user@localhost',
            'localhost/path', 'localhost?query', '[invalid', 'localhost:invalid', 'localhost:0',
        )]
        for headers in cases:
            with self.subTest(headers=headers):
                self.assertEqual(self.request(headers=headers)[0], 403)

    def test_default_remains_local_only(self):
        with patch.object(self.http, 'public_origin', None):
            self.assertEqual(self.request(headers=[('Host', 'manga.example.com')])[0], 403)
            self.assertEqual(self.request(headers=[('Host', 'localhost')])[0], 200)

    def test_nonstandard_public_port_is_matched_exactly(self):
        with patch.object(self.http, 'public_origin', public_origin('https://manga.example.com:8443')):
            self.assertEqual(self.request(headers=[('Host', 'manga.example.com:8443')])[0], 200)
            self.assertEqual(self.request(headers=[('Host', 'manga.example.com')])[0], 403)

    def test_public_origin_configuration_is_validated(self):
        self.assertEqual(public_origin('https://MANGA.example.com:443/'), 'https://manga.example.com')
        for value in ('http://manga.example.com', 'https://manga.example.com/path',
                      'https://u:p@manga.example.com', 'https://manga.example.com?x=1',
                      'https://manga.example.com#x', 'https://*.example.com',
                      'https://manga.example.com:65536', 'https://manga.example.com:0',
                      'https://manga..example.com', 'https://manga.example.com\n'):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                public_origin(value)


if __name__ == '__main__':
    unittest.main()
