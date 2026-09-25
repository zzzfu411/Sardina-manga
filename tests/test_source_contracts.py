"""Source success must mean complete content, or an explicitly empty search.

Only upstream HTTP/JSON responses are replaced; production parsers, pagination,
normalization, and Application caching remain in the exercised path.
"""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from client import providers as p, apk_kuaikan as kk, apk_official_catalogs as official
from server import Application

FIXTURES = Path(__file__).parent / "fixtures"
CHALLENGE = '<html><title>Just a moment...</title><div id="cf-challenge-running">Enable JavaScript and cookies to continue.</div></html>'
OLD_HTML_SITES = ("baozimh", "manhuazhijia", "tuku", "manhuagui", "dm5", "mangabz", "manben")


def state_page(data):
    return '<script>window.__NUXT__=(function(){return ' + json.dumps({"data": [data]}) + ';}());</script>'


class SourceCompletenessTests(unittest.TestCase):
    def dm_response(self, count, batches):
        def get(_session, url, **kwargs):
            query = parse_qs(urlparse(url).query)
            if "page" not in query:
                return f'<script>var DM5_CID=1001;var DM5_MID=10;var DM5_IMAGE_COUNT={count};</script>'.encode(), url, 200
            values = batches.get(int(query["page"][0]))
            data = ('var pix="https://cdndm5.com/synthetic/";var pvalue=' + json.dumps(values) + ';').encode() if values is not None else b'<html>temporarily unavailable</html>'
            return data, url, 200
        return get

    def test_dm5_short_declared_chapter_is_an_error_and_not_cached(self):
        app = Application()
        with patch.object(p.dm.Session, "get", new=self.dm_response(6, {1: ["1.jpg", "2.jpg"]})):
            with self.assertRaisesRegex(RuntimeError, "完整|缺|页数"):
                app.post("/api/chapter-images", {"siteId": "dm5", "chapterUrl": "https://www.dm5.com/m1001/"})
        self.assertEqual(len(app.cache.data), 0)

    def test_dm5_complete_page_order_preserved(self):
        with patch.object(p.dm.Session, "get", new=self.dm_response(6, {1: ["1.jpg", "2.jpg"], 3: ["3.jpg", "4.jpg"], 5: ["5.jpg", "6.jpg"]})):
            rows = p.images("dm5", "https://www.dm5.com/m1001/")
        self.assertEqual(rows, [f"https://cdndm5.com/synthetic/{n}.jpg" for n in range(1, 7)])

    def test_dm5_duplicate_and_excess_pages_cannot_satisfy_declared_count(self):
        for count, batches in [(4, {1: ["1.jpg", "2.jpg"], 3: ["2.jpg", "3.jpg"]}), (1, {1: ["1.jpg", "2.jpg"]})]:
            with self.subTest(count=count), patch.object(p.dm.Session, "get", new=self.dm_response(count, batches)):
                with self.assertRaisesRegex(RuntimeError, "完整|缺|页数"):
                    p.images("dm5", "https://www.dm5.com/m1001/")

    def copy_pages(self, batches):
        return [{"results": {"total": total, "list": [{"uuid": ident, "name": ident} for ident in values]}}
                for total, values in batches]

    def test_copy_overlap_missing_total_and_changing_total_are_not_complete(self):
        cases = [self.copy_pages([(4, ["a", "b"]), (4, ["b", "c"])]),
                 self.copy_pages([(2, ["a", "a"])]),
                 self.copy_pages([(3, ["a"]), (2, ["b"])]),
                 [{"results": {"list": [{"uuid": "a", "name": "a"}]}}],
                 self.copy_pages([(True, ["a"])]), self.copy_pages([(2, ["a"]), (2, [])])]
        for pages in cases:
            with self.subTest(pages=pages), patch.object(p.n, "copy_comic", return_value={"results": {"groups": {"default": {}}, "comic": {"name": "测试"}}}), patch.object(p.n, "_json", side_effect=pages):
                with self.assertRaisesRegex(RuntimeError, "分页|完整"):
                    p.copy_chapters_all("test")

    def test_copy_counts_are_per_group_and_complete_metadata_is_explicit(self):
        with patch.object(p.n, "copy_comic", return_value={"results": {"groups": {"default": {}, "extra": {}}, "comic": {"name": "测试"}}}), patch.object(p.n, "_json", side_effect=self.copy_pages([(2, ["a"]), (2, ["b"]), (1, ["c"])])):
            detail = p.details("mangacopy", "https://www.mangacopy.com/comic/test")
        self.assertEqual([r["group"] for r in detail["chapters"]], ["default", "default", "extra"])
        self.assertEqual(detail["catalogCompleteness"], "complete")

    def test_locked_kuaikan_complete_directory_has_independent_completeness(self):
        page = (FIXTURES / "apk-kuaikan/detail.html").read_text()
        with patch.object(kk, "_get", return_value=page):
            detail = p.details("kuaikan", "https://www.kuaikanmanhua.com/web/topic/2625/")
        self.assertEqual(detail["catalogCompleteness"], "complete")
        self.assertIn("部分章节", detail["unavailableReason"])
        self.assertTrue(any(c["locked"] for c in detail["chapters"]))

    def test_kuaikan_unknown_count_stays_unknown_and_invalid_counts_fail(self):
        base = {"topicInfo": {"id": 1}, "comics": [{"id": 11, "title": "第1话"}]}
        with patch.object(kk, "_get", return_value=state_page(base)):
            self.assertEqual(kk.details("kuaikan", "https://www.kuaikanmanhua.com/web/topic/1/")["catalogCompleteness"], "unknown")
        for count in (True, -1, 0, 2, "one"):
            data = deepcopy(base); data["topicInfo"]["comics_count"] = count
            with self.subTest(count=count), patch.object(kk, "_get", return_value=state_page(data)):
                with self.assertRaisesRegex(RuntimeError, "完整|数量|总数"):
                    kk.details("kuaikan", "https://www.kuaikanmanhua.com/web/topic/1/")

    def test_unverified_legacy_directory_does_not_claim_complete(self):
        page = (FIXTURES / "source-html/manhuazhijia-detail.html").read_text()
        page += '<a href="/chapter/10" class="chapter-item" title="第1话">第1话</a>'
        with patch.object(p.n, "_page", return_value=page):
            detail = p.details("manhuazhijia", "https://www.manhuazhijia.cc/comic/sanyuedeshizi")
        self.assertEqual(len(detail["chapters"]), 1)
        self.assertNotEqual(detail.get("catalogCompleteness"), "complete")


