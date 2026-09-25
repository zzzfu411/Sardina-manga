"""Offline protocol tests using generated pixels and independently encrypted bytes.

No source images are downloaded. Transport/padding and header fixtures follow
output/source-parity/merge_split_file_monga.js; strip vectors were cross-checked
against the captured comicbox-engine JavaScript with numbered synthetic rows.
"""
import base64
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
import http.client
from io import BytesIO
import json
import random
import threading
import time
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import urlencode
from urllib.request import Request

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from PIL import Image

from client import comicbox_images as box


CDN = "https://synthetic.ccavbox.com"
PAGE = CDN + "/break_2/static/upload/book/1/9/1.jpg"
COVER = CDN + "/break_2/static/upload/book/1/cover_pc.jpg"
JPEG_HEADER = bytes.fromhex("ffd8ffe000104a4649460001")


def encrypt(data, *, padded=True):
    """Generate fixtures without using the adapter's padding/decryption code."""
    if padded:
        count = 16 - len(data) % 16
        data += bytes([count]) * count
    cipher = Cipher(algorithms.AES(b"aaaaaaaaaaaaaaaa"), modes.CBC(b"0123456789aaaaaa")).encryptor()
    return cipher.update(data) + cipher.finalize()


def transport_parts(data, joined=False):
    if joined:
        cipher = encrypt(data)
        # Encrypt-then-split chunks need not individually align to AES blocks.
        return [cipher[:7], cipher[7:]]
    return [encrypt(data[:13]), encrypt(data[13:])]


def encoded_manifest(chunks=None, *, decoder="monga-v2-encrypt-then-split"):
    if chunks is None:
        chunks = [
            {"index": 1, "count": 2, "url": CDN + "/immutable/second.bin?sig=a%2Bb"},
            {"index": 0, "count": 2, "url": CDN + "/immutable/first.bin?sig=c%2Fd"},
        ]
    value = {"variants": {"jpeg": {"renditions": [{"role": "page", "decoder": decoder, "chunks": chunks}]}}}
    return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).decode().rstrip("=")


def logical_url(url=PAGE, **query):
    return url + "?" + urlencode(query)


