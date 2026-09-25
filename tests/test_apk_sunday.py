"""Observed Sunday GraphQL/HTML fixtures and controlled boundary variations."""
from copy import deepcopy
from html import escape
from io import BytesIO
import json
from pathlib import Path
import ssl
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, Request

from client import apk_sunday as sunday
from client.html_metadata import parse_html

FIXTURES = Path(__file__).parent / "fixtures" / "apk-sunday"
SITE = "sundaywebry"
EPISODE = "3269754496548998088"
SERIES = "3269754496548915270"
URL = sunday.ORIGIN + "/episode/" + EPISODE
RESTRICTED = sunday.ORIGIN + "/episode/3270375685397882999"
ENDPOINT = sunday.ORIGIN + "/graphql"


def fixture(name):
    path = FIXTURES / name
    return json.loads(path.read_text()) if path.suffix == ".json" else path.read_text()


def permission_parts():
    root = parse_html(fixture("viewer-free.html"))
    return (json.loads(root.first("html").attrs["data-gtm-data-layer"]),
            json.loads(root.first("script", ident="episode-json").attrs["data-value"]))


def permission_html(layer, value):
    return ('<html data-route="core:viewer" data-gtm-data-layer="' + escape(json.dumps(layer), quote=True)
            + '"><script id="episode-json" type="text/json" data-value="'
            + escape(json.dumps(value), quote=True) + '"></script></html>')


class Response(BytesIO):
    def __init__(self, value, url):
        if not isinstance(value, (bytes, str)):
            value = json.dumps(value)
        super().__init__(value.encode() if isinstance(value, str) else value)
        self.url = url

    def geturl(self):
        return self.url


