"""读漫屋 uses the rumanhua CMS: POST /s, /morechapter, packer XOR."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from client import html_metadata, native_sources as n, providers as p

FIX = Path(__file__).parent / "fixtures/source-html"
URL = "http://dumanwu1.com/vzUMvRF/"


class DumanwuAdapterTests(unittest.TestCase):
    def test_search_posts_to_published_http_origin(self):
        payload = json.dumps({
            "code": "200", "msg": "获取成功",
            "data": [{"id": "vzUMvRF", "name": "火影忍者",
                      "imgurl": "https://p6.ecombdimg.com/cover.jpeg", "remarks": "702"}],
        }).encode()
        with patch.object(n, "_post_form", return_value=payload) as post:
            rows = p.search("dumanwu", "火影")
        self.assertEqual(post.call_args.args[:2], ("http://dumanwu1.com/s", {"k": "火影"}))
        self.assertEqual(rows[0]["title"], "火影忍者")
        self.assertEqual(rows[0]["detailUrl"], URL)
        self.assertEqual(rows[0]["siteId"], "dumanwu")

    def test_details_reuse_rumanhua_chapter_protocol(self):
        page = (FIX / "dumanwu-detail.html").read_text()
        extra = json.dumps({"code": "200", "data": [
            {"chapterid": "uANNuRo", "chaptername": "第694话"},
        ]}).encode()
        with patch.object(n, "_page", return_value=page), patch.object(n, "_post_form", return_value=extra) as more:
            detail = p.details("dumanwu", URL)
        self.assertEqual(more.call_args.args[:2], ("http://dumanwu1.com/morechapter", {"id": "vzUMvRF"}))
        self.assertEqual(detail["title"], "火影忍者")
        self.assertEqual(detail["author"], "岸本齐史")
        self.assertTrue(detail["coverUrl"].startswith("https://p6.ecombdimg.com/"))
        names = [c["name"] for c in detail["chapters"]]
        self.assertIn("700 漩涡鸣人！！", names)
        self.assertIn("第694话", names)

    def test_images_and_hosts(self):
        images = ["https://p3-zhuxiaobang-sign.shimolife.com/page.jpeg"]
        with patch.object(n, "rum_images", return_value=images) as decode:
            self.assertEqual(p.images("dumanwu", URL + "IVpdIdWi.html"), images)
        decode.assert_called_once()
        with self.assertRaises(ValueError):
            p.images("dumanwu", "https://dumanwu1.com.evil.test/vzUMvRF/a.html")
        p.validate_url("dumanwu", "http://www.dumanwu.org/vzUMvRF/")
        p.validate_url("dumanwu", "http://m.dumanwu1.com/vzUMvRF/")

    def test_detail_metadata_reads_h1_and_og_fallback(self):
        page = (FIX / "dumanwu-detail.html").read_text()
        meta = html_metadata.detail_metadata("dumanwu", page, URL)
        self.assertEqual(meta["title"], "火影忍者")
        self.assertEqual(meta["author"], "岸本齐史")
        self.assertIn("九尾妖狐", meta["description"])
        self.assertTrue(meta["coverUrl"].startswith("https://p6.ecombdimg.com/"))

    def test_real_start_link_controls_mixed_number_directory_without_resorting_extras(self):
        page = (FIX / 'dumanwu-order.html').read_text()
        extra = (FIX / 'dumanwu-morechapter.json').read_bytes()
        with patch.object(n, '_page', return_value=page), patch.object(n, '_post_form', return_value=extra):
            rows = p.details('dumanwu', URL)['chapters']
        self.assertEqual(rows[0]['url'], URL + 'NczEzEH.html')
        self.assertEqual([row['name'] for row in rows[:2]], ['外传:第1话 Hero', '外传:第2话 兵之书'])
        self.assertEqual([row['name'] for row in rows[-4:]],
                         ['第710话 双瞳中所见到的', '698 鸣人与佐助⑤', '699 和解之印', '700 漩涡鸣人！！'])
        self.assertEqual([row['order'] for row in rows], list(range(8)))

    def test_missing_or_ambiguous_start_does_not_invent_dumanwu_order(self):
        original = (FIX / 'dumanwu-order.html').read_text()
        extra = (FIX / 'dumanwu-morechapter.json').read_bytes()
        for page in [original.replace('开始阅读', '继续阅读'),
                     original.replace('<div class="stat-read-box">',
                                      '<div class="stat-read-box"><a href="/vzUMvRF/IVpdIdWi.html">开始阅读</a>')]:
            with self.subTest(page=page[:30]), patch.object(n, '_post_form', return_value=extra):
                rows = n.dumanwu_chapters(URL, page=page)
            self.assertEqual(rows[0]['name'], '700 漩涡鸣人！！')
            self.assertEqual(rows[-1]['name'], '外传:第1话 Hero')
