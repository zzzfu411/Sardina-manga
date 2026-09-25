"""Recorded public API shapes, with no live network or saved comic pictures."""
from copy import deepcopy
from http.client import IncompleteRead
import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request

from client import apk_official_catalogs as a


FIXTURES = Path(__file__).parent / "fixtures" / "apk-official-catalogs"
TERRA_BOOK = "https://comic.hypergryph.com/terra-historicus/comic/1421"
TERRA_CHAPTER = TERRA_BOOK + "/episode/7424"
NAMI_BOOK = "https://namicomi.com/en/title/Lx6v77NL/part-time-adventurer"
NAMI_CHAPTER = "https://namicomi.com/en/chapter/dMJ4GDPS"


class OfficialCatalogTests(unittest.TestCase):
    def setUp(self):
        self.terra = json.loads((FIXTURES / "terra.json").read_text())
        self.nami = json.loads((FIXTURES / "nami.json").read_text())
        self.calls = []

    def get(self, site, path, params=()):
        self.calls.append((site, path, params))
        if site == "terrahistoricus":
            if path == "/api/comic":
                return self.terra["catalog"]
            if path == "/api/comic/1421":
                return self.terra["details"]
            if path == "/api/comic/1421/episode/7424":
                return self.terra["episode"]
            if path == "/api/comic/1421/episode/7424/page":
                n = dict(params)["pageNum"]
                return {"code": 0, "data": {"pageNum": n, "url": f"https://res01.hycdn.cn/comic/pic/fixture/{n}.jpeg"}}
        if site == "namicomi":
            if path == "/title/search":
                return {"result": "ok", "type": "collection", "data": [self.nami["details"]["data"]],
                        "meta": {"limit": 100, "offset": 0, "total": 1}}
            if path == "/title/Lx6v77NL":
                return self.nami["details"]
            if path == "/chapter":
                return self.nami["directory"]
            if path == "/chapter/dMJ4GDPS":
                return self.nami["chapter"]
            if path == "/images/chapter/dMJ4GDPS":
                return self.nami["images"]
        raise AssertionError((site, path, params))

    def test_registered_sources_have_verified_hosts_and_only_observed_image_hosts(self):
        self.assertEqual(set(a.SOURCES), {"terrahistoricus", "namicomi"})
        self.assertEqual(set(a.IMAGE_DOMAINS), {"web.hycdn.cn", "res01.hycdn.cn", "uploads.namicomi.com"})
        self.assertEqual(a.HOST_ALIASES["terrahistoricus"], ("terra-historicus.hypergryph.com",))

    def test_terra_search_filters_finite_catalog_and_does_not_return_unrelated_works(self):
        with patch.object(a, "_get_json", side_effect=self.get):
            rows = a.search("terrahistoricus", "莱茵生命")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["title"], "罗德岛源石记事——莱茵生命")
            self.assertEqual(rows[0]["author"], "鹰角网络")
            self.assertEqual(rows[0]["url"], TERRA_BOOK)
            self.assertEqual(a.search("terrahistoricus", "一拳超人"), [])
            self.assertEqual(len(a.search("terrahistoricus", "rhine lab")), 1)
        self.assertEqual(dict(self.calls[0][2]), {"topicKey": "terra-historicus"})

    def test_terra_full_directory_preserves_preview_extras_leading_zero_ids_and_order(self):
        before = deepcopy(self.terra)
        with patch.object(a, "_get_json", side_effect=self.get):
            detail = a.details("terrahistoricus", TERRA_BOOK)
        chapters = detail["chapters"]
        self.assertEqual(detail["catalogCompleteness"], "complete")
        self.assertEqual(len(chapters), 13)
        self.assertEqual(chapters[0]["name"], "预告 过往荆棘，誓约不灭")
        self.assertEqual(chapters[1]["id"], "7424")
        self.assertEqual(chapters[4]["id"], "0210")
        self.assertEqual([c["name"] for c in chapters[-3:]], ["番外 · 01 回礼", "番外 · 02 愿望", "番外 · 03 Silence"])
        self.assertEqual([c["order"] for c in chapters], list(range(13)))
        self.assertEqual(self.terra, before)

    def test_terra_old_host_alias_canonicalizes_without_fetching_untrusted_public_html(self):
        with patch.object(a, "_get_json", side_effect=self.get):
            detail = a.details("terrahistoricus", "https://terra-historicus.hypergryph.com/comic/1421")
        self.assertEqual(detail["sourceUrl"], TERRA_BOOK)
        self.assertTrue(all(call[1].startswith("/api/comic/") for call in self.calls))

    def test_terra_full_page_sequence_uses_api_page_numbers_in_order(self):
        with patch.object(a, "_get_json", side_effect=self.get):
            urls = a.images("terrahistoricus", TERRA_CHAPTER)
        self.assertEqual(urls, [f"https://res01.hycdn.cn/comic/pic/fixture/{n}.jpeg" for n in (1, 2, 3)])
        pages = [dict(params)["pageNum"] for _, path, params in self.calls if path.endswith("/page")]
        self.assertEqual(sorted(pages), [1, 2, 3])

    def test_terra_wrong_work_or_duplicate_directory_is_rejected(self):
        for mutation in (lambda d: d.update(cid="1422"), lambda d: d["episodes"].append(d["episodes"][0])):
            with self.subTest(mutation=mutation):
                original = deepcopy(self.terra)
                mutation(self.terra["details"]["data"])
                with patch.object(a, "_get_json", side_effect=self.get), self.assertRaises(a.SourceError):
                    a.details("terrahistoricus", TERRA_BOOK)
                self.terra = original

    def test_terra_unknown_episode_is_rejected_before_page_api(self):
        with patch.object(a, "_get_json", side_effect=self.get), self.assertRaisesRegex(a.SourceError, "不属于"):
            a.images("terrahistoricus", TERRA_BOOK + "/episode/9999")
        self.assertEqual(len(self.calls), 1)

    def test_terra_mismatched_episode_or_page_number_is_rejected(self):
        self.terra["episode"]["data"]["title"] = "另一章节"
        with patch.object(a, "_get_json", side_effect=self.get), self.assertRaisesRegex(a.SourceError, "章节.*不一致"):
            a.images("terrahistoricus", TERRA_CHAPTER)
        self.terra["episode"]["data"]["title"] = "不速之客"
        def wrong_page(site, path, params=()):
            result = self.get(site, path, params)
            if path.endswith("/page"):
                result["data"]["pageNum"] += 1
            return result
        with patch.object(a, "_get_json", side_effect=wrong_page), self.assertRaisesRegex(a.SourceError, "页码"):
            a.images("terrahistoricus", TERRA_CHAPTER)

    def test_terra_failure_or_partial_page_is_never_silently_dropped(self):
        def missing_page(site, path, params=()):
            if path.endswith("/page") and dict(params)["pageNum"] == 2:
                return {"code": 403, "data": {"url": "https://res01.hycdn.cn/private.jpeg"}}
            return self.get(site, path, params)
        with patch.object(a, "_get_json", side_effect=missing_page), self.assertRaises(a.SourceError):
            a.images("terrahistoricus", TERRA_CHAPTER)

    def test_terra_foreign_page_host_and_malformed_group_do_not_become_readable(self):
        def foreign_page(site, path, params=()):
            reply = self.get(site, path, params)
            if path.endswith("/page"):
                reply["data"]["url"] = "https://foreign.test/private.jpeg"
            return reply
        with patch.object(a, "_get_json", side_effect=foreign_page), self.assertRaises(a.SourceError):
            a.images("terrahistoricus", TERRA_CHAPTER)
        self.terra["details"]["data"]["episodes"][0]["type"] = {"unexpected": 1}
        with patch.object(a, "_get_json", side_effect=self.get), self.assertRaises(a.SourceError):
            a.details("terrahistoricus", TERRA_BOOK)

    def test_nami_search_uses_current_title_parameter_and_named_creator_credit(self):
        with patch.object(a, "_get_json", side_effect=self.get):
            rows = a.search("namicomi", "PART-TIME ADVENTURER")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["author"], "Toxic-Ink Incorporated")
            self.assertEqual(rows[0]["url"], NAMI_BOOK)
            self.assertIn("3603cd20", rows[0]["cover"])
            self.assertEqual(a.search("namicomi", "totally unrelated"), [])
        params = dict(self.calls[0][2])
        self.assertEqual(params["title"], "PART-TIME ADVENTURER")
        self.assertNotIn("query", params)

    def test_nami_complete_translations_are_visible_and_never_interleaved(self):
        with patch.object(a, "_get_json", side_effect=self.get):
            detail = a.details("namicomi", NAMI_BOOK)
        chapters = detail["chapters"]
        self.assertEqual(len(chapters), 54)
        self.assertEqual([c["group"] for c in chapters], ["en"] * 27 + ["es-419"] * 27)
        self.assertEqual(chapters[0]["id"], "dMJ4GDPS")
        self.assertEqual(chapters[0]["name"], "第1章 Proposition · English")
        self.assertTrue(chapters[27]["name"].endswith("Español (Latinoamérica)"))
        self.assertEqual(chapters[26]["name"].split()[0], "第27章")
        self.assertEqual([c["order"] for c in chapters], list(range(54)))
        self.assertIn("语言", detail["sourceNotice"])

    def test_nami_unknown_language_retains_its_own_source_order(self):
        chapters = self.nami["directory"]["data"][:4]
        for i, c in enumerate(chapters):
            c["attributes"]["translatedLanguage"] = "x-test" if i % 2 == 0 else "en"
            c["attributes"]["chapter"] = str(10 - i)
        self.nami["directory"]["data"] = chapters
        self.nami["directory"]["meta"]["total"] = 4
        with patch.object(a, "_get_json", side_effect=self.get):
            result = a.details("namicomi", NAMI_BOOK)["chapters"]
        self.assertEqual([c["id"] for c in result], [chapters[i]["id"] for i in (1, 3, 0, 2)])
        self.assertTrue(result[-1]["name"].endswith("x-test"))

    def test_nami_pagination_follows_observed_offsets_until_full_total(self):
        all_rows = self.nami["directory"]["data"]
        offsets = []
        def paged(site, path, params=()):
            if path != "/chapter":
                return self.get(site, path, params)
            query = dict(params)
            self.assertEqual(query["titleIds[]"], "Lx6v77NL")
            self.assertEqual(query["order[natural]"], "asc")
            offset = query["offset"]
            offsets.append(offset)
            return {"result": "ok", "type": "collection", "data": all_rows[offset:offset + 32],
                    "meta": {"limit": 32, "offset": offset, "total": 54}}
        with patch.object(a, "_get_json", side_effect=paged):
            self.assertEqual(len(a.details("namicomi", NAMI_BOOK)["chapters"]), 54)
        self.assertEqual(offsets, [0, 32])

    def test_nami_wrong_title_chapter_and_parent_id_are_rejected(self):
        for part, key, bad in (("details", "id", "WrongID1"), ("chapter", "id", "WrongID2")):
            with self.subTest(part=part):
                original = self.nami[part]["data"][key]
                self.nami[part]["data"][key] = bad
                with patch.object(a, "_get_json", side_effect=self.get), self.assertRaisesRegex(a.SourceError, "标识不一致"):
                    (a.details("namicomi", NAMI_BOOK) if part == "details" else a.images("namicomi", NAMI_CHAPTER))
                self.nami[part]["data"][key] = original
        self.nami["directory"]["data"][0]["relationships"] = [{"type": "title", "id": "WrongID3"}]
        with patch.object(a, "_get_json", side_effect=self.get), self.assertRaisesRegex(a.SourceError, "不属于"):
            a.details("namicomi", NAMI_BOOK)

    def test_nami_repeated_missing_changed_and_excessive_pages_are_errors(self):
        base = {"result": "ok", "type": "collection", "data": self.nami["directory"]["data"][:2],
                "meta": {"limit": 2, "offset": 0, "total": 3}}
        repeated = deepcopy(base)
        repeated["meta"]["offset"] = 2
        missing = {**deepcopy(base), "data": [], "meta": {"limit": 2, "offset": 2, "total": 3}}
        changed = {**deepcopy(base), "meta": {"limit": 2, "offset": 2, "total": 5}}
        for second in (repeated, missing, changed):
            with self.subTest(second=second["meta"]), patch.object(a, "_get_json", side_effect=[base, second]):
                with self.assertRaises(a.SourceError):
                    a._nami_collection("/chapter", (), "chapter", 5000)
        excessive = {**base, "meta": {"limit": 100, "offset": 0, "total": 5001}}
        with patch.object(a, "_get_json", return_value=excessive), self.assertRaises(a.SourceError):
            a._nami_collection("/chapter", (), "chapter", 5000)

    def test_nami_images_use_verified_chapter_id_hash_and_complete_high_quality_list(self):
        with patch.object(a, "_get_json", side_effect=self.get):
            urls = a.images("namicomi", NAMI_CHAPTER)
        self.assertEqual(len(urls), 9)
        self.assertTrue(all(u.startswith("https://uploads.namicomi.com/chapter/dMJ4GDPS/4e709f789ccacef15c0261f503434d4b/high/") for u in urls))
        self.assertEqual(dict(self.calls[-1][2]), {"newQualities": "true"})
        self.assertEqual([c[1] for c in self.calls], ["/chapter/dMJ4GDPS", "/images/chapter/dMJ4GDPS"])

    def test_nami_permission_failure_never_constructs_images_or_retries_with_identity(self):
        errors = {"result": "error", "errors": [{"status": 402, "key": "error_payment_required"}]}
        with patch.object(a, "_get_json", side_effect=[self.nami["chapter"], errors]) as request:
            with self.assertRaises(a.AccessError):
                a.images("namicomi", NAMI_CHAPTER)
        self.assertEqual(request.call_count, 2)
        self.nami["chapter"]["data"]["attributes"]["gating"] = {"type": "paid"}
        with patch.object(a, "_get_json", side_effect=self.get) as request:
            with self.assertRaises(a.AccessError):
                a.images("namicomi", NAMI_CHAPTER)
        self.assertEqual(request.call_count, 1)

    def test_nami_unpublished_entities_are_not_treated_as_public(self):
        for attrs in ({"state": "draft"}, {"state": "published", "workInProgress": True}):
            with self.subTest(attrs=attrs):
                chapter = deepcopy(self.nami["chapter"])
                chapter["data"]["attributes"].update(attrs)
                with patch.object(a, "_get_json", return_value=chapter), self.assertRaises(a.AccessError):
                    a.images("namicomi", NAMI_CHAPTER)

    def test_nami_unsafe_cdn_hash_filename_partial_or_duplicate_images_are_rejected(self):
        modifications = [lambda d: d.update(baseUrl="https://foreign.test"),
                         lambda d: d.update(baseUrl="https://uploads.namicomi.com/private"),
                         lambda d: d.update(hash="../private"),
                         lambda d: d["high"][0].update(filename="../private.png"),
                         lambda d: d["high"][0].update(filename="%2e%2e%2fprivate.png"),
                         lambda d: d["high"].pop(),
                         lambda d: d["high"].__setitem__(1, d["high"][0])]
        for modify in modifications:
            with self.subTest(modify=modify):
                data = deepcopy(self.nami["images"])
                modify(data["data"])
                with patch.object(a, "_get_json", side_effect=[self.nami["chapter"], data]), self.assertRaises(a.SourceError):
                    a.images("namicomi", NAMI_CHAPTER)

    def test_empty_public_directory_remains_empty_with_explanation(self):
        self.terra["details"]["data"]["episodes"] = []
        self.nami["directory"].update(data=[], meta={"limit": 100, "offset": 0, "total": 0})
        with patch.object(a, "_get_json", side_effect=self.get):
            for site, url in (("terrahistoricus", TERRA_BOOK), ("namicomi", NAMI_BOOK)):
                result = a.details(site, url)
                self.assertEqual(result["chapters"], [])
                self.assertIn("公开", result["unavailableReason"])

    def test_url_boundaries_fail_before_network(self):
        bad_urls = ["http://namicomi.com/en/title/Lx6v77NL", "https://foreign.test/en/title/Lx6v77NL",
                    "https://namicomi.com.evil.test/en/title/Lx6v77NL", "https://user@namicomi.com/en/title/Lx6v77NL",
                    "https://namicomi.com:9000/en/title/Lx6v77NL", NAMI_BOOK + "?token=secret", NAMI_BOOK + "#part",
                    "https://namicomi.com/en/title/Lx6v77NL/%2e%2e", "https://namicomi.com/en/title/Lx6v77NL/%2fadmin",
                    "https://namicomi.com/en/title/invalid", "https://[bad/en/title/Lx6v77NL", NAMI_CHAPTER]
        with patch.object(a, "_get_json") as request:
            for url in bad_urls:
                with self.subTest(url=url), self.assertRaises(ValueError):
                    a.details("namicomi", url)
            with self.assertRaises(ValueError):
                a.details("terrahistoricus", "https://comic.hypergryph.com/talos-ii-historicus/comic/1421")
            request.assert_not_called()

    def test_redirect_boundaries_include_identity_and_https(self):
        url = "https://api.namicomi.com/chapter/dMJ4GDPS"
        guard = a._Redirect("namicomi")
        for target in ("https://foreign.test/chapter/dMJ4GDPS", "http://api.namicomi.com/chapter/dMJ4GDPS",
                       "https://api.namicomi.com/chapter/WrongID1", url + "?other=1", "https://api.namicomi.com:9000/chapter/dMJ4GDPS"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                guard.redirect_request(Request(url), None, 302, "Found", {}, target)
        self.assertEqual(guard.redirect_request(Request(url), None, 302, "Found", {}, url).full_url, url)

    def test_api_network_http_and_json_failures_are_explicit(self):
        url = "https://api.namicomi.com/chapter/dMJ4GDPS"
        for error in (HTTPError(url, 401, "private body", {}, None), HTTPError(url, 402, "private body", {}, None),
                      HTTPError(url, 403, "private body", {}, None), HTTPError(url, 500, "private body", {}, None),
                      URLError("offline"), TimeoutError(), IncompleteRead(b"partial")):
            opener = MagicMock()
            opener.open.side_effect = error
            with self.subTest(error=type(error).__name__), patch.object(a, "build_opener", return_value=opener):
                expected = a.AccessError if isinstance(error, HTTPError) and error.code in (401, 402, 403) else a.SourceError
                with self.assertRaises(expected) as raised:
                    a._get_json("namicomi", "/chapter/dMJ4GDPS")
                self.assertNotIn("private body", str(raised.exception))
        for body in (b"<html>login</html>", b"[]", b"[" * 3000 + b"0" + b"]" * 3000):
            response = MagicMock()
            response.__enter__.return_value = response
            response.geturl.return_value = url
            response.read.return_value = body
            opener = MagicMock()
            opener.open.return_value = response
            with self.subTest(body=body[:15]), patch.object(a, "build_opener", return_value=opener), self.assertRaises(a.SourceError):
                a._get_json("namicomi", "/chapter/dMJ4GDPS")

    def test_final_response_url_is_validated_even_if_redirect_handler_was_skipped(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.geturl.return_value = "https://foreign.test/api"
        response.read.return_value = b'{"result":"ok"}'
        opener = MagicMock()
        opener.open.return_value = response
        with patch.object(a, "build_opener", return_value=opener), self.assertRaises(a.SourceError):
            a._get_json("namicomi", "/chapter/dMJ4GDPS")
        response.read.assert_not_called()

    def test_non_permission_business_errors_are_not_mislabeled_as_payment(self):
        with patch.object(a, "_get_json", return_value={"result": "error", "errors": [{"status": 500}]}):
            with self.assertRaises(a.SourceError) as raised:
                a._nami_payload("/chapter/dMJ4GDPS")
        self.assertNotIsInstance(raised.exception, a.AccessError)

    def test_blank_query_and_unknown_sources_do_not_request_catalog(self):
        with patch.object(a, "_get_json") as request:
            self.assertEqual(a.search("terrahistoricus", "  "), [])
            self.assertEqual(a.search("namicomi", "---"), [])
            with self.assertRaises(ValueError):
                a.search("unknown", "a")
            request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