class SearchResponseTests(unittest.TestCase):
    def search(self, site, page):
        with patch.object(p.n, "_page", return_value=page), patch.object(p.gui, "_get", return_value=page), \
                patch.object(p.dm.Session, "get", return_value=(page.encode(), "", 200)):
            return Application().search_site(site, "测试")

    def test_seven_challenge_responses_are_errors_not_empty_results(self):
        for site in OLD_HTML_SITES:
            with self.subTest(site=site):
                self.assertRegex(self.search(site, CHALLENGE).get("error", ""), "验证|访问限制")

    def test_failed_search_is_not_cached_and_immediate_retry_can_succeed(self):
        app = Application()
        page = (FIXTURES / "source-html/baozimh-search.html").read_text()
        with patch.object(p.n, "_page", side_effect=[CHALLENGE, page]) as get:
            self.assertIn("error", app.search_site("baozimh", "三月的狮子"))
            self.assertEqual(len(app.cache.data), 0)
            result = app.search_site("baozimh", "三月的狮子")
        self.assertNotIn("error", result)
        self.assertEqual(len(result["results"]), 2)
        self.assertEqual(get.call_count, 2)

    def test_blank_and_changed_structures_are_errors_not_empty_results(self):
        for site in OLD_HTML_SITES:
            for page in ("", '<html><title>Source</title><main>Maintenance</main></html>', '<div class="results-renamed"><a href="/new-book/">测试</a></div>'):
                with self.subTest(site=site, page=page):
                    self.assertIn("error", self.search(site, page))

    def test_source_declared_zero_and_observed_empty_message_are_legitimate(self):
        # Controlled zero-count variants of the actual public result headings;
        # they test semantic zero, not the absence of parseable book links.
        pages = {
            "baozimh": '<div class="keyword-hinter"><span>相近搜尋結果(0)</span></div><div class="classify-items"></div>',
            "tuku": '<p class="search-tip-text">搜索: "测试" 共有0个结果</p>',
            "dm5": '<h1>“测试”相近搜索结果（0）</h1><ul class="mh-list"></ul>',
            "mangabz": '<div class="result-title">“测试”相近搜索結果（0）</div><ul class="mh-list"></ul>',
            "manben": '<div class="mainSearch"><div class="topBar">共找到0条 “测试” 相关的结果</div><div class="bookList_2"></div></div>',
            "manhuazhijia": (FIXTURES / "source-html/manhuazhijia-search-empty.html").read_text(),
            "manhuagui": '<html><title>搜索结果 - 漫画柜</title><main>没有找到相关漫画</main></html>',
        }
        for site, page in pages.items():
            with self.subTest(site=site):
                result = self.search(site, page)
                self.assertNotIn("error", result)
                self.assertEqual(result["results"], [])

    def test_positive_declared_count_without_parseable_rows_is_not_empty(self):
        for site, page in [("mangabz", '<div class="result-title">相近搜索結果（8）</div><ul class="mh-list"></ul>'),
                           ("manben", '<div class="mainSearch"><div class="topBar">共找到8条 相关的结果</div></div>')]:
            with self.subTest(site=site):
                self.assertIn("error", self.search(site, page))


