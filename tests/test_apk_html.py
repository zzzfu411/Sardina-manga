"""Offline regressions. Historical contract samples are not live-source proof."""
import base64
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from client import apk_html as adapter

FIXTURES = Path(__file__).parent / "fixtures" / "apk-html"


class APKHTMLTests(unittest.TestCase):
    def test_no_failed_live_candidate_is_advertised_as_readable(self):
        self.assertEqual(adapter.SOURCES, {})
        self.assertEqual(adapter.IMAGE_DOMAINS, ())
        with patch.object(adapter, "_fetch") as fetch:
            for site in adapter.UNAVAILABLE:
                for call, arg in [(adapter.search, "三月的狮子"), (adapter.details, "https://example.org/book"), (adapter.images, "https://example.org/chapter")]:
                    with self.assertRaisesRegex(RuntimeError, "暂未启用"):
                        call(site, arg)
            fetch.assert_not_called()

    def test_live_cola_search_excludes_the_recommendation_sidebar(self):
        rows = adapter.parse_search("colamanga", (FIXTURES / "colamanga-search-live.html").read_text(), "https://www.yoyomanga.com/search")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["title"], "一拳超人 G(三方)")
        self.assertEqual(rows[0]["author"], "村田雄介，ONE")
        self.assertEqual(rows[0]["status"], "连载中")
        self.assertEqual(rows[0]["cover"], "https://res.yoyomanga.com/comic/10449/cover.jpg")
        self.assertTrue(rows[1]["url"].endswith("/manga-yx53886/"))

    def test_live_cola_app_advertisement_is_not_a_readable_chapter(self):
        detail = adapter.parse_details("colamanga", (FIXTURES / "colamanga-app-only-live.html").read_text(), "https://www.yoyomanga.com/manga-gm397966/")
        self.assertEqual(detail["title"], "一拳超人 G(三方)")
        self.assertEqual(detail["chapters"], [])
        self.assertIn("APP", detail["unavailableReason"])

    def test_parked_domains_fail_instead_of_returning_successful_empty_search(self):
        for filename in ["gufeng-parked-live.html", "gufeng-sale-live.html"]:
            with self.assertRaisesRegex(RuntimeError, "停放或出售"):
                adapter.parse_search("gufengmh", (FIXTURES / filename).read_text(), "https://www.gufengmh9.com/")

    def test_unrecognized_redirect_script_is_not_a_successful_empty_search(self):
        with self.assertRaisesRegex(RuntimeError, "可识别"):
            adapter.parse_search("manhuadb", "<script>location.href='https://other.example';</script>", "https://www.manhua666.cc/")

    def test_source_url_and_redirect_boundaries_are_exact(self):
        self.assertEqual(adapter._validate_url("colamanga", "https://www.yoyomanga.com/manga-a/"), "https://www.yoyomanga.com/manga-a/")
        for url in ["https://www.yoyomanga.com.evil.example/a", "https://evil.example/a", "https://user:password@www.yoyomanga.com/a", "http://127.0.0.1/a", "https://www.yoyomanga.com:8443/a"]:
            with self.assertRaises(ValueError):
                adapter._validate_url("colamanga", url)

    def test_historical_gufeng_literals_decode_without_executing_rule_javascript(self):
        # Synthetic contract fixture from the supplied Legado selectors; the
        # live GuFeng host is parked and is deliberately not enabled.
        page = '<script>var chapterImages=["001.jpg","002.jpg"];var chapterPath="books/77/";</script>'
        self.assertEqual(adapter.parse_images("gufengmh", page, "https://www.gufengmh9.com/manhua/77/1.html"), ["https://res.xiaoqinre.com/books/77/001.jpg", "https://res.xiaoqinre.com/books/77/002.jpg"])
        with self.assertRaises(RuntimeError):
            adapter.parse_images("gufengmh", '<script>chapterImages=[__import__("os").system("false")];chapterPath="books/";</script>', "https://www.gufengmh9.com/")

    def test_historical_manhuadb_base64_contract_and_invalid_entries(self):
        data = base64.b64encode(json.dumps([{"img": "001.jpg"}, {"img": "002.jpg"}]).encode()).decode()
        page = '<div data-host="https://cdn.example/" data-img_pre="comic/1/"></div><script>var img_data = \'' + data + "';</script>"
        self.assertEqual(adapter.parse_images("manhuadb", page, "https://www.manhua666.cc/manhua/1/1.html"), ["https://cdn.example/comic/1/001.jpg", "https://cdn.example/comic/1/002.jpg"])
        with self.assertRaisesRegex(RuntimeError, "解码失败"):
            adapter.parse_images("manhuadb", "<script>var img_data = 'not base64';</script>", "https://www.manhua666.cc/")

    def test_cola_encrypted_metadata_is_not_treated_as_plain_image_urls(self):
        with self.assertRaisesRegex(RuntimeError, "尚无通过图片实测"):
            adapter.parse_images("colamanga", "<script>var C_DATA='encrypted';</script>", "https://www.yoyomanga.com/manga-a/1/1.html")


if __name__ == "__main__":
    unittest.main()
