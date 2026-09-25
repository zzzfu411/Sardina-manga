"""Komiic's public reader protocol, with image tickets requested on demand.

Protocol evidence: the public /assets/index-CEEO0b8f.js uses imagesByChapterId
for the directory, getImageTickets for visible kids, then X-Image-Ticket when
fetching the returned CDN URL. /api/image/{kid} below is a LOCAL proxy marker;
this adapter never GETs that URL on komiic.com. It does not bypass source quotas.
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
from decimal import Decimal
from io import BytesIO
import json
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, unquote, unquote_plus, urlparse, urlunparse
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from PIL import Image, UnidentifiedImageError

from .native_sources import UA, _ssl_context, _urlopen

ORIGIN = "https://komiic.com"
CDN_HOST = "img.komiic.com"
MAX_BYTES = 12 * 1024 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_PIXELS = 32_000_000
MAX_CACHE_BYTES = 24 * 1024 * 1024
CACHE_SECONDS = 300
_cache = OrderedDict()
_cache_bytes = 0
_cache_lock = threading.Lock()
_locks = [threading.Lock() for _ in range(32)]
_gate = threading.BoundedSemaphore(2)

DETAIL_QUERY = """query comicDetails($comicId: ID!) {
  comicById(comicId: $comicId) { id title description status imageUrl authors { name } }
  chaptersByComicId(comicId: $comicId) { id serial type size }
}"""
IMAGES_QUERY = """query imagesByChapterId($chapterId: ID!) {
  imagesByChapterId(chapterId: $chapterId) { id kid width height }
}"""
TICKET_QUERY = """query getImageTickets($kids: [String!]!) {
  getImageTickets(kids: $kids) { url ticket kid width height expiresAt }
}"""


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _id(value):
    value = str(value)
    if not re.fullmatch(r"[0-9]{1,20}", value):
        raise ValueError("Komiic 作品或章节编号无效")
    return value


def _kid(value):
    # Current public metadata uses UUIDs; also accept bounded opaque URL-safe ids.
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", value):
        raise ValueError("Komiic 图片编号无效")
    return value


def _source_error(errors):
    text = " ".join(_text(item.get("message")) for item in errors if isinstance(item, dict))
    codes = {str((item.get("extensions") or {}).get("code", ""))
             for item in errors if isinstance(item, dict) and isinstance(item.get("extensions", {}), dict)}
    if "QUOTA_EXCEEDED" in codes or "quota" in text.lower():
        return "Komiic 今日图片配额已用尽，请稍后再试或在源站查看"
    if any(word in text.lower() for word in ("unauthorized", "forbidden", "permission", "login")):
        return "Komiic 源站限制了当前访问，请在源站查看或切换漫画源"
    return "Komiic 源站未能完成请求，请稍后重试"


def _query(query, variables):
    body = json.dumps({"query": query, "variables": variables}).encode()
    request = Request(ORIGIN + "/api/query", data=body,
                      headers={"User-Agent": UA, "Content-Type": "application/json",
                               "Origin": ORIGIN, "Referer": ORIGIN + "/"})
    try:
        with _urlopen(request, 25) as response:
            raw = response.read(MAX_JSON_BYTES + 1)
        if len(raw) > MAX_JSON_BYTES:
            raise RuntimeError("Komiic 数据响应过大")
        payload = json.loads(raw)
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise RuntimeError("Komiic 源站限制了当前访问，请在源站查看") from None
        if exc.code in (402, 429):
            raise RuntimeError("Komiic 当前配额或请求频率受限，请稍后再试") from None
        raise RuntimeError("Komiic 数据请求失败，请稍后重试") from None
    except (URLError, TimeoutError, ValueError):
        raise RuntimeError("Komiic 数据请求失败，请稍后重试") from None
    if not isinstance(payload, dict):
        raise RuntimeError("Komiic 返回了未知的数据格式")
    if payload.get("errors"):
        errors = payload["errors"]
        raise RuntimeError(_source_error(errors if isinstance(errors, list) else []))
    if not isinstance(payload.get("data"), dict):
        raise RuntimeError("Komiic 没有返回有效数据")
    return payload["data"]


def details(comic_id):
    comic_id = _id(comic_id)
    data = _query(DETAIL_QUERY, {"comicId": comic_id})
    comic, rows = data.get("comicById"), data.get("chaptersByComicId")
    if not isinstance(comic, dict) or str(comic.get("id")) != comic_id or not isinstance(rows, list):
        raise RuntimeError("Komiic 未返回所选作品和完整目录")
    chapters, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("Komiic 章节目录格式已变化")
        chapter_id = _id(row.get("id", ""))
        if chapter_id in seen:
            continue
        seen.add(chapter_id)
        serial, kind = _text(row.get("serial")), _text(row.get("type"))
        unit = {"book": "卷", "chapter": "话"}.get(kind)
        name = f"第 {serial} {unit}" if serial and unit else serial or "未命名章节"
        url = f"{ORIGIN}/comic/{comic_id}/chapter/{chapter_id}"
        rank = {"chapter": 0, "book": 1}.get(kind, 2)
        number = Decimal(serial) if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", serial) else None
        chapters.append(({"id": url, "url": url, "name": name, "group": kind},
                         (rank, number is None, number or Decimal(0), len(chapters))))
    chapters.sort(key=lambda item: item[1])
    result = [{**row, "order": index} for index, (row, _) in enumerate(chapters)]
    authors = comic.get("authors") or []
    return {"title": _text(comic.get("title")),
            "author": " / ".join(_text(author.get("name")) for author in authors if isinstance(author, dict) and _text(author.get("name"))),
            "description": _text(comic.get("description")), "status": _text(comic.get("status")),
            "coverUrl": _text(comic.get("imageUrl")), "sourceUrl": f"{ORIGIN}/comic/{comic_id}", "chapters": result,
            "catalogCompleteness": "complete"}


def images(chapter_id):
    data = _query(IMAGES_QUERY, {"chapterId": _id(chapter_id)})
    rows = data.get("imagesByChapterId")
    if not isinstance(rows, list) or len(rows) > 10000:
        raise RuntimeError("Komiic 图片目录格式已变化")
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("Komiic 图片目录格式已变化")
        kid = _kid(row.get("kid"))
        if kid not in seen:
            result.append(f"{ORIGIN}/api/image/{quote(kid, safe='')}")
            seen.add(kid)
    return result


def _validate_url(url, host):
    if not isinstance(url, str) or len(url) > 16000 or any(ord(char) <= 32 for char in url):
        raise ValueError("Komiic 图片地址无效")
    parsed = urlparse(url)
    if (parsed.scheme not in {"http", "https"} or parsed.hostname != host
            or parsed.username or parsed.password or parsed.port not in (None, 80, 443)
            or parsed.fragment or parsed.params):
        raise ValueError("Komiic 图片地址不在已接入的源站范围")
    return parsed


def _image_request(url):
    if not isinstance(url, str) or len(url) > 16000:
        raise ValueError("Komiic 图片地址无效")
    parsed = urlparse(url)
    if parsed.hostname == "komiic.com":
        parsed = _validate_url(url, "komiic.com")
        match = re.fullmatch(r"/api/image/([^/]+)", parsed.path)
        if not match or parsed.query:
            raise ValueError("Komiic 图片标记无效")
        kid = _kid(unquote(match[1]))
        return ("kid", kid), kid, None
    parsed = _validate_url(url, CDN_HOST)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    tickets = [value for name, value in query if name == "ticket"]
    if len(tickets) != 1:
        raise ValueError("Komiic 旧图片链接缺少有效票据")
    ticket = _validate_ticket(tickets[0])
    # Preserve other signed query parameters byte-for-byte when removing the
    # legacy local transport field; URL re-encoding can invalidate signatures.
    clean_query = "&".join(part for part in parsed.query.split("&")
                           if unquote_plus(part.partition("=")[0]) != "ticket")
    clean = urlunparse(parsed._replace(query=clean_query))
    return ("legacy", clean), clean, ticket


def is_image_url(url):
    try:
        _image_request(url)
        return True
    except (TypeError, ValueError):
        return False


def _validate_ticket(ticket):
    if not isinstance(ticket, str) or not re.fullmatch(r"[\x21-\x7e]{1,8192}", ticket):
        raise ValueError("Komiic 图片票据无效")
    return ticket


class _ImageRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = _validate_url(newurl, CDN_HOST)
        source = urlparse(req.full_url)
        if (target.scheme, target.hostname, target.port) != (source.scheme, source.hostname, source.port):
            raise ValueError("Komiic 图片跳转超出当前票据范围")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download(url, ticket):
    _validate_url(url, CDN_HOST)
    request = Request(url, headers={"User-Agent": UA, "Referer": ORIGIN + "/",
                                   "Origin": ORIGIN, "X-Image-Ticket": _validate_ticket(ticket)})
    opener = build_opener(_ImageRedirect(), HTTPSHandler(context=_ssl_context()))
    try:
        with opener.open(request, timeout=20) as response:
            mime = response.headers.get_content_type()
            blob = response.read(MAX_BYTES + 1)
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise RuntimeError("Komiic 图片票据已失效或访问受限，请重试或在源站查看") from None
        if exc.code in (402, 429):
            raise RuntimeError("Komiic 图片配额或请求频率受限，请稍后再试") from None
        raise RuntimeError("Komiic 图片下载失败，请稍后重试") from None
    except (URLError, TimeoutError):
        raise RuntimeError("Komiic 图片下载失败，请稍后重试") from None
    if not blob or len(blob) > MAX_BYTES:
        raise RuntimeError("Komiic 图片为空或超过大小限制")
    if mime not in {"image/jpeg", "image/png", "image/webp", "image/gif", "image/avif"}:
        raise RuntimeError("Komiic 源站未返回有效图片")
    try:
        with Image.open(BytesIO(blob)) as image:
            if image.width * image.height > MAX_PIXELS:
                raise RuntimeError("Komiic 图片像素数量超出限制")
            image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise RuntimeError("Komiic 图片内容不完整或无法解码") from None
    return blob, mime


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
        _cache[key] = (time.monotonic() + CACHE_SECONDS, value)
        _cache_bytes += len(value[0])
        while _cache_bytes > MAX_CACHE_BYTES or len(_cache) > 64:
            _, old = _cache.popitem(last=False)
            _cache_bytes -= len(old[1][0])


def fetch_image(url):
    key, identifier, legacy_ticket = _image_request(url)
    with _locks[hash(key) % len(_locks)]:
        cached = _cache_get(key)
        if cached:
            return cached
        with _gate:
            if legacy_ticket is None:
                rows = _query(TICKET_QUERY, {"kids": [identifier]}).get("getImageTickets")
                if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict) or rows[0].get("kid") != identifier:
                    raise RuntimeError("Komiic 未返回所选图片的有效票据")
                row = rows[0]
                image_url = row.get("url")
                _validate_url(image_url, CDN_HOST)
                ticket = _validate_ticket(row.get("ticket"))
                try:
                    expires = datetime.fromisoformat(row["expiresAt"].replace("Z", "+00:00"))
                    if expires.tzinfo is None or expires <= datetime.now(timezone.utc):
                        raise ValueError("expired")
                except (KeyError, AttributeError, TypeError, ValueError):
                    raise RuntimeError("Komiic 图片票据已失效，请重试") from None
            else:
                image_url, ticket = identifier, legacy_ticket
            result = _download(image_url, ticket)
            _cache_put(key, result)
            return result
