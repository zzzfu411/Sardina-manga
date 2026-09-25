"""Rum source-order regression from ordinary-work metadata, 2026-09-22."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from client import native_sources as n, providers


FIX = Path(__file__).parent / 'fixtures/discovery-html'
URL = 'http://rumanhua2.com/vzUECRF/'


class RumanhuaReadingOrderTests(unittest.TestCase):
    def setUp(self):
        self.page = (FIX / 'rumanhua-directory.html').read_text()
        self.extra = (FIX / 'rumanhua-morechapter.json').read_bytes()

    def test_real_begin_reading_target_becomes_first_in_application_directory(self):
        # HTML contains newest chapters; /morechapter continues towards the
        # source's explicitly linked beginning, including unnumbered entries.
        with patch.object(n, '_page', return_value=self.page) as page, \
                patch.object(n, '_post_form', return_value=self.extra) as more:
            detail = providers.details('rumanhua', URL)
        chapters = detail['chapters']
        self.assertEqual(detail['title'], '星甲魂将传')
        self.assertEqual(len(chapters), 15)
        self.assertEqual([x['name'] for x in chapters[:5]], ['预告', '设定图', '001话 最后一战', '002话 星主系统', '003话 绝境'])
        self.assertEqual(chapters[0]['url'], URL + 'aCVImOY.html')
        self.assertEqual([x['name'] for x in chapters[-3:]], ['390 插羽破天骄', '391 阵解星芒尽', '392 营空海雾消'])
        self.assertEqual([x['order'] for x in chapters], list(range(15)))
        self.assertIn('更新推迟通知', [x['name'] for x in chapters])
        # Applying the generic body/extras classifier again moves the explicit
        # 167 label into this early extra's slot, ahead of bare-number 127.
        names = [x['name'] for x in chapters]
        self.assertLess(names.index('番外 80万收藏联动特别篇'), names.index('127 暗镜司'))
        self.assertLess(names.index('127 暗镜司'), names.index('167 章六野出手'))
        page.assert_called_once()
        more.assert_called_once()
        self.assertEqual(more.call_args.args[:2], ('http://rumanhua2.com/morechapter', {'id': 'vzUECRF'}))

    def test_missing_or_conflicting_source_start_marker_does_not_guess(self):
        changed_pages = [self.page.replace('stat-read-box', 'unknown-box'),
                         self.page.replace('aCVImOY.html', 'tbrrvhKH.html'),
                         self.page.replace('开始阅读', '继续阅读')]
        for page in changed_pages:
            with self.subTest(page=page[:50]), patch.object(n, '_post_form', return_value=self.extra):
                rows = n.rum_chapters(URL, page=page)
            self.assertEqual(rows[0]['name'], '392 营空海雾消')
            self.assertEqual(rows[-1]['name'], '预告')
        with patch.object(n, '_post_form', side_effect=RuntimeError('unavailable')):
            rows = n.rum_chapters(URL, page=self.page)
        self.assertEqual(rows[0]['name'], '392 营空海雾消')

    def test_mixed_numbering_or_already_ascending_source_order_is_unchanged(self):
        extra = json.loads(self.extra)
        extra['data'][0]['chaptername'] = '400 非顺序条目'
        with patch.object(n, '_post_form', return_value=json.dumps(extra).encode()):
            rows = n.rum_chapters(URL, page=self.page)
        self.assertEqual(rows[0]['name'], '392 营空海雾消')
        self.assertEqual(rows[-1]['name'], '预告')
        with patch.object(n, '_post_form', return_value=self.extra):
            corrected = n.rum_chapters(URL, page=self.page)
        self.assertEqual(n._rum_reading_order(corrected, self.page, URL), corrected)


if __name__ == '__main__':
    unittest.main()
