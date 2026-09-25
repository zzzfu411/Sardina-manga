"""Synthetic anonymous website protocol fixtures; no network or real images."""
from io import BytesIO
import json
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from client import mangacopy_web as web

KEY = "synthetickey1234"
IV = "publiciv12345678"
DETAIL = web.ORIGIN + "/comic/example"
DIRECTORY = web.ORIGIN + "/comicdetail/example/chapters"
IDS = [f"00000000-0000-4000-8000-{i:012d}" for i in range(1, 8)]
CHAPTER = DETAIL + "/chapter/" + IDS[0]


def encrypt(value=None, *, raw=None, key=KEY):
    data = json.dumps(value, ensure_ascii=False).encode() if raw is None else raw
    padding = PKCS7(128).padder()
    padded = padding.update(data) + padding.finalize()
    cipher = Cipher(algorithms.AES(key.encode()), modes.CBC(IV.encode())).encryptor()
    return IV + (cipher.update(padded) + cipher.finalize()).hex()


def detail_html(*, key=KEY, dnts="3", extra=""):
    return f'''<!doctype html><html><body>
    <aside><h6>推荐作品</h6><span>作者：错误作者</span><img src="https://bad.example/ad.jpg"></aside>
    <div class="container comicParticulars-title">
      <div class="comicParticulars-left-img"><img class="lazyload" data-src="https://ss.mangafunb.fun/e/example/cover.jpg"></div>
      <h6 title="合成作品">合成作品</h6>
      <ul><li><span>作者：</span><span><a>作者甲</a> / <a>作者乙</a></span></li>
      <li><span>狀態：</span><span>連載中</span></li></ul>
    </div><p class="intro">合成简介 &amp; 数据。<br>第二行</p>
    <span id="dnt" value="{dnts}"></span><script>var ccz = '{key}';</script>
    {extra}</body></html>'''


def chapter_html(payload, *, key=KEY):
    return f'''<html><body><ul class="comicContent-list"></ul>
    <img src="https://ss.mangafunb.fun/static/ads/advertisement.jpg">
    <span class="comicCount">0</span>
    <script>var cct = '{key}'; var contentKey = '{payload}';</script></body></html>'''


def directory(groups=None, slug="example"):
    return {"build": {"path_word": slug, "type": [{"id": 1, "name": "話"}, {"id": 2, "name": "卷"}, {"id": 3, "name": "番外篇"}]},
            "groups": groups if groups is not None else {"default": {"path_word": "default", "count": 0, "name": "默認", "chapters": []}}}


def row(index, name, kind=1):
    return {"id": IDS[index], "name": name, "type": kind}


class Response(BytesIO):
    def __init__(self, value, url):
        if isinstance(value, dict):
            value = json.dumps(value, ensure_ascii=False)
        super().__init__(value.encode() if isinstance(value, str) else value)
        self.url = url

    def geturl(self):
        return self.url


