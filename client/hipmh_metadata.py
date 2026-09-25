"""Book metadata from the recorded public HIP /v1/manga endpoint.

The caller supplies an m:ID token. A successful response must name that same
numeric work; search recommendations and site defaults are never substitutes.
Metadata failures are explicit so the application can decide whether a known
book may keep its existing metadata while its independent directory loads.
"""
from __future__ import annotations

import base64
import binascii
from http.client import HTTPException
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlparse
from urllib.request import Request, urlopen

from .native_sources import HIP_API, UA, _ssl_context


MAX_BYTES = 1024 * 1024
COVER_ORIGIN = "https://cover.s3imgs.top"


class MetadataError(RuntimeError):
    """No verified metadata was returned for the requested work."""


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _work_id(mid):
    if not isinstance(mid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{3,64}", mid):
        raise ValueError("嬉皮作品标识无效")
    try:
        decoded = base64.b64decode(mid + "=" * (-len(mid) % 4), altchars=b"-_", validate=True).decode("ascii")
    except (ValueError, binascii.Error, UnicodeError):
        raise ValueError("嬉皮作品标识无效") from None
    if not re.fullmatch(r"m:[1-9][0-9]{0,19}", decoded):
        raise ValueError("嬉皮作品标识无效")
    return int(decoded[2:])


def _cover(value):
    value = _text(value)
    if not value or any(ord(char) <= 32 for char in value):
        return ""
    url = urljoin(COVER_ORIGIN + "/", value) if value.startswith("/") and not value.startswith("//") else value
    try:
        parsed = urlparse(url)
        if (parsed.scheme not in {"https", "http"} or parsed.hostname != "cover.s3imgs.top"
                or parsed.username or parsed.password or parsed.port not in {None, 80, 443}):
            return ""
    except ValueError:
        return ""
    return url


def _parse(payload, expected_id):
    if not isinstance(payload, dict) or payload.get("code") not in (200, "200"):
        raise MetadataError("嬉皮漫画未返回有效作品详情，请稍后重试")
    book = payload.get("data")
    if not isinstance(book, dict):
        raise MetadataError("嬉皮漫画详情格式已变化，请稍后重试")
    actual_id = book.get("id")
    if (isinstance(actual_id, bool) or not isinstance(actual_id, (int, str))
            or not re.fullmatch(r"[1-9][0-9]{0,19}", str(actual_id))
            or int(actual_id) != expected_id):
        raise MetadataError("嬉皮漫画返回的作品与所选作品不一致")
    title = _text(book.get("title"))
    if not title:
        raise MetadataError("嬉皮漫画未返回所选作品名称")
    authors = book.get("authors")
    names = []
    if isinstance(authors, list):
        for author in authors:
            name = _text(author.get("name")) if isinstance(author, dict) else ""
            if name and name not in names:
                names.append(name)
    return {
        "title": title,
        "author": " / ".join(names),
        "coverUrl": _cover(book.get("vertical_image_url")) or _cover(book.get("cover_image_url")),
        "description": _text(book.get("description")),
        "status": _text(book.get("status")),
    }


def metadata(mid):
    """Return five string fields, or raise ValueError/MetadataError.

    Only this work's JSON metadata is requested. Cover URLs are normalized but
    never downloaded, and chapters are left to the existing directory adapter.
    """
    expected_id = _work_id(mid)
    request = Request(HIP_API + "/v1/manga?mid=" + quote(mid, safe=""),
                      headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urlopen(request, timeout=20, context=_ssl_context()) as response:
            raw = response.read(MAX_BYTES + 1)
    except HTTPError as exc:
        raise MetadataError(f"嬉皮漫画详情请求失败（HTTP {exc.code}），请稍后重试") from None
    except (URLError, TimeoutError, OSError, HTTPException):
        raise MetadataError("嬉皮漫画详情暂时无法连接，请稍后重试") from None
    if len(raw) > MAX_BYTES:
        raise MetadataError("嬉皮漫画详情响应过大")
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise MetadataError("嬉皮漫画未返回有效 JSON 详情") from None
    return _parse(payload, expected_id)
