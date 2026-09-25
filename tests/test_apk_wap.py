"""Behavioural regressions for the public APK WAP adapters; no live network."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from client import apk_wap as wap

FIXTURES = Path(__file__).parent / 'fixtures' / 'apk-wap'

def fixture(name):
    return (FIXTURES / name).read_text()


def payload(name):
    return json.loads(fixture(name))


class ApkWapTests(unittest.TestCase):
    def test_four_validated_sources_have_separate_brands(self):
        self.assertEqual(set(wap.SOURCES), {'kanman', 'manhuatai', 'shenmanhua', 'mkzhan'})
        for site, product in [('kanman', 'kmh'), ('manhuatai', 'mht'), ('shenmanhua', 'smh')]:
            with self.subTest(site=site), patch.object(wap, '_get', return_value=fixture('wap-search.json')) as fetch:
                rows = wap.search(site, '妖神记')
                self.assertEqual(rows[0]['title'], '妖神记')
                self.assertEqual(rows[0]['author'], '踏雪动漫')
                self.assertIn('第531话', rows[0]['latest'])
                self.assertEqual(urlparse(rows[0]['url']).hostname, wap.SOURCES[site][1])
                endpoint = urlparse(fetch.call_args.args[0])
                self.assertEqual(endpoint.hostname, 'm.kanman.com')
                self.assertEqual(parse_qs(endpoint.query)['productname'], [product])
                self.assertEqual(parse_qs(endpoint.query)['search_key'], ['妖神记'])
                self.assertEqual(rows[0]['status'], '')  # No invented ongoing/completed state.

    def test_search_pagination_is_bounded_and_deduplicated(self):
        data = payload('wap-search.json')
        data['data']['page']['total_page'] = 50
        with patch.object(wap, '_get', return_value=json.dumps(data)) as fetch:
            rows = wap.search('kanman', '妖')
        self.assertEqual(len(rows), 2)
        self.assertEqual(fetch.call_count, 2)

    def test_empty_keyword_does_not_fetch_recommendations(self):
        with patch.object(wap, '_get') as fetch:
            self.assertEqual(wap.search('kanman', '   '), [])
        fetch.assert_not_called()

    def test_wap_detail_keeps_full_directory_in_reading_order(self):
        with patch.object(wap, '_get', return_value=fixture('wap-detail.json')):
            detail = wap.details('manhuatai', 'https://m.manhuatai.com/yaoshenji/?comic_id=27417')
        self.assertEqual(detail['title'], '妖神记')
        self.assertEqual(detail['chapters'][0]['name'], '第1话 重生')
        self.assertIn('第531话', detail['chapters'][-1]['name'])
        self.assertEqual(detail['chapters'][-1]['group'], '源站收费章节')
        self.assertEqual([c['order'] for c in detail['chapters']], list(range(4)))
        self.assertIn('comic_id=27417', detail['chapters'][0]['url'])
        self.assertIn('mhxk.com', detail['coverUrl'])

    def test_missing_embedded_directory_uses_public_chapter_list(self):
        data = payload('wap-detail.json'); del data['data']['comic_chapter']
        with patch.object(wap, '_get', side_effect=[json.dumps(data), fixture('wap-chapters.json')]) as fetch:
            detail = wap.details('kanman', 'https://m.kanman.com/27417/')
        self.assertEqual(detail['chapters'][0]['name'], '第1话 重生')
        self.assertEqual(fetch.call_count, 2)
        self.assertIn('/getchapterlist/', fetch.call_args.args[0])

    def test_decimal_chapter_identifiers_are_not_dropped(self):
        chapters = wap._chapters_wap('kanman', '27417', [
            {'chapter_newid': '4.26qingjiatiao-1682405610', 'chapter_name': '4.26请假条'},
            {'chapter_newid': '118.2', 'chapter_name': '第118话2 神秘的本子'},
        ])
        self.assertEqual(len(chapters), 2)
        self.assertEqual(chapters[0]['id'], '118.2')
        self.assertIn('chapter_newid=118.2', chapters[0]['url'])

    def test_wap_images_preserve_signed_urls_without_generating_image_rules(self):
        url = 'https://m.taomanhua.com/api/getchapterinfov2/?comic_id=27417&chapter_newid=dyhzs'
        with patch.object(wap, '_get', return_value=fixture('wap-images.json')) as fetch:
            images = wap.images('shenmanhua', url)
        self.assertEqual(len(images), 2)
        self.assertTrue(images[0].endswith('auth_key=fixture-only'))
        self.assertEqual(parse_qs(urlparse(fetch.call_args.args[0]).query)['productname'], ['smh'])

    def test_chapter_identity_mismatch_is_not_presented_as_requested_chapter(self):
        data = payload('wap-images.json'); data['data']['current_chapter']['chapter_newid'] = 'different-chapter'
        with patch.object(wap, '_get', return_value=json.dumps(data)):
            with self.assertRaisesRegex(RuntimeError, '另一章节'):
                wap.images('kanman', 'https://m.kanman.com/api/getchapterinfov2/?comic_id=27417&chapter_newid=dyhzs')
        data = payload('wap-images.json'); data['data']['comic_id'] = 999
        with patch.object(wap, '_get', return_value=json.dumps(data)):
            with self.assertRaisesRegex(RuntimeError, '另一部漫画'):
                wap.images('kanman', 'https://m.kanman.com/api/getchapterinfov2/?comic_id=27417&chapter_newid=dyhzs')

    def test_unavailable_chapter_has_actionable_message(self):
        data = payload('wap-images.json'); data['data']['current_chapter']['chapter_img_list'] = []
        with patch.object(wap, '_get', return_value=json.dumps(data)):
            with self.assertRaisesRegex(RuntimeError, '登录、购买'):
                wap.images('kanman', 'https://m.kanman.com/api/getchapterinfov2/?comic_id=27417&chapter_newid=dyhzs')
        with patch.object(wap, '_get', return_value='{"code":"403","message":"请先购买本章"}'):
            with self.assertRaisesRegex(RuntimeError, '请先购买本章'):
                wap.images('mkzhan', 'https://www.mkzhan.com/207622/476064.html')

    def test_mkzhan_search_does_not_return_recommendations_after_no_match(self):
        with patch.object(wap, '_get', return_value=fixture('mkzhan-no-results.html')):
            self.assertEqual(wap.search('mkzhan', '斗罗'), [])
        with patch.object(wap, '_get', return_value=fixture('mkzhan-search.html')):
            rows = wap.search('mkzhan', '妖神记')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['title'], '妖神记')
        self.assertEqual(rows[0]['url'], 'https://www.mkzhan.com/207622/')
        self.assertIn('oss.mkzcdn.com', rows[0]['cover'])

    def test_mkzhan_detail_ignores_recommendation_metadata_and_strips_icon_glyphs(self):
        source = fixture('mkzhan-detail.html') + '<aside><p class="comic-title">推荐作品</p><span class="name">其他作者</span></aside>'
        with patch.object(wap, '_get', return_value=source):
            detail = wap.details('mkzhan', 'https://www.mkzhan.com/207622/')
        self.assertEqual(detail['title'], '妖神记')
        self.assertEqual(detail['author'], '踏雪动漫')
        self.assertEqual(detail['status'], '连载')
        self.assertEqual(detail['chapters'][0]['name'], '第1话 重生')
        self.assertTrue(detail['chapters'][-1]['name'].startswith('第531话'))
        self.assertEqual(detail['chapters'][-1]['group'], '源站收费章节')
        self.assertNotIn('推荐作品', detail['description'])

    def test_mkzhan_images_use_anonymous_public_content_endpoint(self):
        with patch.object(wap, '_get', return_value=fixture('mkzhan-images.json')) as fetch:
            images = wap.images('mkzhan', 'https://www.mkzhan.com/217081/1068988.html')
        params = parse_qs(urlparse(fetch.call_args.args[0]).query)
        self.assertEqual(params['uid'], ['0']); self.assertEqual(params['sign'], ['0'])
        self.assertEqual(params['comic_id'], ['217081']); self.assertEqual(params['chapter_id'], ['1068988'])
        self.assertEqual(len(images), 2)
        self.assertIn('content.mkzcdn.com', images[0])

    def test_invalid_source_urls_do_not_trigger_requests(self):
        with patch.object(wap, '_get') as fetch:
            for bad in ['https://evil.test/27417/', 'https://m.kanman.com.evil.test/27417/', 'http://127.0.0.1/27417/', 'https://m.kanman.com:8443/27417/', 'https://user@ m.kanman.com/27417/', 'https://m.kanman.com/no-id/']:
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    wap.details('kanman', bad)
        fetch.assert_not_called()

    def test_redirect_and_image_domain_boundaries_remain_closed(self):
        for bad in ['https://localhost/', 'http://m.kanman.com/', 'https://m.kanman.com.evil.test/', 'https://www.mkzhan.com:8888/']:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                wap._safe_fetch_url(bad)
        for bad in ['https://content.mkzcdn.com.evil.test/page.jpg', 'http://127.0.0.1/page.jpg', 'javascript:alert(1)']:
            self.assertEqual(wap._image_url(bad), '')
        self.assertEqual(wap._image_url('//content.mkzcdn.com/page.jpg'), 'https://content.mkzcdn.com/page.jpg')

    def test_business_errors_and_changed_markup_are_not_silent_empty_searches(self):
        with patch.object(wap, '_get', return_value='{"status":4,"message":"服务繁忙"}'):
            with self.assertRaisesRegex(RuntimeError, '服务繁忙'):
                wap.search('kanman', '妖神记')
        with patch.object(wap, '_get', return_value='{"status":0,"data":{"unexpected":[]}}'):
            with self.assertRaisesRegex(RuntimeError, '格式发生变化'):
                wap.search('kanman', '妖神记')
        with patch.object(wap, '_get', return_value='<h1>Maintenance</h1>'):
            with self.assertRaisesRegex(RuntimeError, '结构发生变化'):
                wap.search('mkzhan', '妖神记')


if __name__ == '__main__':
    unittest.main()
