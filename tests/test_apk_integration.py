"""Exercise source parsers through the application API contract."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from client import providers
from server import Application, validate_image

FIXTURES = Path(__file__).parent / 'fixtures'


class ApkIntegrationTests(unittest.TestCase):
    def test_wap_search_directory_and_images_reach_application(self):
        folder = FIXTURES / 'apk-wap'
        for site in ('kanman', 'manhuatai', 'shenmanhua'):
            app = Application()
            with self.subTest(site=site), patch.object(providers.apk_wap, '_get', side_effect=[
                (folder / 'wap-search.json').read_text(),
                (folder / 'wap-detail.json').read_text(),
                (folder / 'wap-images.json').read_text(),
            ]):
                group, = app.post('/api/search', {'siteId': site, 'keyword': '妖神记'})
                row = group['results'][0]
                self.assertEqual(row['siteId'], site)
                self.assertEqual(row['title'], '妖神记')
                validate_image(row['coverUrl'])
                detail = app.post('/api/details', {'siteId': site, 'detailUrl': row['detailUrl']})
                self.assertEqual(detail['chapters'][0]['name'], '第1话 重生')
                result = app.post('/api/chapter-images', {'siteId': site, 'chapterUrl': detail['chapters'][0]['url']})
                self.assertEqual(len(result['images']), 2)
                for url in result['images']:
                    validate_image(url)

    def test_zaimanhua_restricted_work_returns_an_actionable_error(self):
        folder = FIXTURES / 'apk-dmzj'
        app = Application()
        with patch.object(providers.apk_dmzj, '_get_json', side_effect=[
            json.loads((folder / 'detail-restricted.json').read_text()),
            json.loads((folder / 'chapter-restricted.json').read_text()),
        ]):
            detail = app.post('/api/details', {'siteId': 'zaimanhua', 'detailUrl': 'https://m.zaimanhua.com/pages/comic/detail?id=42910'})
            self.assertIn('权限', detail['unavailableReason'])
            with self.assertRaisesRegex(RuntimeError, '权限'):
                app.post('/api/chapter-images', {'siteId': 'zaimanhua', 'chapterUrl': detail['chapters'][0]['url']})


if __name__ == '__main__':
    unittest.main()
