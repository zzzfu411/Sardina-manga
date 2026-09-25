"""Comicbox's published image transport, decoded on the local server.

Protocol evidence: output/source-parity/merge_split_file_monga.js. This module
never executes downloaded scripts. The website's binary header is authoritative
for strip order, including covers; a logical .jpg is not an ordinary JPEG URL.
"""
from __future__ import annotations

import base64
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import hashlib
from io import BytesIO
import json
import re
import threading
import time
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from PIL import Image, UnidentifiedImageError

from .native_sources import UA, _ssl_context

ORIGIN = "https://www.comicbox.xyz"
IMAGE_DOMAINS = ("ccavbox.com",)
MAX_BYTES = 12 * 1024 * 1024
MAX_PIXELS = 32_000_000
MAX_CACHE_BYTES = 32 * 1024 * 1024
AES_KEY, AES_IV = b"aaaaaaaaaaaaaaaa", b"0123456789aaaaaa"
STRIP_COUNTS = (44, 48, 52, 56, 60, 64, 68, 72, 76, 80)
_gate = threading.BoundedSemaphore(2)
_locks = [threading.Lock() for _ in range(32)]
_cache_lock = threading.Lock()
_cache = OrderedDict()
_cache_bytes = 0
_version_lock = threading.Lock()
_version = (0.0, "")


def validate_image_url(url):
    if not isinstance(url, str) or len(url) > 20_000:
        raise ValueError("歪歪图片地址过长或无效")
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if (parsed.scheme not in {"https", "http"} or parsed.username or parsed.password
            or parsed.port not in (None, 80, 443)
            or not (host == "ccavbox.com" or host.endswith(".ccavbox.com"))):
        raise ValueError("歪歪图片地址不在已接入的 CDN 范围")
    return url


class _Redirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_image_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download(url):
    validate_image_url(url)
    opener = build_opener(_Redirect(), HTTPSHandler(context=_ssl_context()))
    request = Request(url, headers={"User-Agent": UA, "Referer": ORIGIN + "/"})
    with opener.open(request, timeout=15) as response:
        data = response.read(MAX_BYTES + 1)
    if not data or len(data) > MAX_BYTES:
        raise RuntimeError("歪歪图片分片为空或超过大小限制")
    return data


def current_cache_version():
    """Compatibility-mode URLs lack the HTML version; fetch it once per 5 min."""
    global _version
    with _version_lock:
        if _version[0] > time.monotonic():
            return _version[1]
        # This fixed source URL never comes from the incoming image query.
        from . import comicbox
        value = comicbox.cache_key(comicbox.fetch_page(ORIGIN + "/"))
        _version = (time.monotonic() + 300, value)
        return value


def _one(params, key, default=""):
    values = params.get(key, [default])
    if len(values) != 1:
        raise ValueError("歪歪图片参数重复")
    return values[0]


def _manifest_chunks(encoded):
    if len(encoded) > 16_000:
        raise ValueError("歪歪图片清单过大")
    try:
        manifest = json.loads(base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True))
        renditions = manifest["variants"]["jpeg"]["renditions"]
        rendition = next(item for item in renditions if item.get("role") == "page")
        if rendition.get("decoder") != "monga-v2-encrypt-then-split":
            raise ValueError("不支持的解码方式")
        chunks = rendition["chunks"]
        if not isinstance(chunks, list) or not 1 <= len(chunks) <= 8:
            raise ValueError("分片数量无效")
        if any(type(chunk.get("index")) is not int or type(chunk.get("count")) is not int for chunk in chunks):
            raise ValueError("分片编号无效")
        chunks = sorted(chunks, key=lambda chunk: chunk["index"])
        if any(chunk["index"] != index or chunk["count"] != len(chunks) for index, chunk in enumerate(chunks)):
            raise ValueError("分片编号不连续")
        return [validate_image_url(chunk["url"]) for chunk in chunks]
    except (KeyError, TypeError, AttributeError, StopIteration, ValueError) as exc:
        raise ValueError("歪歪图片清单无效或使用了未接入的格式") from exc


def image_plan(url):
    """Return explicit chunk URLs and whether ciphertext is joined first."""
    parsed = urlparse(validate_image_url(url))
    query = parse_qs(parsed.query, keep_blank_values=True)
    manifest = _one(query, "manifest")
    if manifest:
        # V3's JPEG rendition is encrypt-then-split, just like the AVIF path.
        return _manifest_chunks(manifest), True
    match = re.fullmatch(r"/(break_2|break_avif)/static/upload/book/\d+/(?:\d+/\d+|cover(?:_pc)?)\.(?:jpe?g|png|gif|avif|webp)", parsed.path, re.I)
    if not match:
        raise ValueError("歪歪图片路径或传输版本尚未接入")
    version = _one(query, "v") or current_cache_version()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", version):
        raise ValueError("歪歪图片缓存版本无效")
    protocol = match[1]
    hosts_value = _one(query, "hosts")
    hosts = hosts_value.split(",") if hosts_value else [f"{parsed.scheme}://{parsed.netloc}/{protocol}"] * 2
    if len(hosts) != 2:
        raise ValueError("歪歪图片分片主机数量不匹配")
    suffix = re.sub(r"\.[^.]+$", "", parsed.path[len(protocol) + 1:])
    urls = []
    for index, host in enumerate(hosts):
        hp = urlparse(validate_image_url(host))
        if hp.path.rstrip("/") != "/" + protocol or hp.query or hp.fragment:
            raise ValueError("歪歪图片分片版本不匹配")
        urls.append(urlunparse((hp.scheme, hp.netloc, "/" + protocol + suffix + f".b_{index}", "", urlencode({"v": version}), "")))
    return urls, protocol == "break_avif"


