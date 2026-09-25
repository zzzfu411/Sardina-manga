"""Regression tests against excerpts captured from the source HTML, not parsed mocks."""
from pathlib import Path
import unittest
from unittest.mock import patch

from client import dm5_family as dm, providers as p
from client.html_metadata import detail_metadata

FIXTURES = Path(__file__).parent / "fixtures" / "source-html"


def fixture(name):
    return (FIXTURES / (name + ".html")).read_text()


class SearchHTMLTests(unittest.TestCase):
    def test_dm5_exact_banner_precedes_fuzzy_results(self):
        rows = dm.parse_search_html("dm5", fixture("dm5-search"), dm.SITES["dm5"]["origin"])
        self.assertEqual(rows[0]["title"], "三月的狮子")
        self.assertEqual(rows[0]["author"], "羽海野千花")
        self.assertEqual(rows[0]["status"], "连载中")
        self.assertIn("/3179/", rows[0]["cover"])
        self.assertEqual(rows[1]["title"], "狮子")
        self.assertEqual(rows[1]["author"], "Ohtaku")
        self.assertEqual(rows[1]["latest"], "第53话")
        self.assertEqual(rows[1]["status"], "完结")
        self.assertIn("cdndm5.com", rows[1]["cover"])

    def test_deduplicates_links_and_ignores_navigation_and_recommendations(self):
        page = fixture("dm5-search")
        noise = '<nav><h2 class="title"><a href="/manhua-navigation/" title="导航">导航</a></h2></nav>'
        duplicate = '<div class="banner_detail_form"><p class="title"><a href="/manhua-sanyuedeshizi/?ref=other">三月的狮子</a></p></div>'
        recommendation = '<ul class="mh-list"><li><div class="mh-item"><h2 class="title"><a href="/manhua-recommendation/">推荐</a></h2></div></li></ul>'
        rows = dm.parse_search_html("dm5", noise + page + duplicate + recommendation, dm.SITES["dm5"]["origin"])
        self.assertEqual(len(rows), 4)
        self.assertEqual(len({row["url"] for row in rows}), 4)
        self.assertFalse(any(row["title"] in {"导航", "推荐"} for row in rows))
        self.assertEqual(dm.parse_search_html("dm5", noise, dm.SITES["dm5"]["origin"]), [])

    def test_mangabz_cover_latest_and_unknown_author(self):
        rows = dm.parse_search_html("mangabz", fixture("mangabz-search"), dm.SITES["mangabz"]["origin"])
        book = rows[1]
        self.assertEqual(book["title"], "三月的獅子")
        self.assertEqual(book["latest"], "第222話")
        self.assertIn("cover.mangabz.com", book["cover"])
        self.assertEqual(book["author"], "")
        # “最新” describes an update, not an explicit ongoing status.
        self.assertEqual(book["status"], "")
        self.assertEqual(rows[2]["status"], "完結")

    def test_manben_uses_search_results_only(self):
        page = fixture("manben-search") + '<div class="bookList_2"><div class="item"><p class="title"><a href="/mh-noise/">推荐作品</a></p></div></div>'
        rows = dm.parse_search_html("manben", page, dm.SITES["manben"]["origin"])
        self.assertEqual([row["title"] for row in rows], ["小狮子Leo", "三界志", "梦三国"])
        self.assertTrue(rows[0]["cover"])
        self.assertIn("小狮子leo", rows[0]["description"])
        self.assertEqual(rows[0]["author"], "")

    def test_source_adapter_preserves_real_fields_in_one_request(self):
        with patch.object(dm.Session, "get", return_value=(fixture("dm5-search").encode(), "", 200)) as get:
            rows = p.search("dm5", "三月的狮子")
        self.assertEqual(get.call_count, 1)
        self.assertEqual(rows[0]["siteId"], "dm5")
        self.assertEqual(rows[0]["author"], "羽海野千花")
        self.assertTrue(rows[0]["coverUrl"])
        self.assertEqual(rows[1]["latestChapter"], "第53话")

    def test_baozi_author_and_cover_entities(self):
        with patch.object(p.n, "_page", return_value=fixture("baozimh-search")) as get:
            rows = p.search("baozimh", "三月的狮子")
        self.assertEqual(get.call_count, 1)
        self.assertEqual(rows[0]["author"], "羽海野千花")
        self.assertIn("&h=375", rows[0]["coverUrl"])
        self.assertNotIn("&amp;", rows[0]["coverUrl"])


