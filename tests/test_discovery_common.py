import io
import json
from email.message import Message
import unittest
from unittest.mock import Mock, patch
from urllib.request import Request

from client import discovery_common as c


class Response(io.BytesIO):
    def __init__(self, body=b'<html></html>', url='https://source.test/rank', kind='text/html', length=None):
        super().__init__(body)
        self.url = url
        self.headers = Message()
        self.headers['Content-Type'] = kind
        if length is not None:
            self.headers['Content-Length'] = str(length)
    def geturl(self):
        return self.url


class DiscoveryTransportTests(unittest.TestCase):
    def test_read_only_json_post_preserves_observed_body_and_bounds_timeout(self):
        opener = Mock(open=Mock(return_value=Response(b'{"items": []}', kind='application/json')))
        with patch.object(c, 'build_opener', return_value=opener):
            self.assertEqual(c.read_json('https://source.test/rank', hosts=('source.test',),
                                        data=b'{"query":"list"}', headers={'Content-Type':'application/json'}), {'items':[]})
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(request.data, b'{"query":"list"}')
        self.assertLessEqual(opener.open.call_args.kwargs['timeout'], 12)

    def test_rejects_unexpected_host_credentials_paths_redirect_and_response_types(self):
        for url in ('https://evil.test/rank', 'https://source.test.evil.test/rank', 'https://u@source.test/rank',
                    'https://source.test:444/rank', 'file:///rank', 'https://source.test/rank#x',
                    'https://source.test/rank\n', 'https://source.test\\evil/rank'):
            with self.subTest(url=url), patch.object(c, 'build_opener') as build, self.assertRaises(ValueError):
                c.read_text(url, hosts=('source.test',))
            build.assert_not_called()
        redirect = c._Redirect('https://source.test/rank', ('source.test',))
        with self.assertRaises(c.DiscoveryError):
            redirect.redirect_request(Request('https://source.test/rank'), None, 302, '', {}, 'https://source.test/login')
        for response in (Response(url='https://source.test/login'), Response(kind='image/jpeg'),
                         Response(length=c.MAX_BYTES+1), Response(b'x'*33)):
            with self.subTest(kind=response.headers['Content-Type']), patch.object(c, 'build_opener', return_value=Mock(open=Mock(return_value=response))), self.assertRaises(c.DiscoveryError):
                c.read_text('https://source.test/rank', hosts=('source.test',), max_bytes=32)

    def test_deadline_encoding_and_invalid_json_are_explicit_errors(self):
        with patch.object(c.time, 'monotonic', return_value=10), self.assertRaisesRegex(c.DiscoveryError, '超时'):
            c.read_text('https://source.test/rank', hosts=('source.test',), deadline=10)
        with patch.object(c, 'build_opener', return_value=Mock(open=Mock(return_value=Response('漫画'.encode('gbk'), kind='text/html; charset=gbk')))):
            self.assertEqual(c.read_text('https://source.test/rank', hosts=('source.test',)), '漫画')
        with patch.object(c, 'read_text', return_value='<html>not json</html>'), self.assertRaises(c.DiscoveryError):
            c.read_json('https://source.test/rank', hosts=('source.test',))

    def test_cover_validation_retains_real_addresses_only(self):
        self.assertEqual(c.image_url('//cdn.source.test/1.jpg', 'https://source.test', ('source.test',)), 'https://cdn.source.test/1.jpg')
        for value in ('https://source.test.evil.test/1.jpg', 'javascript:alert(1)', 'https://source.test/placeholder.jpg',
                      'https://user@source.test/1.jpg', 'https://source.test/1.jpg#bad'):
            self.assertEqual(c.image_url(value, 'https://source.test', ('source.test',)), '')

    def test_result_keeps_missing_rank_and_rejects_duplicate_or_mismatched_books(self):
        row = {'title':'漫画','detailUrl':'https://source.test/book/1','coverUrl':'https://cdn.source.test/1.jpg'}
        data = c.result('source','来源','popular','',1,[row.copy()],has_more=False,source_url='https://source.test/rank',label='首页热门',note='当前首页热门展示')
        self.assertNotIn('rank',data['items'][0])
        self.assertEqual(data['paginationNote'],'当前首页热门展示')
        for rows in ([row.copy(),row.copy()],[{**row,'siteId':'other'}],[{**row,'rank':True}]):
            with self.assertRaises(c.DiscoveryError):
                c.result('source','来源','popular','',1,rows,has_more=False,source_url='https://source.test/rank',label='榜单')
