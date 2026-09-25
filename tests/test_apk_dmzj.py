import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from client import apk_dmzj as source

FIXTURES = Path(__file__).parent / "fixtures" / "apk-dmzj"


def fixture(name):
    return json.loads((FIXTURES / (name + ".json")).read_text())


class ZaimanhuaTests(unittest.TestCase):
    def test_only_verified_live_api_is_registered_with_explicit_notice(self):
        self.assertEqual(set(source.SOURCES), {"zaimanhua"})
        self.assertIn("部分作品", source.SOURCE_NOTICES["zaimanhua"])
        with self.assertRaises(ValueError):
            source.search("dmzj", "一拳")

    def test_real_search_response_normalizes_fields_and_uses_public_mobile_url(self):
        with patch.object(source, "_get_json", return_value=fixture("search-onepunch")) as get:
            rows = source.search("zaimanhua", "一拳")
        self.assertEqual(len(rows), 14)
        self.assertEqual(rows[0]["title"], "请接受我这一拳！")
        self.assertEqual(rows[0]["author"], "murata")
        self.assertEqual(rows[0]["latest"], "第20话")
        self.assertEqual(rows[0]["status"], "已完结")
        self.assertEqual(rows[0]["url"], "https://m.zaimanhua.com/pages/comic/detail?id=42910")
        self.assertEqual(get.call_count, 1)
        self.assertEqual(get.call_args.args[1]["keyword"], "一拳")

    def test_real_zero_matches_are_empty_but_business_errors_are_not(self):
        with patch.object(source, "_get_json", return_value=fixture("search-empty-march")):
            self.assertEqual(source.search("zaimanhua", "三月的狮子"), [])
        for payload in [{"errno": 401, "errmsg": "访问受限", "data": {}}, {"errno": 0, "data": {"list": [], "total": 2}}, {"errno": 0, "data": {}}]:
            with self.subTest(payload=payload), patch.object(source, "_get_json", return_value=payload), self.assertRaises(RuntimeError):
                source.search("zaimanhua", "一拳")

    def test_search_pagination_merges_deduplicates_and_stops_at_total(self):
        first = fixture("search-onepunch")
        first["data"]["total"] = 21
        second = copy.deepcopy(first)
        extra = copy.deepcopy(first["data"]["list"][0])
        extra.update(id=99999, title="第二页准确作品")
        second["data"]["list"] = [first["data"]["list"][0], extra]
        with patch.object(source, "_get_json", side_effect=[first, second]) as get:
            rows = source.search("zaimanhua", "一拳")
        self.assertEqual(len(rows), 15)
        self.assertEqual(rows[-1]["title"], "第二页准确作品")
        self.assertEqual([call.args[1]["page"] for call in get.call_args_list], [1, 2])
        with patch.object(source, "_get_json", return_value=first), self.assertRaisesRegex(RuntimeError, "分页"):
            source.search("zaimanhua", "一拳")

    def test_real_directory_preserves_metadata_and_reading_restriction(self):
        with patch.object(source, "_get_json", return_value=fixture("detail-restricted")) as get:
            data = source.details("zaimanhua", source._book_url(42910))
        self.assertEqual(data["title"], "请接受我这一拳！")
        self.assertEqual(data["author"], "murata")
        self.assertEqual(data["status"], "已完结")
        self.assertEqual(len(data["chapters"]), 22)
        self.assertEqual(data["chapters"][0]["name"], "第01话")
        self.assertEqual(data["chapters"][-1]["name"], "第20话")
        self.assertEqual([row["order"] for row in data["chapters"]], list(range(22)))
        self.assertEqual({row["group"] for row in data["chapters"]}, {"连载"})
        self.assertIn("未授予", data["unavailableReason"])
        self.assertEqual(get.call_args.kwargs["platform"], "pc")

    def test_group_order_and_duplicate_chapters_do_not_merge_different_volumes(self):
        data = fixture("detail-restricted")
        comic = data["data"]["data"]
        one, two = comic["chapters"][0]["data"][-1], comic["chapters"][0]["data"][-2]
        special = dict(one, chapter_id=900001, chapter_name="番外", chapter_order=1)
        comic.update(canRead=True, chapters=[{"title": "连载", "data": [two, one, one]}, {"title": "番外", "data": [special]}])
        with patch.object(source, "_get_json", return_value=data):
            result = source.details("zaimanhua", source._book_url(42910))
        self.assertEqual([row["group"] for row in result["chapters"]], ["连载", "连载", "番外"])
        self.assertEqual(result["chapters"][0]["id"], str(one["chapter_id"]))
        self.assertNotIn("unavailableReason", result)

    def test_real_chapter_access_denial_is_actionable_not_empty_success(self):
        with patch.object(source, "_get_json", return_value=fixture("chapter-restricted")), self.assertRaisesRegex(RuntimeError, "未授予.*阅读权限"):
            source.images("zaimanhua", source._chapter_url(42910, 73595))

    def test_real_public_work_directory_and_signed_image_fields(self):
        with patch.object(source, "_get_json", return_value=fixture("detail-public")):
            data = source.details("zaimanhua", source._book_url(86003))
        self.assertEqual(data["title"], "人人都会魔法的世界")
        self.assertEqual(len(data["chapters"]), 22)
        self.assertNotIn("unavailableReason", data)
        self.assertEqual(data["chapters"][0]["id"], "182392")
        with patch.object(source, "_get_json", return_value=fixture("chapter-public")):
            images = source.images("zaimanhua", data["chapters"][0]["url"])
        self.assertEqual(len(images), 5)
        # Captured URL structure is real; signatures are deliberately redacted
        # in the fixture. Production parsing preserves signed queries.
        self.assertTrue(all("sign=fixture-redacted" in image and "t=0" in image for image in images))

    def test_permission_denial_takes_precedence_even_if_urls_are_present(self):
        data = fixture("chapter-authorized-synthetic")
        data["data"]["data"]["canRead"] = False
        with patch.object(source, "_get_json", return_value=data), self.assertRaisesRegex(RuntimeError, "未授予"):
            source.images("zaimanhua", source._chapter_url(42910, 73595))

    def test_authorized_synthetic_chapter_preserves_page_order(self):
        with patch.object(source, "_get_json", return_value=fixture("chapter-authorized-synthetic")) as get:
            images = source.images("zaimanhua", source._chapter_url(42910, 73595))
        self.assertEqual(images, ["https://images.zaimanhua.com/comics/example/page-1.jpg", "https://images.zaimanhua.com/comics/example/page-2.jpg"])
        self.assertEqual(get.call_args.kwargs["platform"], "h5")

    def test_unknown_permission_wrong_chapter_or_invalid_image_fails_closed(self):
        changes = [{"canRead": None}, {"comic_id": 2}, {"chapter_id": 3}, {"page_url_hd": []}, {"page_url_hd": ["http://127.0.0.1/private"]}, {"page_url_hd": ["https://images.zaimanhua.com.evil.test/page.jpg"]}]
        for change in changes:
            data = fixture("chapter-authorized-synthetic")
            data["data"]["data"].update(change)
            with self.subTest(change=change), patch.object(source, "_get_json", return_value=data), self.assertRaises(RuntimeError):
                source.images("zaimanhua", source._chapter_url(42910, 73595))

    def test_rejects_noncanonical_or_ambiguous_urls_before_network(self):
        urls = ["https://m.zaimanhua.com.evil.test/pages/comic/detail?id=42910", "http://127.0.0.1/pages/comic/detail?id=42910", "https://user@m.zaimanhua.com/pages/comic/detail?id=42910", "https://m.zaimanhua.com:8765/pages/comic/detail?id=42910", "https://m.zaimanhua.com/pages/comic/detail?id=42910&id=2", "https://m.zaimanhua.com/pages/comic/detail?id=../private", "https://m.zaimanhua.com/unrelated?id=42910"]
        with patch.object(source, "_get_json") as get:
            for url in urls:
                with self.subTest(url=url), self.assertRaises(ValueError):
                    source.details("zaimanhua", url)
            get.assert_not_called()

    def test_network_errors_are_reported_without_empty_search(self):
        with patch.object(source, "urlopen", side_effect=HTTPError(source.API_BASE, 503, "unavailable", {}, None)), self.assertRaisesRegex(RuntimeError, "HTTP 503"):
            source.search("zaimanhua", "一拳")


if __name__ == "__main__":
    unittest.main()