class DetailHTMLTests(unittest.TestCase):
    def test_observed_metadata_across_html_sources(self):
        expected = {
            "baozimh": ("三月的獅子", "羽海野千花", "連載中"),
            "manhuazhijia": ("三月的狮子", "羽海野千花", ""),
            "manhuagui": ("3月的狮子", "羽海野千花", "连载中"),
            "tuku": ("三月的狮子", "羽海野千花", "连载中"),
            "rumanhua": ("三月的狮子", "羽海野千花", ""),
            "dm5": ("三月的狮子", "羽海野千花", "连载中"),
            "mangabz": ("三月的獅子", "羽海野千花", "連載中"),
            "manben": ("小狮子Leo", "Leo", "连载中"),
        }
        for site, values in expected.items():
            with self.subTest(site=site):
                meta = detail_metadata(site, fixture(site + "-detail"), "https://" + p.SOURCES[site][1] + "/")
                self.assertEqual((meta["title"], meta["author"], meta["status"]), values)
                self.assertTrue(meta["description"])
                self.assertTrue(meta["coverUrl"].startswith("https://"))
                self.assertNotIn("简介：", meta["description"])

    def test_no_site_author_or_sidebar_status_as_book_metadata(self):
        page = '<meta name="Author" content="漫画网站"><aside><p>作者：推荐作者</p><p>状态：已完结</p></aside><h1>登录</h1>'
        for site in ("dm5", "manben", "manhuazhijia", "tuku"):
            with self.subTest(site=site):
                meta = detail_metadata(site, page, "https://example.test/")
                self.assertEqual(meta, dict.fromkeys(("title", "author", "description", "coverUrl", "status"), ""))

    def test_detail_metadata_reuses_the_chapter_http_response(self):
        page = fixture("manhuazhijia-detail") + '<a href="/chapter/20" class="chapter-item" title="第二话">第二话</a><a href="/chapter/10" class="chapter-item" title="第一话">第一话</a>'
        with patch.object(p.n, "_page", return_value=page) as get:
            data = p.details("manhuazhijia", "https://www.manhuazhijia.cc/comic/sanyuedeshizi")
        self.assertEqual(get.call_count, 1)
        self.assertEqual(data["title"], "三月的狮子")
        self.assertEqual(data["author"], "羽海野千花")
        self.assertEqual([chapter["name"] for chapter in data["chapters"]], ["第一话", "第二话"])

    def test_removed_dm5_directory_does_not_use_other_books_chapters(self):
        page = fixture("dm5-detail") + fixture("dm5-search")
        with patch.object(dm.Session, "get", return_value=(page.encode(), "", 200)) as get:
            data = p.details("dm5", "https://www.dm5.com/manhua-sanyuedeshizi/")
        self.assertEqual(get.call_count, 1)
        self.assertEqual(data["chapters"], [])
        self.assertIn("下架", data["unavailableReason"])
        self.assertEqual(data["title"], "三月的狮子")

    def test_available_dm5_directory_excludes_recommendations(self):
        page = fixture("dm5-available-directory")
        rows, reason = dm.parse_directory_html("dm5", page)
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0]["url"], "https://www.dm5.com/m463652/")
        self.assertEqual(rows[1]["url"], "https://www.dm5.com/m463653/")
        self.assertEqual(rows[-1]["url"], "https://www.dm5.com/m463655/")
        self.assertEqual(reason, "")
        self.assertFalse(any("460440" in row["url"] for row in rows))

    def test_manben_blocked_directory_has_explicit_source_reason(self):
        page = '<div class="banForm"><p>章节数据缺少，暂时作屏蔽处理</p></div><aside><a href="/m123/">推荐作品</a></aside>'
        rows, reason = dm.parse_directory_html('manben', page)
        self.assertEqual(rows, [])
        self.assertIn('屏蔽', reason)

    def test_copy_author_and_status_are_optional_strings(self):
        with patch.object(p.n, "copy_comic", return_value={"results": {"comic": {"name": "测试", "author": [{"name": "甲"}, {"name": "乙"}], "status": {"display": "连载中"}}}}), patch.object(p.n, "_json", return_value={"results": {"list": [], "total": 0}}):
            data = p.details("mangacopy", "https://www.mangacopy.com/comic/test")
        self.assertEqual(data["author"], "甲 / 乙")
        self.assertEqual(data["status"], "连载中")


if __name__ == "__main__":
    unittest.main()
