"""Synthetic HTML-only Comicbox regressions. No image data or network access."""
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from client import comicbox

FIXTURES = Path(__file__).parent / 'fixtures' / 'comicbox'

def fixture(name):
    return (FIXTURES / name).read_text()


class ComicboxTests(unittest.TestCase):
    def test_search_exact_title_deduplicates_and_excludes_sidebar_and_ads(self):
        with patch.object(comicbox, '_page', return_value=fixture('search.html')) as fetch:
            rows = comicbox.search('富家女姐姐')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['title'], '富家女姐姐')
        self.assertEqual(rows[0]['url'], 'https://www.comicbox.xyz/book/3872')
        self.assertEqual(rows[0]['description'], '')
        self.assertEqual(parse_qs(urlparse(fetch.call_args.args[0]).query)['keyword'], ['富家女姐姐'])
        params = parse_qs(urlparse(rows[0]['cover']).query)
        self.assertEqual(params['v'], ['2030010101'])
        self.assertEqual(params['t'], ['3'])

    def test_detail_uses_book_hero_and_directory_in_document_reading_order(self):
        with patch.object(comicbox, '_page', return_value=fixture('detail.html')):
            result = comicbox.details('https://www.comicbox.xyz/book/42')
        self.assertEqual(result['title'], '合成测试漫画')
        self.assertEqual(result['author'], '测试作者')
        self.assertEqual(result['status'], '连载中')
        self.assertEqual(result['description'], '')
        self.assertEqual([c['name'] for c in result['chapters']], ['第1話', '第2話', '番外'])
        self.assertEqual([c['order'] for c in result['chapters']], [0, 1, 2])
        self.assertEqual(parse_qs(urlparse(result['coverUrl']).query)['v'], ['2030010202'])

    def test_images_only_come_from_current_chapter_comic_pages(self):
        with patch.object(comicbox, '_page', return_value=fixture('chapter.html')):
            urls = comicbox.images('https://www.comicbox.xyz/free-chapter/2?t=synthetic')
        self.assertEqual(len(urls), 2)
        self.assertTrue(urlparse(urls[0]).path.endswith('/101.jpg'))
        self.assertTrue(urlparse(urls[1]).path.endswith('/102.jpg'))
        self.assertEqual(parse_qs(urlparse(urls[0]).query)['v'], ['2030010303'])

    def test_explicit_hosts_and_v3_manifest_are_transported_without_rewriting(self):
        with patch.object(comicbox, '_page', return_value=fixture('chapter.html')):
            url = comicbox.images('https://www.comicbox.xyz/free-chapter/2')[0]
        params = parse_qs(urlparse(url).query)
        self.assertEqual(params['hosts'], ['https://first.example/break_2,https://second.example/break_2'])
        self.assertEqual(params['manifest'], ['eyJ2YXJpYW50cyI6e319'])
        self.assertEqual(params['t'], ['3'])

    def test_cover_also_preserves_explicit_transport_attributes(self):
        source = fixture('search.html').replace("class='cropped'", "class='cropped' data-hosts='https://first.example,https://second.example' data-bmi-manifest='YWJjXy0'")
        with patch.object(comicbox, '_page', return_value=source):
            url = comicbox.search('富家女姐姐')[0]['cover']
        params = parse_qs(urlparse(url).query)
        self.assertEqual(params['hosts'], ['https://first.example,https://second.example'])
        self.assertEqual(params['manifest'], ['YWJjXy0'])

    def test_legacy_avif_and_extended_image_suffixes_are_not_dropped(self):
        for extension in ['jpg', 'jpeg', 'gif', 'avif', 'webp']:
            source = fixture('chapter.html').replace('break_2/', 'break_avif/').replace('.jpg', '.' + extension)
            with self.subTest(extension=extension), patch.object(comicbox, '_page', return_value=source):
                urls = comicbox.images('/free-chapter/2')
            self.assertEqual(len(urls), 2)
            self.assertIn('/break_avif/', urls[1])
            self.assertTrue(urlparse(urls[1]).path.endswith('.' + extension))

    def test_explicit_v3_manifest_does_not_require_guessing_legacy_path(self):
        source = fixture('chapter.html').replace('/break_2/static/upload/book/42/2/101.jpg', '/bmi-v3/immutable/photo-placeholder')
        with patch.object(comicbox, '_page', return_value=source):
            urls = comicbox.images('/free-chapter/2')
        self.assertEqual(len(urls), 2)
        self.assertEqual(urlparse(urls[0]).path, '/bmi-v3/immutable/photo-placeholder')
        self.assertEqual(parse_qs(urlparse(urls[0]).query)['manifest'], ['eyJ2YXJpYW50cyI6e319'])

    def test_cache_key_supports_quotes_and_does_not_use_text_or_comment_fallbacks(self):
        self.assertEqual(comicbox.cache_key('<script>window.BMI_CACHE_KEY = "new-version";</script>'), 'new-version')
        self.assertEqual(comicbox.cache_key("<script>BMI_CACHE_KEY='another-version';</script>"), 'another-version')
        for source in ['<p>BMI_CACHE_KEY="stale"</p>', '<script>// BMI_CACHE_KEY="stale"\n</script>', '<script>/*BMI_CACHE_KEY="stale"*/</script>', '<script src="external.js">BMI_CACHE_KEY="stale"</script>']:
            with self.subTest(source=source), self.assertRaisesRegex(RuntimeError, 'BMI_CACHE_KEY'):
                comicbox.cache_key(source)

    def test_missing_key_fails_clearly_for_all_entrypoints(self):
        with patch.object(comicbox, '_page', return_value='<html></html>'):
            for call in [lambda: comicbox.search('测试'), lambda: comicbox.details('/book/42'), lambda: comicbox.images('/free-chapter/2')]:
                with self.assertRaisesRegex(RuntimeError, '上游图片格式发生变化'):
                    call()

    def test_no_images_does_not_attempt_login_or_guess_other_pages(self):
        source = '<script>window.BMI_CACHE_KEY="current";</script><div class="sp-read-wrap">请登录后阅读</div>'
        with patch.object(comicbox, '_page', return_value=source) as fetch:
            with self.assertRaisesRegex(RuntimeError, '登录或购买'):
                comicbox.images('/chapter/2')
        self.assertEqual(fetch.call_count, 1)

    def test_foreign_and_invalid_routes_are_rejected_before_fetch(self):
        with patch.object(comicbox, '_page') as fetch:
            for url in ['https://www.comicbox.xyz.evil.example/book/42', 'http://127.0.0.1/book/42', 'https://user@www.comicbox.xyz/book/42', 'https://www.comicbox.xyz:8443/book/42', 'https://www.comicbox.xyz/search']:
                with self.subTest(url=url), self.assertRaises(ValueError):
                    comicbox.details(url)
        fetch.assert_not_called()

    def test_compatibility_page_fetch_exports_same_safe_reader(self):
        with patch.object(comicbox, '_page', return_value='fixture') as fetch:
            self.assertEqual(comicbox.fetch_page(comicbox.ORIGIN + '/'), 'fixture')
        fetch.assert_called_once_with(comicbox.ORIGIN + '/')


if __name__ == '__main__':
    unittest.main()
