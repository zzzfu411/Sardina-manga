import copy
from concurrent.futures import ThreadPoolExecutor
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from client import discovery, providers, recommendations as rec
from server import Application, Handler


def row(site, number=1, **extra):
    paths = {"hipmh": f"/manga/bTo{number}", "komiic": f"/comic/{number}",
             "manben": f"/mh-work-{number}/", "baozimh": f"/comic/work_{number}",
             "zaimanhua": f"/pages/comic/detail?id={number}", "mangacopy": f"/comic/work_{number}"}
    return {"siteId": site, "title": f"作品{number}", "author": f"作者{number}",
            "detailUrl": "https://" + providers.SOURCES[site][1] + paths.get(site, f"/comic/{number}/"),
            "coverUrl": "https://" + rec._COVERS[site][0] + f"/cover/{number}.jpg", **extra}


def feeds(count=40):
    return {site: {"siteId": site, "siteName": name, "kind": kind, "period": period, "page": page,
                   "items": [row(site, number) for number in range(1, count + 1)],
                   "sourceUrl": "https://" + providers.SOURCES[site][1] + "/", "fetchedAt": "2026-09-22T08:00:00Z"}
            for site, name, kind, period, page, label in rec._plan()[0]}


class RecommendationsTests(unittest.TestCase):
    def run_feeds(self, data, **kwargs):
        def read(site, kind, period, page):
            value = data[site]
            if isinstance(value, Exception):
                raise value
            return copy.deepcopy(value)
        reader = Mock(side_effect=read)
        return rec.fetch(fetch_feed=reader, **kwargs), reader

    def test_bounded_multi_source_pool_and_injected_discovery_cache_contract(self):
        data = feeds(60)
        untouched = copy.deepcopy(data)
        with patch.object(discovery, "fetch", side_effect=AssertionError("shared reader must be used")):
            result, reader = self.run_feeds(data)
        self.assertEqual(len(result["items"]), 200)
        self.assertEqual(result["candidateCount"], len(result["items"]))
        self.assertEqual([book["siteId"] for book in result["items"][:6]], list(rec._PREFERRED))
        self.assertEqual(reader.call_count, 6)
        self.assertEqual({call.args for call in reader.call_args_list},
                         {(site, kind, period, page) for site, _, kind, period, page, _ in rec._plan()[0]})
        self.assertEqual(result["warnings"], [])
        self.assertEqual(result["nextBatch"], 1)
        self.assertTrue(result["hasMore"])
        self.assertEqual(data, untouched)
        self.assertEqual({origin["siteId"] for origin in result["origins"]}, set(rec._PREFERRED))
        self.assertTrue(all(book["coverUrl"] and book["recommendationSourceUrl"] for book in result["items"]))
        self.assertTrue(all(sum(book["siteId"] == site for book in result["items"]) <= 40 for site in data))

    def test_rotation_uses_registered_modes_and_periods_without_crawling_pages(self):
        registered = discovery.sources()
        flattened = [config for batch in rec._plan() for config in batch]
        self.assertEqual({config[0] for config in flattened[:len(registered)]}, {source["siteId"] for source in registered})
        self.assertTrue(all(len(batch) <= 6 for batch in rec._plan()))
        self.assertEqual(len(flattened), len(set((site, kind, period, page) for site, _, kind, period, page, _ in flattened)))
        for site, _, kind, period, page, _ in flattened:
            self.assertEqual(discovery.normalize_request({"siteId": site, "kind": kind, "period": period, "page": page}), (site, kind, period, 1))
        for invalid in (-1, True, 1000, "0", len(rec._plan())):
            with self.assertRaises(ValueError):
                rec.fetch(batch=invalid, fetch_feed=Mock(side_effect=AssertionError("invalid input must not request")))

    def test_at_most_three_list_calls_run_concurrently(self):
        data = feeds()
        lock, barrier = threading.Lock(), threading.Barrier(3)
        active = maximum = calls = 0
        def read(site, *_):
            nonlocal active, maximum, calls
            with lock:
                active += 1; calls += 1; maximum = max(active, maximum)
            barrier.wait(timeout=2)
            with lock:
                active -= 1
            return data[site]
        result = rec.fetch(fetch_feed=read)
        self.assertEqual(maximum, 3)
        self.assertEqual(calls, 6)
        self.assertEqual(result["warnings"], [])

    def test_simultaneous_recommendation_requests_share_the_three_source_slots(self):
        data, lock, barrier = feeds(1), threading.Lock(), threading.Barrier(3)
        active = maximum = 0
        def read(site, *_):
            nonlocal active, maximum
            with lock:
                active += 1; maximum = max(active, maximum)
            barrier.wait(timeout=2)
            with lock:
                active -= 1
            return data[site]
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(rec.fetch, fetch_feed=read) for _ in range(2)]
            results = [future.result(timeout=5) for future in futures]
        self.assertEqual(maximum, 3)
        self.assertTrue(all(len(result["items"]) == 6 and not result["warnings"] for result in results))

    def test_deadline_returns_partial_results_and_cancels_queued_calls(self):
        data, release = feeds(), threading.Event()
        def read(site, *_):
            if site != "hipmh":
                release.wait(2)
            return data[site]
        try:
            with patch.object(rec, "_TOTAL_SECONDS", .03):
                result = rec.fetch(fetch_feed=read)
            self.assertEqual({book["siteId"] for book in result["items"]}, {"hipmh"})
            self.assertEqual(len(result["warnings"]), 5)
        finally:
            release.set()

    def test_same_title_different_authors_and_unknown_authors_all_survive_backend(self):
        data = feeds(2)
        for index, (site, value) in enumerate(data.items()):
            value["items"] = [row(site, 1, title="逆光", author=["甲作者", "乙作者", "", "丙作者", "甲作者", ""][index])]
        result, _ = self.run_feeds(data)
        self.assertEqual(len(result["items"]), 6)
        self.assertEqual({book["author"] for book in result["items"]}, {"甲作者", "乙作者", "丙作者", ""})
        self.assertEqual({book["siteId"] for book in result["items"]}, set(data))

    def test_duplicate_source_entries_do_not_manufacture_candidates(self):
        data = feeds(1)
        data["hipmh"]["items"] *= 5
        result, _ = self.run_feeds(data)
        self.assertEqual(len(result["items"]), 6)

    def test_partial_and_whole_batch_failures_preserve_progression_and_truthful_counts(self):
        data = feeds(3)
        for site in ("hipmh", "komiic", "manben"):
            data[site] = TimeoutError("来源暂时超时")
        result, reader = self.run_feeds(data)
        self.assertEqual(len(result["items"]), 9)
        self.assertEqual(len(result["warnings"]), 3)
        self.assertEqual(reader.call_count, 6)
        result, _ = self.run_feeds({site: RuntimeError("不可用") for site in data})
        self.assertEqual(result["items"], [])
        self.assertEqual(result["candidateCount"], 0)
        self.assertEqual(len(result["warnings"]), 6)
        self.assertEqual(result["nextBatch"], 1)

    def test_invalid_rows_and_forged_source_metadata_are_isolated(self):
        for mutation in ({"sourceUrl": "https://evil.example/"}, {"kind": "latest"}, {"siteId": "komiic"},
                         {"period": "day"}, {"page": 2}, {"page": True}, {"items": {}}, {"fetchedAt": None},
                         {"fetchedAt": "2026-09-22T10:00:00"}):
            data = feeds(2); data["hipmh"].update(mutation)
            result, _ = self.run_feeds(data)
            self.assertNotIn("hipmh", {book["siteId"] for book in result["items"]})
            self.assertEqual(len(result["warnings"]), 1)
        data = feeds(1)
        original = data["manben"]["items"][0]
        data["manben"]["items"] = [dict(original, **change) for change in (
            {"coverUrl": "https://mhfm.cdndm5.com.evil.test/a.jpg"}, {"coverUrl": "data:image/png,no"},
            {"coverUrl": "https://mhfm.cdndm5.com/placeholder.jpg"}, {"detailUrl": "https://www.manben.com/mh-ranklist/"},
            {"siteId": "komiic"}, {"detailUrl": "https://other.test/comic/1"})]
        result, _ = self.run_feeds(data)
        self.assertNotIn("manben", {book["siteId"] for book in result["items"]})
        self.assertIn("有效封面", result["warnings"][0])

    def test_image_domains_credentials_and_ports_remain_validated_per_source(self):
        self.assertTrue(rec._cover_url("komiic", "https://public.komiic.com/1.jpg"))
        for url in ("https://cf.mhgui.com:8443/1.jpg", "https://u:p@cf.mhgui.com/1.jpg", "https://cf.mhgui.com/1.jpg#foo",
                    "https://mhgui.com.evil.test/1.jpg", "https://evil-mhgui.com/1.jpg", "https://cf.mhgui.com/ bad.jpg"):
            self.assertFalse(rec._cover_url("manhuagui", url), url)
        self.assertFalse(rec._cover_url("komiic", "https://cf.mhgui.com/1.jpg"))

    def test_latest_never_inherits_rank_and_popularity_does_not_fabricate_rank(self):
        data = feeds(1)
        data["baozimh"]["items"][0]["rank"] = 99
        data["hipmh"]["items"][0]["rank"] = True
        data["komiic"]["items"][0]["rank"] = 2
        result, _ = self.run_feeds(data)
        books = {book["siteId"]: book for book in result["items"]}
        self.assertNotIn("rank", books["baozimh"])
        self.assertNotIn("rank", books["hipmh"])
        self.assertEqual(books["komiic"]["rank"], 2)

    def test_verified_desktop_list_hosts_remain_attributable_with_mobile_book_addresses(self):
        data = feeds(2)
        data["zaimanhua"]["sourceUrl"] = "https://manhua.zaimanhua.com/rank"
        result, _ = self.run_feeds(data)
        self.assertEqual(result["warnings"], [])
        self.assertIn("zaimanhua", {book["siteId"] for book in result["items"]})
        self.assertTrue(rec._source_url("manhuatai", "https://m.kanman.com/api/getsortlist/?productname=mht"))
        self.assertTrue(rec._source_url("shenmanhua", "https://m.kanman.com/api/getsortlist/?productname=smh"))
        self.assertFalse(rec._source_url("zaimanhua", "https://manhua.zaimanhua.com.evil.test/rank"))

    def test_discovery_and_recommendations_share_feed_cache_with_separate_composition_locks(self):
        data, app = feeds(2), Application()
        with patch.object(discovery, "fetch", side_effect=lambda site, *_: data[site]) as read:
            app.post("/api/discovery", {"siteId": "hipmh", "kind": "popular", "period": "", "page": 1})
            first = app.recommendations()
            self.assertEqual(sum(call.args[0] == "hipmh" for call in read.call_args_list), 1)
            self.assertEqual(read.call_count, 6)
            self.assertEqual(first, app.recommendations())
            self.assertEqual(read.call_count, 6)
            app.recommendations(refresh=True)
            self.assertEqual(read.call_count, 12)
        self.assertIsNot(app.cache, app.recommendation_cache)

    def test_recommendation_http_batch_query_routes_and_rejects_invalid_values(self):
        app = SimpleNamespace(recommendations=Mock(return_value={"items": []}))
        def request(path):
            handler = object.__new__(Handler)
            handler.path, handler.server = path, SimpleNamespace(app=app)
            handler.valid_host, handler.send = lambda: True, Mock()
            handler.do_GET()
            return handler.send.call_args.args
        self.assertEqual(request("/api/recommendations?batch=2&refresh=1")[0], 200)
        app.recommendations.assert_called_once_with(batch=2, refresh=True)
        for query in ("batch=-1", "batch=32", "batch=01", "batch=", "batch=true", "batch=1&batch=2", "batch=1&other=1"):
            with self.subTest(query=query):
                self.assertEqual(request("/api/recommendations?" + query)[0], 400)
        self.assertEqual(app.recommendations.call_count, 1)


if __name__ == "__main__":
    unittest.main()