class OfficialSemanticTests(unittest.TestCase):
    def test_terra_official_subtitle_reaches_unified_search_output(self):
        payload = json.loads((FIXTURES / "apk-official-catalogs/terra.json").read_text())
        with patch.object(official, "_get_json", return_value=payload["catalog"]):
            rows = p.search("terrahistoricus", "rhine lab")
        self.assertEqual(len(rows), 1)
        self.assertIn("RHINE LAB", rows[0]["matchedTitle"])
        self.assertIn(rows[0]["matchedTitle"], rows[0]["alternateTitles"])
        self.assertEqual(rows[0]["title"], "罗德岛源石记事——莱茵生命")

    def test_nami_localized_title_evidence_is_preserved_without_synopsis_inference(self):
        payload = json.loads((FIXTURES / "apk-official-catalogs/nami.json").read_text())["details"]["data"]
        payload["attributes"]["title"]["zh-Hans"] = "冒险者兼职"
        response = {"result": "ok", "type": "collection", "data": [payload], "meta": {"limit": 100, "offset": 0, "total": 1}}
        with patch.object(official, "_get_json", return_value=response):
            row = p.search("namicomi", "Part-Time Adventurer")[0]
        self.assertEqual(row["title"], "冒险者兼职")
        self.assertEqual(row["matchedTitle"], "Part-Time Adventurer")
        self.assertIn(row["matchedTitle"], row["alternateTitles"])

    def test_nami_sequences_preserve_every_language_chapter(self):
        payload = json.loads((FIXTURES / "apk-official-catalogs/nami.json").read_text())
        def get(site, path, params=()):
            return payload["directory"] if path == "/chapter" else payload["details"]
        with patch.object(official, "_get_json", side_effect=get):
            result = p.details("namicomi", "https://namicomi.com/en/title/Lx6v77NL/part-time-adventurer")
        self.assertEqual(result["catalogCompleteness"], "complete")
        rows = result["chapters"]
        self.assertEqual(len(rows), 54)
        self.assertEqual([c["sequenceId"] for c in rows], ["language:en"] * 27 + ["language:es-419"] * 27)
        self.assertEqual([c["language"] for c in rows], ["en"] * 27 + ["es-419"] * 27)

    def test_missing_language_does_not_join_a_known_language_sequence(self):
        payload = json.loads((FIXTURES / "apk-official-catalogs/nami.json").read_text())
        next(row for row in payload["directory"]["data"] if row["id"] == "dMJ4GDPS")["attributes"].pop("translatedLanguage")
        def get(site, path, params=()):
            return payload["directory"] if path == "/chapter" else payload["details"]
        with patch.object(official, "_get_json", side_effect=get):
            result = official.details("namicomi", "https://namicomi.com/en/title/Lx6v77NL/part-time-adventurer")
        unknown = next(c for c in result["chapters"] if c["id"] == "dMJ4GDPS")
        self.assertEqual(unknown["language"], "")
        self.assertEqual(unknown["sequenceId"], "language:unknown")
        self.assertEqual(len(result["chapters"]), 54)

    def test_unified_title_evidence_rejects_unsupported_or_nonstring_aliases(self):
        row = {"title": "作品", "url": "https://namicomi.com/en/title/12345678/test",
               "alternateTitles": ["Official Alias", None, "", "Official Alias", {"title": "not text"}],
               "matchedTitle": "Not in source title evidence"}
        with patch.object(official, "search", return_value=[row]):
            result = p.search("namicomi", "test")[0]
        self.assertEqual(result["alternateTitles"], ["Official Alias"])
        self.assertNotIn("matchedTitle", result)


if __name__ == "__main__":
    unittest.main()
