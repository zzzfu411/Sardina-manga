"""HIP metadata contract, using the observed response shape and synthetic values."""
from copy import deepcopy
from http.client import IncompleteRead
import json
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from client import hipmh_metadata as h


MID = "bTo0Mjk2"
BOOK = {
    "id": 4296,
    "title": "一拳超人",
    "authors": [{"id": 2059, "name": "ONE+村田雄介"}, {"id": 652, "name": "集英社"}],
    "description": "合成测试简介",
    "status": "ongoing",
    "vertical_image_url": "/kk/vertical/sample.webp",
    "cover_image_url": "/kk/horizontal/sample.webp",
}


def response(payload):
    result = MagicMock()
    result.__enter__.return_value = result
    result.read.return_value = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return result


class HipMetadataTests(unittest.TestCase):
    def test_recorded_endpoint_returns_verified_book_without_images_or_chapters(self):
        reply = response({"code": 200, "message": "ok", "data": BOOK})
        with patch.object(h, "urlopen", return_value=reply) as get:
            result = h.metadata(MID)
        self.assertEqual(result, {
            "title": "一拳超人", "author": "ONE+村田雄介 / 集英社",
            "coverUrl": "https://cover.s3imgs.top/kk/vertical/sample.webp",
            "description": "合成测试简介", "status": "ongoing",
        })
        get.assert_called_once()
        self.assertEqual(get.call_args.args[0].full_url, "https://hipapi1.s3file.top/v1/manga?mid=" + MID)
        self.assertEqual(get.call_args.args[0].get_method(), "GET")
        reply.read.assert_called_once_with(h.MAX_BYTES + 1)

    def test_wrong_missing_boolean_or_fractional_book_identity_is_rejected(self):
        for identity in (4297, None, True, 4296.0, "4296.0", {}, ""):
            with self.subTest(identity=identity), patch.object(h, "urlopen", return_value=response({"code": 200, "data": {**BOOK, "id": identity}})):
                with self.assertRaisesRegex(h.MetadataError, "不一致"):
                    h.metadata(MID)

    def test_string_numeric_identity_is_equivalent(self):
        with patch.object(h, "urlopen", return_value=response({"code": 200, "data": {**BOOK, "id": "4296"}})):
            self.assertEqual(h.metadata(MID)["title"], "一拳超人")

    def test_api_failure_site_default_or_nested_recommendations_are_not_a_book(self):
        replies = [
            {"code": 403, "message": "denied", "data": BOOK},
            {"data": BOOK}, [], None,
            {"code": 200, "data": {"title": "站点首页", "description": "站点简介"}},
            {"code": 200, "data": {"recommendations": [BOOK]}},
            {"code": 200, "data": [BOOK]},
        ]
        for payload in replies:
            with self.subTest(payload=payload), patch.object(h, "urlopen", return_value=response(payload)):
                with self.assertRaises(h.MetadataError):
                    h.metadata(MID)

    def test_missing_title_is_explicit_failure_not_placeholder(self):
        for title in (None, "  ", 123):
            with self.subTest(title=title), patch.object(h, "urlopen", return_value=response({"code": 200, "data": {**BOOK, "title": title}})):
                with self.assertRaisesRegex(h.MetadataError, "作品名称"):
                    h.metadata(MID)

    def test_invalid_mid_is_rejected_before_io(self):
        for mid in (None, 4296, "4296", "", "../private", "bTo0Mjk2?other=1", "Yzox", "bTo0Mjk2-foreign"):
            with self.subTest(mid=mid), patch.object(h, "urlopen") as get:
                with self.assertRaises(ValueError):
                    h.metadata(mid)
                get.assert_not_called()

    def test_missing_optional_fields_stay_empty_and_source_authors_are_not_guessed(self):
        minimal = {"id": 4296, "title": "一拳超人", "description": {"site": "首页"}, "authors": "站点维护者", "status": 1}
        with patch.object(h, "urlopen", return_value=response({"code": 200, "data": minimal})):
            self.assertEqual(h.metadata(MID), {"title": "一拳超人", "author": "", "coverUrl": "", "description": "", "status": ""})

    def test_author_names_are_trimmed_deduplicated_and_payload_unmodified(self):
        book = {**BOOK, "authors": [{"name": " ONE "}, {"name": "ONE"}, {"name": ""}, "site staff", None, {"name": "村田雄介"}]}
        original = deepcopy(book)
        result = h._parse({"code": 200, "data": book}, 4296)
        self.assertEqual(result["author"], "ONE / 村田雄介")
        self.assertEqual(book, original)

    def test_cover_paths_use_known_cover_host_and_reject_foreign_or_unsafe_urls(self):
        self.assertEqual(h._cover("/kk/vertical/a.webp"), "https://cover.s3imgs.top/kk/vertical/a.webp")
        self.assertEqual(h._cover("https://cover.s3imgs.top/a.webp"), "https://cover.s3imgs.top/a.webp")
        for value in ("//foreign.test/cover.jpg", "https://foreign.test/cover.jpg", "javascript:alert(1)",
                      "https://user@cover.s3imgs.top/a.webp", "https://cover.s3imgs.top:9000/a.webp", "https://[bad/cover", "/a\nb.webp"):
            with self.subTest(value=value):
                self.assertEqual(h._cover(value), "")
        result = h._parse({"code": 200, "data": {**BOOK, "vertical_image_url": None}}, 4296)
        self.assertEqual(result["coverUrl"], "https://cover.s3imgs.top/kk/horizontal/sample.webp")

    def test_network_and_http_failures_stay_explicit(self):
        for error in (HTTPError("https://example.test", 403, "private upstream body", {}, None),
                      URLError("offline"), TimeoutError(), IncompleteRead(b"partial")):
            with self.subTest(error=type(error).__name__), patch.object(h, "urlopen", side_effect=error):
                with self.assertRaises(h.MetadataError) as raised:
                    h.metadata(MID)
                self.assertNotIn("private upstream body", str(raised.exception))

    def test_invalid_json_and_oversized_responses_fail(self):
        for raw in (b"<html>login</html>", b"\xff"):
            with self.subTest(raw=raw), patch.object(h, "urlopen", return_value=response(raw)):
                with self.assertRaisesRegex(h.MetadataError, "JSON"):
                    h.metadata(MID)
        with patch.object(h, "urlopen", return_value=response(b"[" * 3000 + b"0" + b"]" * 3000)):
            with self.assertRaises(h.MetadataError):
                h.metadata(MID)
        with patch.object(h, "MAX_BYTES", 10), patch.object(h, "urlopen", return_value=response(b"x" * 11)):
            with self.assertRaisesRegex(h.MetadataError, "过大"):
                h.metadata(MID)


if __name__ == "__main__":
    unittest.main()