def _decrypt(data):
    if not data or len(data) % 16:
        raise RuntimeError("歪歪图片分片不完整")
    try:
        decryptor = Cipher(algorithms.AES(AES_KEY), modes.CBC(AES_IV)).decryptor()
        padded = decryptor.update(data) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        return unpadder.update(padded) + unpadder.finalize()
    except ValueError as exc:
        raise RuntimeError("歪歪图片校验失败，请重新打开章节") from exc


def decrypt_parts(parts, join_first=False):
    if not parts or any(not part for part in parts) or sum(map(len, parts)) > MAX_BYTES:
        raise RuntimeError("歪歪图片分片缺失或超过大小限制")
    return _decrypt(b"".join(parts)) if join_first else b"".join(_decrypt(part) for part in parts)


def restore_header(data):
    if len(data) < 12 or data[1] != 0:
        raise RuntimeError("歪歪图片不是已支持的漫画编码")
    signatures = {0: bytes.fromhex("ffd8ffe000104a4649460001"), 3: b"GIF89a", 4: bytes.fromhex("000000206674797061766966")}
    signature = signatures.get(data[0])
    if signature is None:
        raise RuntimeError("歪歪图片编码格式尚未接入")
    book_id, page_number = str(int.from_bytes(data[2:4], "big")), str(int.from_bytes(data[4:8], "big"))
    return signature + data[len(signature):], book_id, page_number


def strip_count(book_id, page_number):
    last = hashlib.md5((str(book_id) + str(page_number)).encode()).hexdigest()[-1]
    return STRIP_COUNTS[ord(last) % 10]


def unscramble(image, book_id, page_number):
    width, height = image.size
    count = strip_count(book_id, page_number)
    unit, remainder = divmod(height, count)
    result = Image.new("RGB", (width, height), "white")
    for index in range(count):
        size = unit + (remainder if index == 0 else 0)
        if not size:
            continue
        source_y = height - unit * (index + 1) - remainder
        target_y = unit * index + (remainder if index else 0)
        strip = image.crop((0, source_y, width, source_y + size))
        try:
            result.paste(strip, (0, target_y))
        finally:
            strip.close()
    return result


def decode_image(parts, join_first=False):
    data, book_id, page_number = restore_header(decrypt_parts(parts, join_first))
    try:
        with Image.open(BytesIO(data)) as image:
            if image.width < 1 or image.height < 1 or image.width * image.height > MAX_PIXELS:
                raise RuntimeError("歪歪图片像素数量超出限制")
            if image.format not in {"JPEG", "GIF", "AVIF"}:
                raise RuntimeError("歪歪图片未返回受支持的格式")
            image.load()
            # Source templates use this for all cropped assets, covers included.
            restored = unscramble(image, book_id, page_number)
            try:
                output = BytesIO()
                restored.save(output, format="JPEG", quality=90)
                data = output.getvalue()
            finally:
                restored.close()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise RuntimeError("歪歪图片解码失败，请重试或换源") from exc
    if len(data) > MAX_BYTES:
        raise RuntimeError("还原后的图片过大")
    return data, "image/jpeg"


def _cache_get(key):
    global _cache_bytes
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > time.monotonic():
            _cache.move_to_end(key)
            return hit[1]
        if hit:
            _cache_bytes -= len(hit[1][0])
            del _cache[key]
    return None


def _cache_put(key, value):
    global _cache_bytes
    with _cache_lock:
        old = _cache.pop(key, None)
        if old:
            _cache_bytes -= len(old[1][0])
        _cache[key] = (time.monotonic() + 300, value)
        _cache_bytes += len(value[0])
        while _cache_bytes > MAX_CACHE_BYTES or len(_cache) > 64:
            _, old = _cache.popitem(last=False)
            _cache_bytes -= len(old[1][0])


def fetch_image(url):
    validate_image_url(url)
    with _locks[hash(url) % len(_locks)]:
        cached = _cache_get(url)
        if cached:
            return cached
        with _gate:
            chunks, join_first = image_plan(url)
            with ThreadPoolExecutor(max_workers=2) as pool:
                parts = list(pool.map(_download, chunks))
            result = decode_image(parts, join_first)
            _cache_put(url, result)
            return result