class ProtocolTests(unittest.TestCase):
    def transport(self, pages):
        def read(request, timeout):
            self.assertEqual(timeout, 25)
            self.assertEqual(request.get_method(), "GET")
            self.assertIsNone(request.get_header("Authorization"))
            self.assertIsNone(request.get_header("Cookie"))
            return Response(pages[request.full_url], request.full_url)
        opener = Mock()
        opener.open.side_effect = read
        return opener

    def test_details_decrypts_public_protocol_and_preserves_all_groups_and_types(self):
        groups = {
            "default": {"name": "默認", "count": 4, "chapters": [row(2, "番外篇", 3), row(1, "第10話"), row(0, "第2話"), row(3, "第01卷", 2)]},
            "other": {"name": "另一版本", "count": 2, "chapters": [row(4, "第1話"), row(0, "第2話")]},
        }
        opener = self.transport({DETAIL: detail_html(), DIRECTORY: {"code": 200, "results": encrypt(directory(groups))}})
        with patch.object(web, "build_opener", return_value=opener) as build:
            result = web.details(DETAIL)
        build.assert_called_once()
        self.assertEqual(result["title"], "合成作品")
        self.assertEqual(result["author"], "作者甲 / 作者乙")
        self.assertEqual(result["description"], "合成简介 & 数据。 第二行")
        self.assertEqual(result["status"], "連載中")
        self.assertEqual(result["coverUrl"], "https://ss.mangafunb.fun/e/example/cover.jpg")
        self.assertEqual(result["sourceUrl"], DETAIL)
        self.assertNotIn("unavailableReason", result)
        self.assertEqual([r["name"] for r in result["chapters"]], ["第2話", "第10話", "第01卷", "番外篇", "第1話"])
        self.assertEqual([r["group"] for r in result["chapters"]], ["默認 · 話", "默認 · 話", "默認 · 卷", "默認 · 番外篇", "另一版本 · 話"])
        self.assertEqual([r["order"] for r in result["chapters"]], list(range(5)))
        self.assertEqual(result["chapters"][0]["id"], CHAPTER)
        requests = [call.args[0] for call in opener.open.call_args_list]
        self.assertEqual([r.full_url for r in requests], [DETAIL, DIRECTORY])
        self.assertEqual(requests[1].get_header("Dnts"), "3")
        self.assertEqual(requests[1].get_header("Referer"), DETAIL)
        self.assertEqual(requests[1].get_header("Content-type"), "application/x-www-form-urlencoded;charset=UTF-8")

    def test_current_empty_public_directory_is_explicitly_unavailable(self):
        opener = self.transport({DETAIL: detail_html(), DIRECTORY: {"code": 200, "results": encrypt(directory())}})
        with patch.object(web, "build_opener", return_value=opener):
            result = web.details(DETAIL)
        self.assertEqual(result["chapters"], [])
        self.assertEqual(result["unavailableReason"], web.EMPTY_DIRECTORY)
        self.assertEqual(result["title"], "合成作品")
        self.assertEqual(opener.open.call_count, 2)

    def test_directory_mismatch_partial_invalid_or_oversize_never_looks_complete(self):
        invalid = [
            directory(slug="other-work"),
            {"build": {"path_word": "example"}, "groups": []},
            directory({"g": {"name": "test", "count": 2, "chapters": [row(0, "第1話")]}}),
            directory({"g": {"name": "test", "count": True, "chapters": [row(0, "第1話")]}}),
            directory({"g": {"name": "test", "chapters": []}}),
            directory({"g": {"count": 1, "chapters": [{"id": "../../private", "name": "第1話"}]}}),
            directory({"g": {"count": 1, "chapters": [{"id": IDS[0], "name": ""}]}}),
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                opener = self.transport({DETAIL: detail_html(), DIRECTORY: {"code": 200, "results": encrypt(payload)}})
                with patch.object(web, "build_opener", return_value=opener), self.assertRaises(RuntimeError):
                    web.details(DETAIL)
        with patch.object(web, "MAX_CHAPTERS", 0), self.assertRaisesRegex(RuntimeError, "上限"):
            web._directory(directory({"g": {"count": 1, "chapters": [row(0, "第1話")]}}), "example")

    def test_business_errors_and_non_json_do_not_produce_empty_success(self):
        for response in ({"code": 210, "message": "private upstream explanation"}, {"code": 403}, [], "<html>login</html>"):
            with self.subTest(response=response):
                if isinstance(response, list):
                    response = json.dumps(response)
                opener = self.transport({DETAIL: detail_html(), DIRECTORY: response})
                with patch.object(web, "build_opener", return_value=opener), self.assertRaises(RuntimeError) as error:
                    web.details(DETAIL)
                self.assertNotIn("private upstream explanation", str(error.exception))
                self.assertEqual(opener.open.call_count, 2)

    def test_empty_or_challenged_detail_never_uses_recommendation_metadata(self):
        for page in ("<h6>广告标题</h6><div>请输入验证码</div>", "<html>Login required</html>"):
            opener = self.transport({DETAIL: page})
            with patch.object(web, "build_opener", return_value=opener), self.assertRaisesRegex(RuntimeError, "作品详情"):
                web.details(DETAIL)
            self.assertEqual(opener.open.call_count, 1)

    def test_missing_or_dynamic_page_parameter_is_not_executed_or_guessed(self):
        for page in (
            detail_html().replace("var ccz = '" + KEY + "';", "var ccz = computeKey();"),
            detail_html().replace("var ccz = '" + KEY + "';", "var ccz = 'abc' + other;"),
            detail_html(extra="<script>var ccz = 'secondkey1234567';</script>"),
            detail_html(dnts=""),
            detail_html(dnts="x&#10;Injected:value"),
        ):
            opener = self.transport({DETAIL: page})
            with patch.object(web, "build_opener", return_value=opener), self.assertRaises(RuntimeError):
                web.details(DETAIL)
            self.assertEqual(opener.open.call_count, 1)

    def test_images_decrypt_only_embedded_data_preserve_page_order_and_signed_query(self):
        first = "https://ss.mangafunb.fun/manga/image.webp?signature=abc%2B%20xyz&order=1"
        second = "https://s3.mangafunb.fun/manga/page2.jpg"
        payload = [{"url": first}, {"url": second}, {"url": first}]
        opener = self.transport({CHAPTER: chapter_html(encrypt(payload))})
        with patch.object(web, "build_opener", return_value=opener):
            result = web.images(CHAPTER)
        self.assertEqual(result, [first, second, first])
        self.assertEqual(opener.open.call_count, 1)
        self.assertEqual(opener.open.call_args.args[0].get_header("Referer"), DETAIL)

    def test_empty_content_key_and_empty_or_non_list_payload_raise_not_empty_list(self):
        for value in ("", encrypt([]), encrypt({"images": []}), encrypt(None)):
            opener = self.transport({CHAPTER: chapter_html(value)})
            with patch.object(web, "build_opener", return_value=opener), self.assertRaisesRegex(RuntimeError, "未向匿名访问"):
                web.images(CHAPTER)
            self.assertEqual(opener.open.call_count, 1)

    def test_image_rows_and_hosts_fail_closed_without_omitting_bad_pages(self):
        good = {"url": "https://ss.mangafunb.fun/p.jpg"}
        for bad in ({}, None, {"url": "https://evil-mangafunb.fun/p.jpg"}, {"url": "https://mangafunb.fun.evil.test/p.jpg"},
                    {"url": "https://127.0.0.1/p.jpg"}, {"url": "file:///private/file"},
                    {"url": "https://user:password@ss.mangafunb.fun/p.jpg"}, {"url": "https://ss.mangafunb.fun:8765/p.jpg"},
                    {"url": "https://ss.mangafunb.fun/p.jpg\n"}):
            opener = self.transport({CHAPTER: chapter_html(encrypt([good, bad]))})
            with patch.object(web, "build_opener", return_value=opener), self.assertRaises(RuntimeError):
                web.images(CHAPTER)
        opener = self.transport({CHAPTER: chapter_html(encrypt([good, good]))})
        with patch.object(web, "MAX_IMAGES", 1), patch.object(web, "build_opener", return_value=opener), self.assertRaisesRegex(RuntimeError, "上限"):
            web.images(CHAPTER)

    def test_cbc_iv_is_raw_ascii_cipher_is_hex_and_padding_is_strict(self):
        payload = {"text": "合成中文", "value": [1, 2]}
        self.assertEqual(web._decrypt_json(encrypt(payload), KEY), payload)
        padded = b" " * 15 + b"\x00"  # Invalid PKCS#7; AES itself remains valid.
        encoder = Cipher(algorithms.AES(KEY.encode()), modes.CBC(IV.encode())).encryptor()
        bad_padding = IV + (encoder.update(padded) + encoder.finalize()).hex()
        invalid = [bad_padding, IV + "00", IV + "ff" * 16 + "x", "a" * 15,
                   IV + "not-base64-or-hex", encrypt(raw=b"not json"), encrypt(raw=b"\xff\xfe")]
        for value in invalid:
            with self.subTest(value=value[:40]), self.assertRaises(RuntimeError):
                web._decrypt_json(value, KEY)
        with self.assertRaises(RuntimeError):
            web._decrypt_json(encrypt(payload), "short-key")
        with patch.object(web, "MAX_PLAINTEXT_BYTES", 8), self.assertRaisesRegex(RuntimeError, "过大"):
            web._decrypt_json(encrypt({"many": "x" * 100}), KEY)

    def test_urls_are_canonicalized_and_invalid_addresses_make_no_request(self):
        self.assertEqual(web._url("http://mangacopy.com/comic/example/"), (DETAIL, "example"))
        invalid = [None, 123, "https://mangacopy.com.evil.test/comic/example", "file:///comic/example",
                   DETAIL + "?dnts=0", DETAIL + "#fragment", "https://u:p@www.mangacopy.com/comic/example",
                   "https://www.mangacopy.com:8765/comic/example", web.ORIGIN + "/comic/%2e%2e", DETAIL + "\n"]
        with patch.object(web, "build_opener") as build:
            for value in invalid:
                with self.subTest(value=value), self.assertRaises(ValueError):
                    web.details(value)
            with self.assertRaises(ValueError):
                web.images(DETAIL + "/chapter/not-an-id")
        build.assert_not_called()

    def test_http_limits_errors_and_redirects_are_not_retried(self):
        for status in (401, 403, 404, 429, 500):
            opener = Mock()
            opener.open.side_effect = HTTPError(DETAIL, status, "upstream detail", {}, None)
            with patch.object(web, "build_opener", return_value=opener), self.assertRaises(RuntimeError):
                web.details(DETAIL)
            self.assertEqual(opener.open.call_count, 1)
        opener = Mock()
        opener.open.side_effect = URLError("network error")
        with patch.object(web, "build_opener", return_value=opener), self.assertRaisesRegex(RuntimeError, "请求失败"):
            web.details(DETAIL)
        opener = self.transport({DETAIL: b"x" * 33})
        with patch.object(web, "MAX_RESPONSE_BYTES", 32), patch.object(web, "build_opener", return_value=opener), self.assertRaisesRegex(RuntimeError, "过大"):
            web.details(DETAIL)
        opener = Mock()
        opener.open.return_value = Response(detail_html(), web.ORIGIN + "/web/login")
        with patch.object(web, "build_opener", return_value=opener), self.assertRaisesRegex(RuntimeError, "未返回所请求"):
            web.details(DETAIL)

    def test_redirects_block_foreign_hosts_downgrades_and_login(self):
        handler, request = web._Redirect(), Request(DETAIL, headers={"Dnts": "3"})
        for url in ("https://evil.test/read", "http://www.mangacopy.com/read", "https://user@www.mangacopy.com/read",
                    "https://www.mangacopy.com:8765/read", web.ORIGIN + "/web/login?next=read"):
            with self.subTest(url=url), self.assertRaises(RuntimeError):
                handler.redirect_request(request, None, 302, "Found", {}, url)
        redirect = handler.redirect_request(request, None, 302, "Found", {}, web.ORIGIN + "/comic/example/")
        self.assertEqual(redirect.full_url, web.ORIGIN + "/comic/example/")


if __name__ == "__main__":
    unittest.main()