def synthetic_jpeg(*, noise=False):
    # Header IDs 1/1 produce 72 strips; the cover URL has no page number.
    size = (40, 579) if not noise else (128, 144)
    with Image.new("RGB", size) as image:
        if noise:
            rng = random.Random(1729)
            image.putdata([tuple(rng.randrange(256) for _ in range(3)) for _ in range(size[0] * size[1])])
        else:
            image.putdata([(y * 255 // size[1],) * 3 for y in range(size[1]) for _ in range(size[0])])
        output = BytesIO()
        image.save(output, "JPEG", quality=5 if noise else 95)
    source = output.getvalue()
    assert source[:12] == JPEG_HEADER
    custom = bytearray(source)
    custom[:8] = b"\x00\x00" + (1).to_bytes(2, "big") + (1).to_bytes(4, "big")
    return bytes(custom), size


class ComicboxCryptoTests(unittest.TestCase):
    def test_pkcs7_lengths_and_raw_binary_plaintext(self):
        for length in (0, 1, 15, 16, 17, 31, 32, 77):
            raw = bytes((index * 37 + 13) % 256 for index in range(length))
            with self.subTest(length=length):
                self.assertEqual(box.decrypt_parts([encrypt(raw)]), raw)

    def test_both_split_protocols_restore_identical_bytes(self):
        raw = bytes(range(77))
        self.assertEqual(box.decrypt_parts(transport_parts(raw)), raw)
        self.assertEqual(box.decrypt_parts(transport_parts(raw, True), True), raw)
        with self.assertRaisesRegex(RuntimeError, "不完整"):
            box.decrypt_parts(transport_parts(raw, True), False)

    def test_padding_is_validated_not_silently_stripped(self):
        # These decrypt to illegal padding: zero, >16, and inconsistent bytes.
        for block in (b"a" * 15 + b"\x00", b"a" * 15 + b"\x11", b"a" * 14 + b"\x01\x02"):
            with self.subTest(block=block), self.assertRaisesRegex(RuntimeError, "校验失败"):
                box.decrypt_parts([encrypt(block, padded=False)])

    def test_missing_truncated_or_oversized_parts_fail(self):
        for parts in ([], [b""], [b"x"], [encrypt(b"a"), b""]):
            with self.subTest(parts=parts), self.assertRaises(RuntimeError):
                box.decrypt_parts(parts)
        with patch.object(box, "MAX_BYTES", 31), self.assertRaisesRegex(RuntimeError, "大小限制"):
            box.decrypt_parts([encrypt(b"a"), encrypt(b"b")])


class ComicboxHeaderAndStripTests(unittest.TestCase):
    def test_headers_are_replaced_in_place_after_reading_big_endian_ids(self):
        signatures = {0: JPEG_HEADER, 3: b"GIF89a", 4: bytes.fromhex("000000206674797061766966")}
        for kind, signature in signatures.items():
            original = bytes([kind, 0, 0x12, 0x34, 1, 2, 3, 4]) + bytes(range(8, 32))
            with self.subTest(kind=kind):
                restored, book_id, page = box.restore_header(original)
                self.assertEqual((book_id, page), ("4660", "16909060"))
                self.assertEqual(restored, signature + original[len(signature):])
                self.assertEqual(len(restored), len(original))

    def test_unsupported_headers_and_non_manga_types_are_explicit_errors(self):
        for value in (b"", b"\x00" * 11, b"\x00\x01" + b"\x00" * 20, b"\x00\x02" + b"\x00" * 20,
                      b"\x01\x00" + b"\x00" * 20, b"\xff\x00" + b"\x00" * 20):
            with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, "编码|格式"):
                box.restore_header(value)

    def test_strip_count_matches_original_javascript_vectors(self):
        # The digest's final ASCII character is used, not its hexadecimal value.
        for book_id, page, expected in (("568", "51014", 44), ("1", "1", 72), ("123", "0004", 44), ("0", "0", 48), ("314159", "271828", 52)):
            with self.subTest(book_id=book_id, page=page):
                self.assertEqual(box.strip_count(book_id, page), expected)

    def test_strip_geometry_preserves_every_row_at_boundary_heights(self):
        for book, page, count in (("568", "51014", 44), ("1", "1", 72), ("0", "0", 48)):
            for height in (1, count - 1, count, count + 1, count * 2 + 3, count * 3):
                rows = [(y, y, y) for y in range(height)]
                # Independent block oracle: reverse equal blocks, retaining the
                # larger trailing block intact as the first destination block.
                unit = height // count
                expected = rows[(count - 1) * unit:]
                for block in reversed(range(count - 1)):
                    expected += rows[block * unit:(block + 1) * unit]
                with self.subTest(book=book, height=height), Image.new("RGB", (1, height)) as source:
                    source.putdata(rows)
                    restored = box.unscramble(source, book, page)
                    try:
                        actual = [restored.getpixel((0, y)) for y in range(height)]
                        self.assertEqual(actual, expected)
                        self.assertEqual([source.getpixel((0, y)) for y in range(height)], rows)
                        self.assertEqual(sorted(actual), rows)
                    finally:
                        restored.close()


class ComicboxImageDecodeTests(unittest.TestCase):
    def test_actual_image_decode_and_strip_restore_for_both_protocols(self):
        raw, size = synthetic_jpeg()
        outputs = []
        for joined in (False, True):
            with self.subTest(joined=joined):
                result, mime = box.decode_image(transport_parts(raw, joined), joined)
                self.assertEqual(mime, "image/jpeg")
                with Image.open(BytesIO(result)) as image:
                    self.assertEqual(image.size, size)
                    self.assertGreater(image.getpixel((20, 3))[0], 245)
                    self.assertLess(image.getpixel((20, size[1] - 4))[0], 10)
                outputs.append(result)
        self.assertEqual(outputs[0], outputs[1])

    def test_pixel_limit_checked_on_real_image(self):
        raw, size = synthetic_jpeg()
        with patch.object(box, "MAX_PIXELS", size[0] * size[1] - 1), self.assertRaisesRegex(RuntimeError, "像素"):
            box.decode_image(transport_parts(raw))

    def test_invalid_image_after_valid_crypto_is_rejected(self):
        custom = b"\x00\x00\x00\x01\x00\x00\x00\x01" + b"not an image" * 5
        with self.assertRaisesRegex(RuntimeError, "解码失败"):
            box.decode_image(transport_parts(custom))

    def test_encoded_output_limit_is_distinct_from_input_limit(self):
        raw, _ = synthetic_jpeg(noise=True)
        parts = transport_parts(raw)
        result, _ = box.decode_image(parts)
        input_size = sum(map(len, parts))
        self.assertGreater(len(result), input_size)
        limit = (len(result) + input_size) // 2
        with patch.object(box, "MAX_BYTES", limit), self.assertRaisesRegex(RuntimeError, "还原后的图片过大"):
            box.decode_image(parts)


class ComicboxPlanTests(unittest.TestCase):
    def test_legacy_paths_preserve_cache_version_and_split_host_order(self):
        hosts = "https://first.ccavbox.com/break_2,https://second.ccavbox.com/break_2"
        urls, joined = box.image_plan(logical_url(COVER, v="version_2-abc", hosts=hosts))
        self.assertFalse(joined)
        self.assertEqual(urls, ["https://first.ccavbox.com/break_2/static/upload/book/1/cover_pc.b_0?v=version_2-abc",
                                "https://second.ccavbox.com/break_2/static/upload/book/1/cover_pc.b_1?v=version_2-abc"])

    def test_avif_path_selects_ciphertext_join_and_fallback_version(self):
        with patch.object(box, "current_cache_version", return_value="current-commit") as version:
            urls, joined = box.image_plan(PAGE.replace("/break_2/", "/break_avif/"))
        self.assertTrue(joined)
        version.assert_called_once_with()
        self.assertTrue(all("/break_avif/" in url and url.endswith("?v=current-commit") for url in urls))

    def test_manifest_takes_priority_over_legacy_url_and_keeps_explicit_urls(self):
        # A manifest's JPEG still uses encrypt-then-split. It must not cause a
        # version fetch or derive legacy siblings from the logical JPEG path.
        with patch.object(box, "current_cache_version", side_effect=AssertionError("unexpected version fetch")):
            urls, joined = box.image_plan(logical_url(manifest=encoded_manifest(), hosts="invalid", v="invalid/version"))
        self.assertTrue(joined)
        self.assertEqual(urls, [CDN + "/immutable/first.bin?sig=c%2Fd", CDN + "/immutable/second.bin?sig=a%2Bb"])

    def test_malformed_or_unsupported_manifests_are_not_legacy_fallbacks(self):
        values = ["!invalid!", "a", "x" * 16001,
                  base64.urlsafe_b64encode(b"not json").decode(),
                  base64.urlsafe_b64encode(b"{}").decode(),
                  encoded_manifest(decoder="unknown-decoder")]
        for encoded in values:
            with self.subTest(encoded=encoded[:30]), patch.object(box, "current_cache_version") as version, self.assertRaises(ValueError):
                box.image_plan(logical_url(manifest=encoded))
            version.assert_not_called()

    def test_manifest_rejects_incomplete_duplicate_invalid_or_foreign_chunks(self):
        valid = {"index": 0, "count": 1, "url": CDN + "/immutable.bin"}
        cases = [[], [None], [{**valid, "index": True}], [{**valid, "count": "1"}],
                 [{**valid, "index": 1}], [{**valid, "count": 2}],
                 [{**valid, "count": 2}, {**valid, "count": 2}],
                 [{**valid, "index": i, "count": 9} for i in range(9)],
                 [{**valid, "url": "https://ccavbox.com.evil.test/chunk"}],
                 [{**valid, "url": "http://127.0.0.1/chunk"}]]
        for chunks in cases:
            with self.subTest(chunks=chunks), self.assertRaises(ValueError):
                box.image_plan(logical_url(manifest=encoded_manifest(chunks)))

    def test_unsupported_path_version_query_and_host_lists_are_rejected(self):
        urls = [logical_url(PAGE.replace("/break_2/", "/break_3/"), v="one"),
                logical_url(v="../bad"), logical_url(v="a") + "&v=b",
                logical_url(v="a", hosts=CDN + "/break_2"),
                logical_url(v="a", hosts=CDN + "/break_avif," + CDN + "/break_avif"),
                logical_url(v="a", hosts="https://evil.test/break_2," + CDN + "/break_2"),
                logical_url(manifest=encoded_manifest()) + "&manifest=duplicate"]
        for url in urls:
            with self.subTest(url=url[:120]), self.assertRaises(ValueError):
                box.image_plan(url)


class ComicboxNetworkBoundaryTests(unittest.TestCase):
    def test_host_boundary_scheme_credentials_and_ports(self):
        for url in (CDN + "/x", "https://ccavbox.com/x", "http://a.ccavbox.com:80/x"):
            self.assertEqual(box.validate_image_url(url), url)
        for url in ("https://ccavbox.com.evil.test/x", "https://evilccavbox.com/x", "https://user@synthetic.ccavbox.com/x",
                    "https://synthetic.ccavbox.com:8000/x", "file:///tmp/x", "http://127.0.0.1/x", "https://ccavbox.com:bad/x"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                box.validate_image_url(url)

    def test_redirects_recheck_the_destination_host(self):
        redirect = box._Redirect()
        request = Request(CDN + "/chunk")
        valid = redirect.redirect_request(request, None, 302, "Found", {}, "https://other.ccavbox.com/chunk")
        self.assertEqual(valid.full_url, "https://other.ccavbox.com/chunk")
        for url in ("https://evil.test/chunk", "https://ccavbox.com.evil.test/chunk", "http://127.0.0.1/chunk"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                redirect.redirect_request(request, None, 302, "Found", {}, url)

    def test_download_bounds_response_bytes_and_sets_source_referer(self):
        opener = MagicMock()
        response = opener.open.return_value.__enter__.return_value
        for payload in (b"", b"x" * 17):
            response.read.return_value = payload
            with patch.object(box, "MAX_BYTES", 16), patch.object(box, "build_opener", return_value=opener), self.assertRaisesRegex(RuntimeError, "大小限制"):
                box._download(CDN + "/chunk")
            response.read.assert_called_with(17)
        response.read.return_value = b"raw encrypted bytes"
        with patch.object(box, "build_opener", return_value=opener):
            self.assertEqual(box._download(CDN + "/chunk"), b"raw encrypted bytes")
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_header("Referer"), "https://www.comicbox.xyz/")
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 15)


class ComicboxCacheAndFetchTests(unittest.TestCase):
    def setUp(self):
        with box._cache_lock:
            box._cache.clear()
            box._cache_bytes = 0

    def tearDown(self):
        with box._cache_lock:
            box._cache.clear()
            box._cache_bytes = 0

    def test_cache_expires_replacements_and_evicts_least_recently_used_by_bytes(self):
        with patch.object(box, "MAX_CACHE_BYTES", 5), patch.object(box.time, "monotonic", return_value=10):
            box._cache_put("a", (b"aa", "image/jpeg"))
            box._cache_put("b", (b"bb", "image/jpeg"))
            self.assertIsNotNone(box._cache_get("a"))
            box._cache_put("c", (b"cc", "image/jpeg"))
            self.assertIsNone(box._cache_get("b"))
            box._cache_put("a", (b"a", "image/jpeg"))
            self.assertEqual(box._cache_bytes, 3)
        with patch.object(box.time, "monotonic", return_value=311):
            self.assertIsNone(box._cache_get("a"))
            self.assertIsNone(box._cache_get("c"))
            self.assertEqual(box._cache_bytes, 0)

    def test_cache_has_entry_limit_and_does_not_retain_oversized_single_value(self):
        for index in range(65):
            box._cache_put(str(index), (b"x", "image/jpeg"))
        self.assertEqual(len(box._cache), 64)
        self.assertIsNone(box._cache_get("0"))
        with patch.object(box, "MAX_CACHE_BYTES", 2):
            box._cache_put("too-big", (b"xxxx", "image/jpeg"))
        self.assertEqual(box._cache_bytes, 0)
        self.assertFalse(box._cache)

    def test_cover_fetch_is_reordered_from_header_ids_and_cached_by_version(self):
        raw, size = synthetic_jpeg()
        parts = transport_parts(raw)

        def download(url):
            return parts[0 if ".b_0?" in url else 1]

        with patch.object(box, "_download", side_effect=download) as get:
            first = box.fetch_image(logical_url(COVER, v="v1"))
            self.assertEqual(box.fetch_image(logical_url(COVER, v="v1")), first)
            self.assertEqual(get.call_count, 2)
            box.fetch_image(logical_url(COVER, v="v2"))
            self.assertEqual(get.call_count, 4)
        with Image.open(BytesIO(first[0])) as image:
            self.assertEqual(image.size, size)
            self.assertGreater(image.getpixel((20, 3))[0], 245)
            self.assertLess(image.getpixel((20, size[1] - 4))[0], 10)

    def test_v3_fetch_uses_manifest_order_and_full_real_decoder(self):
        raw, _ = synthetic_jpeg()
        parts = transport_parts(raw, True)
        locations = [CDN + "/immutable/first.bin?sig=c%2Fd", CDN + "/immutable/second.bin?sig=a%2Bb"]
        mapping = dict(zip(locations, parts))
        with patch.object(box, "_download", side_effect=mapping.__getitem__) as get:
            result = box.fetch_image(logical_url(manifest=encoded_manifest()))
        self.assertEqual(result, box.decode_image(parts, True))
        self.assertEqual({call.args[0] for call in get.call_args_list}, set(locations))

    def test_failed_decode_is_not_cached(self):
        url = logical_url(v="bad-data")
        with patch.object(box, "_download", return_value=b"incomplete"):
            with self.assertRaises(RuntimeError):
                box.fetch_image(url)
        self.assertIsNone(box._cache_get(url))

    def test_concurrent_identical_fetches_share_one_transport_and_decode(self):
        raw, _ = synthetic_jpeg()
        parts = transport_parts(raw)

        def download(url):
            time.sleep(0.01)
            return parts[0 if ".b_0?" in url else 1]

        url = logical_url(v="concurrent")
        with patch.object(box, "_download", side_effect=download) as get, ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(box.fetch_image, [url] * 4))
        self.assertEqual(get.call_count, 2)
        self.assertTrue(all(result == results[0] for result in results))

    def test_parallel_different_pages_keep_download_concurrency_bounded(self):
        raw, _ = synthetic_jpeg()
        parts = transport_parts(raw)
        lock = threading.Lock()
        active = peak = 0

        def download(url):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                time.sleep(0.01)
                return parts[0 if ".b_0?" in url else 1]
            finally:
                with lock:
                    active -= 1

        urls = [logical_url(v="concurrency-" + str(index)) for index in range(6)]
        with patch.object(box, "_download", side_effect=download), ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(box.fetch_image, urls))
        self.assertEqual(len(results), 6)
        self.assertLessEqual(peak, 4)
        self.assertEqual(active, 0)


class ComicboxHTTPIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from server import Application, Handler
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.app = Application()
        cls.thread = threading.Thread(target=lambda: cls.server.serve_forever(poll_interval=0.01), daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_http_proxy_decodes_legacy_cover_and_v3_manifest_using_only_mock_downloads(self):
        with box._cache_lock:
            box._cache.clear()
            box._cache_bytes = 0
        raw, size = synthetic_jpeg()
        legacy = transport_parts(raw)
        joined = transport_parts(raw, True)
        cover = logical_url(COVER, v="http-synthetic")
        manifest = logical_url(manifest=encoded_manifest())
        mapping = {
            CDN + "/break_2/static/upload/book/1/cover_pc.b_0?v=http-synthetic": legacy[0],
            CDN + "/break_2/static/upload/book/1/cover_pc.b_1?v=http-synthetic": legacy[1],
            CDN + "/immutable/first.bin?sig=c%2Fd": joined[0],
            CDN + "/immutable/second.bin?sig=a%2Bb": joined[1],
        }
        try:
            # Everything after the download boundary is production code:
            # HTTP routing -> image plan -> decrypt/unpad -> repair -> reorder.
            with patch.object(box, "_download", side_effect=mapping.__getitem__) as get:
                for logical in (cover, manifest):
                    with self.subTest(logical=logical[:100]):
                        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
                        try:
                            # Omit siteId: the trusted CDN must still select the
                            # Comicbox decoder rather than ordinary forwarding.
                            connection.request("GET", "/api/image?" + urlencode({"url": logical}))
                            response = connection.getresponse()
                            payload = response.read()
                            self.assertEqual(response.status, 200, payload[:200])
                            self.assertEqual(response.getheader("Content-Type"), "image/jpeg")
                            self.assertEqual(response.getheader("X-Content-Type-Options"), "nosniff")
                            self.assertIn("private", response.getheader("Cache-Control"))
                        finally:
                            connection.close()
                        with Image.open(BytesIO(payload)) as image:
                            image.load()
                            self.assertEqual(image.size, size)
                            self.assertGreater(image.getpixel((20, 3))[0], 245)
                            self.assertLess(image.getpixel((20, size[1] - 4))[0], 10)
                self.assertEqual(get.call_count, 4)
                self.assertEqual({call.args[0] for call in get.call_args_list}, set(mapping))
        finally:
            with box._cache_lock:
                box._cache.clear()
                box._cache_bytes = 0


if __name__ == "__main__":
    unittest.main()