class SundayTests(unittest.TestCase):
    def transport(self, sequence):
        pending = list(sequence)
        opener = Mock()

        def read(request, timeout):
            self.assertGreater(timeout, 0)
            self.assertLessEqual(timeout, 15)
            self.assertTrue(request.full_url.startswith(sunday.ORIGIN + "/"))
            self.assertIsNone(request.get_header("Authorization"))
            self.assertIsNone(request.get_header("Cookie"))
            self.assertEqual(request.get_header("Origin"), sunday.ORIGIN)
            self.assertTrue(pending, "Unexpected extra network request")
            return Response(pending.pop(0), request.full_url)

        opener.open.side_effect = read
        return opener

    def test_live_search_maps_official_episode_anchor_and_signed_cover(self):
        opener = self.transport([fixture("search.json")])
        with patch.object(sunday, "build_opener", return_value=opener) as build:
            result = sunday.search(SITE, "名探偵コナン")
        self.assertEqual(len(result), 4)
        self.assertEqual(result[0]["title"], "名探偵コナン")
        self.assertEqual(result[0]["url"], URL)
        self.assertEqual(result[0]["author"], "青山剛昌")
        self.assertTrue(result[0]["cover"].endswith("?1700553648"))
        self.assertEqual(result[0]["latest"], "3. FILE.3 仲間はずれの名探偵")
        self.assertEqual(result[0]["status"], "")
        self.assertEqual(opener.open.call_count, 1)  # no per-result detail/image requests
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.full_url, ENDPOINT)
        body = json.loads(request.data)
        self.assertEqual(body["variables"], {"q": "名探偵コナン", "after": None, "first": 100})
        self.assertIn("types: [SERIES]", body["query"])
        tls = next(handler for handler in build.call_args.args if isinstance(handler, HTTPSHandler))
        self.assertEqual(tls._context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(tls._context.check_hostname)

    def test_search_follows_opaque_cursors_and_deduplicates_ids(self):
        first, second = fixture("search.json"), fixture("search.json")
        all_rows = deepcopy(first["data"]["search"]["edges"])
        first["data"]["search"] = {"edges": all_rows[:2], "pageInfo": {"hasNextPage": True, "endCursor": "opaque_X="}}
        second["data"]["search"] = {"edges": all_rows[1:], "pageInfo": {"hasNextPage": False, "endCursor": "done"}}
        opener = self.transport([first, second])
        with patch.object(sunday, "build_opener", return_value=opener):
            result = sunday.search(SITE, "コナン")
        self.assertEqual(len(result), 4)
        second_vars = json.loads(opener.open.call_args_list[1].args[0].data)["variables"]
        self.assertEqual(second_vars["after"], "opaque_X=")

    def test_valid_empty_search_is_distinct_from_partial_error(self):
        empty = {"data": {"search": {"edges": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}}}
        with patch.object(sunday, "build_opener", return_value=self.transport([empty])):
            self.assertEqual(sunday.search(SITE, "不存在的作品"), [])
        partial = fixture("search.json")
        partial["errors"] = [{"message": "private token must not be echoed"}]
        with patch.object(sunday, "build_opener", return_value=self.transport([partial])), self.assertRaises(RuntimeError) as error:
            sunday.search(SITE, "コナン")
        self.assertNotIn("private token", str(error.exception))

    def test_live_directory_uses_one_query_and_real_episode_number_order(self):
        opener = self.transport([fixture("directory.json")])
        with patch.object(sunday, "build_opener", return_value=opener):
            result = sunday.details(SITE, URL)
        self.assertEqual(opener.open.call_count, 1)
        self.assertEqual(result["title"], "名探偵コナン")
        self.assertEqual(result["author"], "青山剛昌")
        self.assertEqual(len(result["chapters"]), 22)  # do not invent missing episode numbers
        self.assertEqual(result["catalogCompleteness"], "complete")
        self.assertEqual(result["chapters"][0]["url"], URL)
        self.assertTrue(result["chapters"][0]["name"].startswith("1."))
        self.assertTrue(result["chapters"][-1]["name"].startswith("172."))
        self.assertEqual([row["order"] for row in result["chapters"]], list(range(22)))
        self.assertTrue(all("locked" not in row and "_number" not in row for row in result["chapters"]))
        self.assertEqual(result["sourceNotice"], sunday.SOURCE_NOTICES[SITE])
        self.assertEqual(result["sourceUrl"], URL)
        self.assertEqual(result["status"], "")  # no guessed publication status

    def test_directory_paginates_all_rows_and_rejects_cross_work_response(self):
        first, second = fixture("directory.json"), fixture("directory.json")
        a = first["data"]["episode"]["series"]["episodes"]
        b = second["data"]["episode"]["series"]["episodes"]
        a["edges"], b["edges"] = a["edges"][:20], b["edges"][19:]
        a["pageInfo"] = {"hasNextPage": True, "endCursor": "MTk"}
        opener = self.transport([first, second])
        with patch.object(sunday, "build_opener", return_value=opener):
            result = sunday.details(SITE, URL)
        self.assertEqual(len(result["chapters"]), 22)
        self.assertEqual(opener.open.call_count, 2)
        self.assertEqual(json.loads(opener.open.call_args_list[1].args[0].data)["variables"]["after"], "MTk")
        second["data"]["episode"]["series"]["databaseId"] = "99999"
        with patch.object(sunday, "build_opener", return_value=self.transport([first, second])), self.assertRaisesRegex(RuntimeError, "其他作品"):
            sunday.details(SITE, URL)

    def test_incomplete_looping_or_excess_directory_never_returns_partial_rows(self):
        first = fixture("directory.json")
        first["data"]["episode"]["series"]["episodes"]["pageInfo"] = {"hasNextPage": True, "endCursor": "again"}
        variants = []
        repeat = deepcopy(first)
        variants.append(repeat)
        empty = deepcopy(first)
        empty["data"]["episode"]["series"]["episodes"] = {"edges": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}
        variants.append(empty)
        missing = deepcopy(first)
        del missing["data"]["episode"]["series"]["episodes"]["pageInfo"]
        variants.append(missing)
        bad = deepcopy(first)
        bad["data"]["episode"]["series"]["episodes"]["edges"][0]["node"] = None
        variants.append(bad)
        for second in variants:
            with self.subTest(second=second), patch.object(sunday, "build_opener", return_value=self.transport([first, second])), self.assertRaises(RuntimeError):
                sunday.details(SITE, URL)
        with patch.object(sunday, "MAX_DIRECTORY_PAGES", 1), patch.object(sunday, "build_opener", return_value=self.transport([first])), self.assertRaisesRegex(RuntimeError, "未返回部分目录"):
            sunday.details(SITE, URL)

    def test_empty_directory_gets_explicit_source_notice(self):
        data = fixture("directory.json")
        data["data"]["episode"]["series"]["episodes"] = {"edges": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}
        with patch.object(sunday, "build_opener", return_value=self.transport([data])):
            result = sunday.details(SITE, URL)
        self.assertEqual(result["chapters"], [])
        self.assertIn("没有返回", result["unavailableReason"])

    def test_free_images_first_check_html_permission_and_then_metadata(self):
        opener = self.transport([fixture("viewer-free.html"), fixture("images.json")])
        with patch.object(sunday, "build_opener", return_value=opener):
            images = sunday.images(SITE, URL)
        self.assertEqual(len(images), 36)
        self.assertEqual(images[0], fixture("images.json")["data"]["episode"]["pageImages"]["edges"][0]["node"]["src"])
        requests = [call.args[0] for call in opener.open.call_args_list]
        self.assertEqual([request.get_method() for request in requests], ["GET", "POST"])
        self.assertEqual(requests[0].full_url, URL)
        self.assertEqual(requests[1].full_url, ENDPOINT)
        self.assertEqual(requests[1].get_header("Referer"), URL)
        self.assertEqual(json.loads(requests[1].data)["variables"]["id"], EPISODE)
        self.assertTrue(all("/public/original/" in image for image in images))

    def test_observed_nonfree_chapter_blocks_before_image_metadata(self):
        opener = self.transport([fixture("viewer-restricted.html")])
        with patch.object(sunday, "build_opener", return_value=opener), self.assertRaisesRegex(RuntimeError, "未.*公开免费"):
            sunday.images(SITE, RESTRICTED)
        self.assertEqual(opener.open.call_count, 1)
        self.assertEqual(opener.open.call_args.args[0].get_method(), "GET")

    def test_permission_requires_positive_boolean_flags_even_if_purchased(self):
        for field, value in (("can_read", False), ("can_read", "true"), ("can_read", None), ("isPublic", False), ("isPublic", 1)):
            layer, product = permission_parts()
            if field == "can_read":
                layer["episode"][field] = value
            else:
                product["readableProduct"][field] = value
                product["readableProduct"]["hasPurchased"] = True
            opener = self.transport([permission_html(layer, product)])
            with self.subTest(field=field, value=value), patch.object(sunday, "build_opener", return_value=opener), self.assertRaisesRegex(RuntimeError, "公开免费"):
                sunday.images(SITE, URL)
            self.assertEqual(opener.open.call_count, 1)

    def test_permission_rejects_mismatched_ids_permalinks_and_missing_structures(self):
        variants = []
        layer, product = permission_parts()
        layer["episode"]["episode_id"] = "123"
        variants.append(permission_html(layer, product))
        layer, product = permission_parts()
        product["readableProduct"]["id"] = "123"
        variants.append(permission_html(layer, product))
        layer, product = permission_parts()
        product["readableProduct"]["permalink"] = sunday.ORIGIN + "/episode/123"
        variants.append(permission_html(layer, product))
        layer, product = permission_parts()
        product["readableProduct"]["series"]["id"] = "123"
        variants.append(permission_html(layer, product))
        layer, product = permission_parts()
        product["readableProduct"]["pageStructure"] = None
        variants.append(permission_html(layer, product))
        variants.extend(["<html>login</html>", '<html data-route="core:viewer" data-gtm-data-layer="bad"><script id="episode-json" data-value="{}"></script></html>'])
        for page in variants:
            opener = self.transport([page])
            with self.subTest(page=page[:80]), patch.object(sunday, "build_opener", return_value=opener), self.assertRaises(RuntimeError):
                sunday.images(SITE, URL)
            self.assertEqual(opener.open.call_count, 1)

    def test_images_follow_connection_to_exact_public_page_count(self):
        first, second = fixture("images.json"), fixture("images.json")
        a, b = first["data"]["episode"]["pageImages"], second["data"]["episode"]["pageImages"]
        a["edges"], b["edges"] = a["edges"][:20], b["edges"][20:]
        a["pageInfo"] = {"hasNextPage": True, "endCursor": "MTk"}
        opener = self.transport([fixture("viewer-free.html"), first, second])
        with patch.object(sunday, "build_opener", return_value=opener):
            result = sunday.images(SITE, URL)
        self.assertEqual(len(result), 36)
        self.assertEqual(json.loads(opener.open.call_args_list[2].args[0].data)["variables"]["after"], "MTk")

    def test_image_metadata_identity_count_dimensions_and_cdn_are_enforced(self):
        variants = []
        bad = fixture("images.json"); bad["data"]["episode"]["databaseId"] = "123"; variants.append(bad)
        bad = fixture("images.json"); bad["data"]["episode"]["series"]["databaseId"] = "123"; variants.append(bad)
        bad = fixture("images.json"); bad["data"]["episode"]["pageImages"]["edges"].pop(); variants.append(bad)
        bad = fixture("images.json"); bad["data"]["episode"]["pageImages"]["edges"].append(deepcopy(bad["data"]["episode"]["pageImages"]["edges"][0])); variants.append(bad)
        for key, value in (("width", -1), ("height", 99_999_999), ("width", True), ("src", "https://evil.cdn-img.www.sunday-webry.com/public/original/file"),
                           ("src", "https://cdn-img.www.sunday-webry.com.evil.test/public/original/file"), ("src", "https://cdn-img.www.sunday-webry.com/public/page/2/scrambled"),
                           ("src", "http://cdn-img.www.sunday-webry.com/public/original/file"), ("src", "https://cdn-img.www.sunday-webry.com:wrong/public/original/file")):
            bad = fixture("images.json"); bad["data"]["episode"]["pageImages"]["edges"][0]["node"][key] = value; variants.append(bad)
        for data in variants:
            opener = self.transport([fixture("viewer-free.html"), data])
            with self.subTest(data=data), patch.object(sunday, "build_opener", return_value=opener), self.assertRaises(RuntimeError):
                sunday.images(SITE, URL)

    def test_invalid_site_urls_and_keywords_make_no_network_request(self):
        invalid = [None, 123, "file:///episode/123", "https://www.sunday-webry.com.evil.test/episode/123",
                   URL + "?id=123", URL + "#123", URL + "\n", "https://user@www.sunday-webry.com/episode/123",
                   "https://www.sunday-webry.com:8765/episode/123", sunday.ORIGIN + "/episode/1e20", sunday.ORIGIN + "/series/123"]
        with patch.object(sunday, "build_opener") as build:
            for value in invalid:
                with self.subTest(value=value), self.assertRaises(ValueError):
                    sunday.details(SITE, value)
            with self.assertRaises(ValueError):
                sunday.search("other", "コナン")
            for keyword in (None, "x" * 201):
                with self.assertRaises(ValueError):
                    sunday.search(SITE, keyword)
            self.assertEqual(sunday.search(SITE, "   "), [])
        build.assert_not_called()

    def test_http_errors_limits_bad_json_and_deadline_are_explicit(self):
        for error in [HTTPError(ENDPOINT, code, "hidden", {}, None) for code in (400, 401, 403, 429, 503)] + [URLError("failed"), TimeoutError()]:
            opener = Mock(); opener.open.side_effect = error
            with self.subTest(error=error), patch.object(sunday, "build_opener", return_value=opener), self.assertRaises(RuntimeError):
                sunday.search(SITE, "コナン")
            self.assertEqual(opener.open.call_count, 1)
        for value in ("not json", {"data": None}, {"errors": [{}]}, []):
            with patch.object(sunday, "build_opener", return_value=self.transport([value])), self.assertRaises(RuntimeError):
                sunday.search(SITE, "コナン")
        with patch.object(sunday, "MAX_RESPONSE_BYTES", 8), patch.object(sunday, "build_opener", return_value=self.transport(["x" * 9])), self.assertRaisesRegex(RuntimeError, "过大"):
            sunday.search(SITE, "コナン")
        with patch.object(sunday, "build_opener") as build, self.assertRaisesRegex(RuntimeError, "超时"):
            sunday._request(ENDPOINT, time.monotonic() - 1)
        build.assert_not_called()

    def test_no_redirect_does_not_forward_post_or_headers(self):
        request = Request(ENDPOINT, data=b'{"query":"fixture"}', headers={"Origin": sunday.ORIGIN})
        for destination in ("https://evil.test/graphql", "http://www.sunday-webry.com/graphql", sunday.ORIGIN + "/login"):
            with self.subTest(destination=destination), self.assertRaisesRegex(RuntimeError, "跳转"):
                sunday._NoRedirect().redirect_request(request, None, 302, "Found", {}, destination)


if __name__ == "__main__":
    unittest.main()
