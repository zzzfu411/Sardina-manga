"""Komiic public-protocol regression tests; all bytes and tickets are synthetic."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.message import Message
from io import BytesIO
import json
import time
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.request import Request

from PIL import Image

from client import komiic as k


KID = "2c56308b-ec10-4334-8ccf-4b28156d2fc3"
MARKER = "https://komiic.com/api/image/" + KID
IMAGE_URL = "https://img.komiic.com/comics/synthetic/book/01/000.jpg"
TICKET = "synthetic.secret.ticket"


def response(body, mime="application/json"):
    value = MagicMock()
    value.__enter__.return_value = value
    value.read.return_value = body if isinstance(body, bytes) else json.dumps(body).encode()
    value.headers = Message()
    value.headers["Content-Type"] = mime
    return value


def ticket(kid=KID, **overrides):
    return {"kid": kid, "url": IMAGE_URL, "ticket": TICKET, "width": 3, "height": 4,
            "expiresAt": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(), **overrides}


def png():
    buffer = BytesIO()
    with Image.new("RGB", (3, 4), (20, 50, 80)) as image:
        image.save(buffer, "PNG")
    return buffer.getvalue()


class KomiicMetadataTests(unittest.TestCase):
    def test_details_preserves_metadata_type_and_canonical_chapter_identity(self):
        data = {"comicById": {"id": "1750", "title": "3月的獅子", "description": "将棋与成长", "status": "ongoing",
                              "imageUrl": "https://komiic.com/cover.jpg", "authors": [{"name": "羽海野千花"}]},
                "chaptersByComicId": [{"id": "20", "serial": "02", "type": "book", "size": 188},
                                      {"id": "12", "serial": "10", "type": "chapter", "size": 20},
                                      {"id": "11", "serial": "2.5", "type": "chapter", "size": 20},
                                      {"id": "10", "serial": "01", "type": "book", "size": 188}]}
        with patch.object(k, "_urlopen", return_value=response({"data": data})) as get:
            result = k.details("1750")
        self.assertEqual(result["title"], "3月的獅子")
        self.assertEqual(result["author"], "羽海野千花")
        self.assertEqual(result["description"], "将棋与成长")
        self.assertEqual(result["status"], "ongoing")
        self.assertEqual(result["catalogCompleteness"], "complete")
        self.assertEqual([r["name"] for r in result["chapters"]], ["第 2.5 话", "第 10 话", "第 01 卷", "第 02 卷"])
        self.assertEqual([r["group"] for r in result["chapters"]], ["chapter", "chapter", "book", "book"])
        self.assertEqual(result["chapters"][2]["url"], "https://komiic.com/comic/1750/chapter/10")
        self.assertEqual([r["order"] for r in result["chapters"]], list(range(4)))
        get.assert_called_once()
        query = json.loads(get.call_args.args[0].data)
        self.assertEqual(query["variables"], {"comicId": "1750"})
        self.assertIn("comicById", query["query"])
        self.assertIn("chaptersByComicId", query["query"])

    def test_wrong_comic_or_missing_directory_is_not_partial_success(self):
        for data in ({"comicById": {"id": "2"}, "chaptersByComicId": []},
                     {"comicById": {"id": "1"}, "chaptersByComicId": None}):
            with self.subTest(data=data), patch.object(k, "_urlopen", return_value=response({"data": data})), self.assertRaisesRegex(RuntimeError, "所选作品"):
                k.details("1")

    def test_images_only_request_metadata_not_whole_chapter_tickets(self):
        rows = [{"id": str(i), "kid": "synthetic-" + str(i), "width": 3, "height": 4} for i in range(188)]
        with patch.object(k, "_urlopen", return_value=response({"data": {"imagesByChapterId": rows}})) as get:
            urls = k.images("42848")
        self.assertEqual(len(urls), 188)
        self.assertEqual(urls[0], "https://komiic.com/api/image/synthetic-0")
        self.assertEqual(urls[-1], "https://komiic.com/api/image/synthetic-187")
        self.assertTrue(all(k.is_image_url(url) for url in urls))
        get.assert_called_once()
        payload = json.loads(get.call_args.args[0].data)
        self.assertIn("imagesByChapterId", payload["query"])
        self.assertNotIn("Ticket", payload["query"])
        self.assertEqual(payload["variables"], {"chapterId": "42848"})

    def test_incomplete_metadata_fails_instead_of_dropping_pages(self):
        data = {"data": {"imagesByChapterId": [{"kid": KID}, {"id": "missing-kid"}]}}
        with patch.object(k, "_urlopen", return_value=response(data)), self.assertRaises(ValueError):
            k.images("42848")

    def test_graphql_quota_is_explicit_and_ignores_partial_data(self):
        payload = {"data": {"getImageTickets": [ticket()]},
                   "errors": [{"message": "Daily image quota exceeded " + TICKET, "extensions": {"code": "QUOTA_EXCEEDED"}}]}
        with patch.object(k, "_urlopen", return_value=response(payload)), self.assertRaisesRegex(RuntimeError, "配额") as raised:
            k._query(k.TICKET_QUERY, {"kids": [KID]})
        self.assertNotIn(TICKET, str(raised.exception))

    def test_malformed_or_oversized_graphql_response_fails(self):
        for payload in (b"not json", [], {"data": None}):
            with self.subTest(payload=payload), patch.object(k, "_urlopen", return_value=response(payload)), self.assertRaises(RuntimeError):
                k.images("1")
        with patch.object(k, "MAX_JSON_BYTES", 5), patch.object(k, "_urlopen", return_value=response(b"x" * 6)) as get, self.assertRaisesRegex(RuntimeError, "过大"):
            k.images("1")
        get.return_value.read.assert_called_once_with(6)


class KomiicImageTests(unittest.TestCase):
    def setUp(self):
        with k._cache_lock:
            k._cache.clear()
            k._cache_bytes = 0

    def tearDown(self):
        with k._cache_lock:
            k._cache.clear()
            k._cache_bytes = 0

    def test_single_visible_kid_gets_one_ticket_and_header_only_download(self):
        data = png()
        opener = MagicMock()
        opener.open.return_value = response(data, "image/png")
        with patch.object(k, "_urlopen", return_value=response({"data": {"getImageTickets": [ticket()]}})) as gql, patch.object(k, "build_opener", return_value=opener):
            result = k.fetch_image(MARKER)
            self.assertEqual(k.fetch_image(MARKER), result)
        gql.assert_called_once()
        query = json.loads(gql.call_args.args[0].data)
        self.assertEqual(query["variables"], {"kids": [KID]})
        self.assertIn("getImageTickets", query["query"])
        self.assertNotIn("imageTicketsByChapterId", query["query"])
        opener.open.assert_called_once()
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, IMAGE_URL)
        self.assertEqual(request.get_header("X-image-ticket"), TICKET)
        self.assertNotIn("Cookie", request.headers)
        self.assertEqual(result, (data, "image/png"))
        with Image.open(BytesIO(result[0])) as image:
            image.load()
            self.assertEqual(image.size, (3, 4))
        self.assertNotIn(TICKET, repr(k._cache))

    def test_legacy_query_ticket_is_removed_from_url_and_sent_as_header(self):
        opener = MagicMock()
        opener.open.return_value = response(png(), "image/png")
        with patch.object(k, "_urlopen", side_effect=AssertionError("legacy must not request a new ticket")), patch.object(k, "build_opener", return_value=opener):
            k.fetch_image(IMAGE_URL + "?ticket=" + TICKET + "&quality=90&signature=a%20b%2Bc")
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, IMAGE_URL + "?quality=90&signature=a%20b%2Bc")
        self.assertEqual(request.get_header("X-image-ticket"), TICKET)
        self.assertNotIn(TICKET, repr(k._cache))

    def test_wrong_kid_expired_or_foreign_ticket_never_downloads(self):
        cases = [[ticket(kid="another-image")], [], [ticket(), ticket()],
                 [ticket(expiresAt="2000-01-01T00:00:00Z")], [ticket(expiresAt="invalid")],
                 [ticket(url="https://evil.test/page.jpg")], [ticket(ticket="bad\r\nX-Injected: yes")]]
        for rows in cases:
            with self.subTest(rows=rows), patch.object(k, "_urlopen", return_value=response({"data": {"getImageTickets": rows}})), patch.object(k, "build_opener") as build:
                with self.assertRaises((RuntimeError, ValueError)):
                    k.fetch_image(MARKER)
                build.assert_not_called()
                self.assertFalse(k._cache)

    def test_quota_and_expired_ticket_errors_are_not_cached_or_retried(self):
        for status, word in ((401, "票据"), (403, "票据"), (402, "配额"), (429, "配额")):
            opener = MagicMock()
            opener.open.side_effect = HTTPError(IMAGE_URL + "?ticket=" + TICKET, status, TICKET, {}, None)
            with self.subTest(status=status), patch.object(k, "_urlopen", return_value=response({"data": {"getImageTickets": [ticket()]}})) as gql, patch.object(k, "build_opener", return_value=opener):
                with self.assertRaisesRegex(RuntimeError, word) as raised:
                    k.fetch_image(MARKER)
                self.assertNotIn(TICKET, str(raised.exception))
                gql.assert_called_once()
                opener.open.assert_called_once()
                self.assertFalse(k._cache)

    def test_download_checks_byte_pixel_and_image_content_limits(self):
        data = png()
        cases = [(data, "image/png", "MAX_BYTES", len(data) - 1, "大小"),
                 (data, "image/png", "MAX_PIXELS", 11, "像素"),
                 (b"broken", "image/png", "MAX_BYTES", 1024, "解码"),
                 (b"<html>denied</html>", "text/html", "MAX_BYTES", 1024, "有效图片")]
        for body, mime, limit, value, word in cases:
            opener = MagicMock()
            opener.open.return_value = response(body, mime)
            with self.subTest(limit=limit, word=word), patch.object(k, limit, value), patch.object(k, "build_opener", return_value=opener), self.assertRaisesRegex(RuntimeError, word):
                k._download(IMAGE_URL, TICKET)

    def test_success_cache_is_bounded_expires_and_replaces_values(self):
        with patch.object(k, "MAX_CACHE_BYTES", 5), patch.object(k.time, "monotonic", return_value=10):
            k._cache_put("a", (b"aa", "image/png"))
            k._cache_put("b", (b"bb", "image/png"))
            self.assertIsNotNone(k._cache_get("a"))
            k._cache_put("c", (b"cc", "image/png"))
            self.assertIsNone(k._cache_get("b"))
            k._cache_put("a", (b"a", "image/png"))
            self.assertEqual(k._cache_bytes, 3)
        with patch.object(k.time, "monotonic", return_value=311):
            self.assertIsNone(k._cache_get("a"))
            self.assertIsNone(k._cache_get("c"))
            self.assertEqual(k._cache_bytes, 0)

    def test_parallel_same_image_coalesces_ticket_and_download(self):
        opener = MagicMock()

        def load(*args, **kwargs):
            time.sleep(0.01)
            return response(png(), "image/png")

        opener.open.side_effect = load
        with patch.object(k, "_urlopen", return_value=response({"data": {"getImageTickets": [ticket()]}})) as gql, patch.object(k, "build_opener", return_value=opener), ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(k.fetch_image, [MARKER] * 4))
        gql.assert_called_once()
        opener.open.assert_called_once()
        self.assertTrue(all(value == results[0] for value in results))


class KomiicURLTests(unittest.TestCase):
    def test_only_proxy_markers_and_explicit_legacy_ticket_urls_are_recognized(self):
        self.assertTrue(k.is_image_url(MARKER))
        self.assertTrue(k.is_image_url(IMAGE_URL + "?ticket=" + TICKET))
        for url in ("https://komiic.com.evil.test/api/image/" + KID, "https://other.komiic.com/api/image/" + KID,
                    "https://user@komiic.com/api/image/" + KID, "https://komiic.com:9000/api/image/" + KID,
                    "https://komiic.com/api/image/..%2Fprivate", MARKER + "?kid=other", IMAGE_URL,
                    IMAGE_URL + "?ticket=a&ticket=b", IMAGE_URL + "?ticket=bad%0d%0aheader", MARKER + "\n", None, 42):
            with self.subTest(url=url):
                self.assertFalse(k.is_image_url(url))

    def test_image_ticket_cannot_follow_cross_origin_redirect(self):
        request = Request(IMAGE_URL, headers={"X-Image-Ticket": TICKET})
        allowed = k._ImageRedirect().redirect_request(request, None, 302, "Found", {}, "https://img.komiic.com/redirect.jpg")
        self.assertEqual(allowed.get_header("X-image-ticket"), TICKET)
        for url in ("https://komiic.com/elsewhere", "https://another.komiic.com/elsewhere",
                    "https://img.komiic.com.evil.test/x", "http://img.komiic.com/downgrade", "https://img.komiic.com:443/x"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                k._ImageRedirect().redirect_request(request, None, 302, "Found", {}, url)

    def test_invalid_ids_do_not_request_network(self):
        for value in ("../private", "1 OR 1", "", None):
            with self.subTest(value=value), patch.object(k, "_urlopen") as get, self.assertRaises(ValueError):
                k.details(value)
            get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
