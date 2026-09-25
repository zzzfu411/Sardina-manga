"""Real public discovery excerpts; no live network, images, or image tickets."""
from copy import deepcopy
from html import escape
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from client import discovery_api as d, providers


FIXTURES = Path(__file__).parent / "fixtures" / "discovery-api"


def fixture(name):
    return (FIXTURES / name).read_text()


def payload(name):
    return json.loads(fixture(name + ".json"))


class DiscoveryApiSourcesTests(unittest.TestCase):
    def test_capabilities_report_only_verified_modes_and_periods(self):
        sources = {source["siteId"]: source for source in d.sources()}
        self.assertEqual(set(sources), {"hipmh", "komiic", "mangacopy", "sundaywebry", "terrahistoricus", "namicomi"})
        self.assertTrue(all(source["coverLookup"] is False for source in sources.values()))
        self.assertEqual([mode["kind"] for mode in sources["terrahistoricus"]["modes"]], ["latest"])
        self.assertEqual([p["id"] for p in sources["komiic"]["modes"][0]["periods"]], ["month", "total"])
        self.assertEqual(len(sources["mangacopy"]["modes"][0]["periods"]), 8)
        for site in ("sundaywebry", "namicomi", "terrahistoricus"):
            self.assertTrue(all(mode["maxPage"] == 1 for mode in sources[site]["modes"]))
        sources["komiic"]["modes"][0]["periods"].clear()
        self.assertEqual(len(d.sources()[1]["modes"][0]["periods"]), 2)

    def test_invalid_selection_never_requests_remote_data(self):
        cases = [("missing", "popular", "", 1), ("terrahistoricus", "popular", "", 1),
                 ("komiic", "popular", "day", 1), ("hipmh", "latest", "", 2),
                 ("sundaywebry", "latest", "", 2), ("mangacopy", "popular", "male-day", 2),
                 ("mangacopy", "latest", "day", 1), ("namicomi", "popular", "month", 1)]
        cases.extend(("komiic", "latest", "", value) for value in (True, "1", 0, -1, 1.5, 1001, None, []))
        with patch.object(d, "read_text") as html, patch.object(d, "read_json") as api:
            for selection in cases:
                with self.subTest(selection=selection), self.assertRaises(ValueError):
                    d.fetch(*selection)
        html.assert_not_called()
        api.assert_not_called()

    def test_hip_rank_preserves_source_ranks_and_existing_reader_identity(self):
        items, more = d._hip(fixture("hip-popular.html"), "popular", 1)
        self.assertEqual([row["rank"] for row in items], [1, 2, 3])
        self.assertEqual(items[0]["title"], "一人之下")
        self.assertEqual(items[0]["detailUrl"], "https://reader.hipmh.top/manga/bToyMzQ3NQ")
        self.assertTrue(more)
        self.assertTrue(all(row["coverUrl"].startswith("https://cover.s3imgs.top/") for row in items))
        for row in items:
            providers.validate_url("hipmh", row["detailUrl"])

    def test_hip_updates_are_scoped_and_keep_source_age_without_freshening(self):
        source = fixture("hip-latest.html") + fixture("hip-popular.html")
        items, more = d._hip(source, "latest", 1)
        self.assertEqual(len(items), 10)
        self.assertFalse(more)
        self.assertEqual(items[0]["title"], "与盲眼公爵订婚了")
        self.assertEqual(items[0]["updatedAtText"], "2周前")
        self.assertNotIn("rank", items[0])

    def test_hip_rejects_stale_page_and_wrong_next_page(self):
        source = fixture("hip-popular.html")
        with self.assertRaisesRegex(d.DiscoveryError, "所选榜单页"):
            d._hip(source, "popular", 2)
        with self.assertRaisesRegex(d.DiscoveryError, "没有前进"):
            d._hip(source.replace("/popularity?page=2", "/popularity?page=1"), "popular", 1)
        with self.assertRaises(d.DiscoveryError):
            d._hip(source.replace('class="rank-badge rank-badge-1"', 'class="missing"'), "popular", 1)

    def test_komiic_uses_observed_metadata_query_and_ascending_flag(self):
        for kind, period, name, field, order, page in [
            ("popular", "month", "komiic-hot", "hotComics", "MONTH_VIEWS", 1),
            ("popular", "total", "komiic-total", "hotComics", "VIEWS", 1),
            ("latest", "", "komiic-page2", "recentUpdate", "DATE_UPDATED", 2),
        ]:
            with self.subTest(kind=kind, period=period), patch.object(d, "read_json", return_value=payload(name)) as query:
                result = d.fetch("komiic", kind, period, page)
            self.assertEqual(len(result["items"]), 20)
            self.assertTrue(result["hasMore"])
            self.assertTrue(all("rank" not in row and row["coverUrl"] for row in result["items"]))
            self.assertEqual(query.call_args.args[0], "https://komiic.com/api/query")
            request = json.loads(query.call_args.kwargs["data"])
            self.assertEqual(request["variables"]["pagination"], {"limit": 20, "offset": (page - 1) * 20,
                                                                  "orderBy": order, "asc": True})
            self.assertIn(field, request["query"])
            self.assertNotIn("Image", request["query"])
            self.assertNotIn("Ticket", request["query"])
            self.assertNotIn("mutation", request["query"])
            self.assertEqual(result["sourceUrl"], "https://komiic.com/" + ("hot" if kind == "popular" else "updates"))

    def test_komiic_recorded_values_verify_actual_descending_order_and_page_boundary(self):
        for name, field, key in [("komiic-hot", "hotComics", "monthViews"), ("komiic-total", "hotComics", "views"),
                                 ("komiic-recent", "recentUpdate", "dateUpdated"), ("komiic-page2", "recentUpdate", "dateUpdated")]:
            values = [row[key] for row in payload(name)["data"][field]]
            self.assertEqual(values, sorted(values, reverse=True))
        first, second = (payload(name)["data"]["recentUpdate"] for name in ("komiic-recent", "komiic-page2"))
        self.assertGreater(first[-1]["dateUpdated"], second[0]["dateUpdated"])
        self.assertFalse({row["id"] for row in first} & {row["id"] for row in second})

    def test_komiic_partial_graphql_data_and_repeated_books_are_errors(self):
        good = payload("komiic-hot")
        bad = [None, {"errors": [{"message": "denied"}], **good}, {"data": {}}, {"data": {"hotComics": {}}}]
        repeated = deepcopy(good)
        repeated["data"]["hotComics"][1] = repeated["data"]["hotComics"][0]
        bad.append(repeated)
        for value in bad:
            with self.subTest(value=value), patch.object(d, "read_json", return_value=value), self.assertRaises(d.DiscoveryError):
                d.fetch("komiic", "popular", "month", 1)

    def test_komiic_empty_terminal_page_stays_empty_without_recommendation_fallback(self):
        with patch.object(d, "read_json", return_value={"data": {"recentUpdate": []}}):
            result = d.fetch("komiic", "latest", "", 2)
        self.assertEqual(result["items"], [])
        self.assertFalse(result["hasMore"])

    def test_copy_rank_has_real_covers_ranks_and_author(self):
        rows, more = d._copy(fixture("copy-popular.html"), "popular", "male-day", 1)
        self.assertEqual([row["rank"] for row in rows], [1, 2, 3])
        self.assertEqual(rows[0]["author"], "月見ハク")
        self.assertTrue(rows[0]["coverUrl"].endswith("1777035877.jpg.328x422.jpg"))
        self.assertFalse(more)
        with self.assertRaisesRegex(d.DiscoveryError, "所选榜单"):
            d._copy(fixture("copy-popular.html"), "popular", "female-week", 1)

    def test_copy_updates_parse_bounded_template_data_and_verified_pagination(self):
        rows, more = d._copy(fixture("copy-latest.html"), "latest", "", 1)
        self.assertEqual(len(rows), 50)
        self.assertTrue(more)
        self.assertEqual(rows[0]["title"], "以為是第二次人生，沒想到其實是第三次。")
        self.assertEqual(rows[0]["author"], "take4 / 麦こうちゃ")
        self.assertTrue(all(row["coverUrl"] and "rank" not in row for row in rows))
        with self.assertRaisesRegex(d.DiscoveryError, "所选更新排序和页码"):
            d._copy(fixture("copy-latest.html"), "latest", "", 2)
        with self.assertRaises(d.DiscoveryError):
            d._copy(fixture("copy-latest.html").replace("-datetime_updated", "-datetime_created"), "latest", "", 1)

    def test_copy_literal_data_never_evaluates_code(self):
        source = fixture("copy-latest.html")
        root = d._tree(source)
        old = root.first(cls="exemptComic-box").attrs["list"]
        for replacement in ["__import__('os').getcwd()", "[None] * 999999999", "x" * 400001]:
            changed = source.replace(escape(old, quote=True), escape(replacement, quote=True))
            self.assertNotEqual(source, changed)
            with self.assertRaises(d.DiscoveryError):
                d._copy(changed, "latest", "", 1)

    def test_sunday_rank_and_actual_updates_are_separate_and_preserve_episode_titles(self):
        popular = d._sunday(fixture("sunday.html"), "popular")
        latest = d._sunday(fixture("sunday.html"), "latest")
        self.assertEqual(len(popular), 8)
        self.assertEqual(len(latest), 16)
        self.assertEqual(latest[0]["title"], "吉田だより")
        self.assertEqual(latest[0]["latestChapter"], "第1話 吉田のいる教室")
        self.assertTrue(all("spacer.png" not in row["coverUrl"] and row["coverUrl"] for row in popular + latest))
        self.assertTrue(all("rank" not in row for row in popular))
        for row in popular + latest:
            providers.validate_url("sundaywebry", row["detailUrl"])

    def test_nami_popular_scopes_hot_titles_without_fabricated_rank_or_cycle(self):
        items = d._nami(fixture("nami-popular.html"), "popular")
        self.assertEqual(len(items), 18)
        self.assertEqual(items[0]["title"], "My Blossoming Summer")
        self.assertTrue(all("rank" not in row and row["coverUrl"] for row in items))
        for row in items:
            providers.validate_url("namicomi", row["detailUrl"])

    def test_nami_updates_collapse_each_books_chapters_and_keep_gated_metadata(self):
        items = d._nami(fixture("nami-latest.html"), "latest")
        self.assertEqual(len(items), 3)
        self.assertEqual([row["latestChapter"] for row in items], ["Ch. 160", "Ch. 67", "Ch. 42"])
        self.assertEqual(items[-1]["title"], "Lovestuck")
        self.assertEqual(items[0]["updatedAtText"], "2026-09-22T05:36:18.000Z")
        self.assertTrue(all("/title/" in row["detailUrl"] for row in items))

    def test_terra_latest_uses_actual_episode_updates_and_deduplicates_work(self):
        source = payload("terra-recent")
        self.assertEqual(len(source["data"]), 4)
        items = d._terra(source)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["latestChapter"], "「空弦」篇")
        self.assertEqual(items[0]["updatedAtText"], "2026-09-18")
        self.assertEqual(items[0]["coverUrl"], source["data"][0]["coverUrl"])
        self.assertEqual(items[0]["detailUrl"], "https://comic.hypergryph.com/terra-historicus/comic/6253")
        reversed_updates = deepcopy(source)
        reversed_updates["data"].reverse()
        with self.assertRaisesRegex(d.DiscoveryError, "顺序"):
            d._terra(reversed_updates)

    def test_every_html_mode_uses_one_list_request_and_returns_public_page_link(self):
        cases = [("hipmh", "popular", "", "hip-popular.html"), ("hipmh", "latest", "", "hip-latest.html"),
                 ("mangacopy", "popular", "male-day", "copy-popular.html"), ("mangacopy", "latest", "", "copy-latest.html"),
                 ("sundaywebry", "popular", "", "sunday.html"), ("sundaywebry", "latest", "", "sunday.html"),
                 ("namicomi", "popular", "", "nami-popular.html"), ("namicomi", "latest", "", "nami-latest.html")]
        for site, kind, period, name in cases:
            with self.subTest(site=site, kind=kind), patch.object(d, "read_text", return_value=fixture(name)) as request, \
                    patch.object(d, "read_json") as api:
                result = d.fetch(site, kind, period, 1)
            request.assert_called_once()
            api.assert_not_called()
            self.assertTrue(all(row["siteId"] == site and row["siteName"] for row in result["items"]))
            self.assertIn("deadline", request.call_args.kwargs)
            self.assertIn("hosts", request.call_args.kwargs)
            self.assertEqual(result["sourceUrl"], request.call_args.args[0])

    def test_page_cap_does_not_advertise_an_unreachable_next_page(self):
        with patch.object(d, "_komiic", return_value=([{"title": "x", "detailUrl": "https://komiic.com/comic/1"}], True)):
            with self.assertRaisesRegex(d.DiscoveryError, "页数上限"):
                d.fetch("komiic", "latest", "", 1000)

    def test_book_links_reject_other_sources_chapters_and_unsafe_hosts(self):
        invalid = [("mangacopy", "https://evil.test/comic/test"), ("mangacopy", "/comic/book/chapter/abc"),
                   ("komiic", "/comic/1/chapter/2"), ("komiic", "https://user@komiic.com/comic/1"),
                   ("hipmh", "/works/not-a-valid-token"), ("namicomi", "/en/chapter/abcdefgh"),
                   ("terrahistoricus", "/terra-historicus/comic/6253/episode/5734"),
                   ("sundaywebry", "https://www.sunday-webry.com/episode/1?access=1")]
        for site, url in invalid:
            with self.subTest(site=site, url=url), self.assertRaises(d.DiscoveryError):
                d._book(site, url)

    def test_unsafe_or_missing_covers_stay_missing_without_guessed_urls(self):
        for value in ("", "javascript:alert(1)", "https://evil.test/x.jpg", "https://public.komiic.com.evil.test/x.jpg"):
            row = d._item("komiic", "作品", "/comic/1", value)
            self.assertEqual(row["coverUrl"], "")

    def test_missing_or_challenged_source_sections_are_errors(self):
        for parser in (lambda source: d._hip(source, "latest", 1), lambda source: d._copy(source, "popular", "male-day", 1),
                       lambda source: d._sunday(source, "popular"), lambda source: d._nami(source, "popular")):
            for source in ("<main>Unavailable</main>", "<title>Just a moment...</title>"):
                with self.subTest(source=source), self.assertRaises(d.DiscoveryError):
                    parser(source)


if __name__ == "__main__":
    unittest.main()
