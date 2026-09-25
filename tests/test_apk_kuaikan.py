import json
from pathlib import Path
import unittest
from unittest.mock import patch

from client import apk_kuaikan as kk
from client.serialized_state import nuxt_state

FIXTURES = Path(__file__).parent / "fixtures" / "apk-kuaikan"


def state_page(value):
    return '<script>window.__NUXT__=(function(){return ' + json.dumps(value) + ';}());</script>'


class NuxtLiteralTests(unittest.TestCase):
    def test_aliases_and_shared_objects_without_javascript(self):
        page = '<script>window.__NUXT__=(function(a,b,c){b[0]=a;b[1]="剧情";c.nickname="墨飞";return {data:[{title:a,tags:b,user:c,free:true}]}}("谷围南亭",Array(2),{}));</script>'
        row = nuxt_state(page)["data"][0]
        self.assertEqual(row, {"title": "谷围南亭", "tags": ["谷围南亭", "剧情"], "user": {"nickname": "墨飞"}, "free": True})

    def test_calls_property_reads_and_arbitrary_statements_are_rejected(self):
        for expression in [
            '(function(){return {data:fetch("https://example.com")}}())',
            '(function(a){return {x:a.constructor}}({}))',
            '(function(a){a.run();return {}}({}))',
            '(function(){while(true){};return {}}())',
            '(function(a){return {a:a}}(Array(10000000)))',
            '(function(a){a[999999999]=1;return {}}([]))',
            '(function(){return {x:1+2}}())',
        ]:
            with self.subTest(expression=expression), self.assertRaises(ValueError):
                nuxt_state('<script>window.__NUXT__=' + expression + ';</script>')

    def test_unknown_format_and_wrong_parameter_count(self):
        for page in ['<html>维护中</html>', '<script>window.__NUXT__=(function(a){return {}}());</script>']:
            with self.assertRaises(ValueError):
                nuxt_state(page)


class KuaikanTests(unittest.TestCase):
    def test_search_and_empty_results(self):
        with patch.object(kk, '_get', return_value=(FIXTURES / 'search.json').read_text()):
            row = kk.search('kuaikan', '谷围南亭')[0]
            self.assertEqual(row['author'], '墨飞')
            self.assertEqual(row['url'], 'https://www.kuaikanmanhua.com/web/topic/2625/')
            self.assertEqual(row['latest'], '《谷围南亭》同名动画热播中')
        with patch.object(kk, '_get', return_value='{"code":200,"hits":[]}'):
            self.assertEqual(kk.search('kuaikan', '不存在'), [])
        with patch.object(kk, '_get', return_value='{"code":403,"message":"访问受限"}'):
            with self.assertRaisesRegex(RuntimeError, '受限'):
                kk.search('kuaikan', '测试')

    def test_directory_keeps_order_and_marks_locked_chapters(self):
        with patch.object(kk, '_get', return_value=(FIXTURES / 'detail.html').read_text()):
            detail = kk.details('kuaikan', 'https://m.kuaikanmanhua.com/mobile/2625/list/')
        self.assertEqual(detail['title'], '谷围南亭')
        self.assertEqual(detail['author'], '墨飞')
        self.assertEqual([r['order'] for r in detail['chapters']], [0, 1])
        self.assertFalse(detail['chapters'][0]['locked'])
        self.assertTrue(detail['chapters'][1]['locked'])
        self.assertIn('源站受限', detail['chapters'][1]['name'])
        self.assertIn('部分章节', detail['unavailableReason'])

    def test_incomplete_directory_is_not_reported_as_complete(self):
        data = {'data': [{'topicInfo': {'id': 1, 'comics_count': 2}, 'comics': [{'id': 11, 'title': '1'}]}]}
        with patch.object(kk, '_get', return_value=state_page(data)):
            with self.assertRaisesRegex(RuntimeError, '未完整'):
                kk.details('kuaikan', 'https://www.kuaikanmanhua.com/web/topic/1/')

    def test_image_order_signed_query_and_cdn_filter(self):
        with patch.object(kk, '_get', return_value=(FIXTURES / 'chapter.html').read_text()):
            rows = kk.images('kuaikan', 'https://www.kuaikanmanhua.com/web/comic/132643')
        self.assertEqual(rows, ['https://tn1.kkmh.com/page1.webp?sign=a%2Bb&t=123', 'https://tn1.kkmh.com/page2.webp'])

    def test_locked_chapter_cannot_leak_preview_as_full_chapter(self):
        info = {'id': 2, 'locked': True, 'comic_images': [{'url': 'https://tn1.kkmh.com/preview.webp'}]}
        with patch.object(kk, '_get', return_value=state_page({'data': [{'res': {'code': 200, 'data': {'comic_info': info}}}]})):
            with self.assertRaisesRegex(RuntimeError, '权限限制'):
                kk.images('kuaikan', 'https://www.kuaikanmanhua.com/web/comic/2')

    def test_malformed_address_does_not_request_network(self):
        for url in ['https://www.kuaikanmanhua.com.evil.test/web/topic/1/', 'https://user@www.kuaikanmanhua.com/web/topic/1/', 'https://www.kuaikanmanhua.com:8000/web/topic/1/', 'https://www.kuaikanmanhua.com/web/topic/not-an-id/']:
            with patch.object(kk, '_get') as get, self.assertRaises(ValueError):
                kk.details('kuaikan', url)
            get.assert_not_called()


if __name__ == '__main__':
    unittest.main()
