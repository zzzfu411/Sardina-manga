"""Bounded metadata transport for explicitly registered discovery adapters.

Adapters construct their own observed endpoints from validated selections.
Remote scripts and chapter/image APIs are never used by this transport.
"""
from __future__ import annotations

from datetime import datetime, timezone
from http.client import HTTPException
import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from .native_sources import UA, _ssl_context

MAX_BYTES = 4 * 1024 * 1024
TIMEOUT = 12
DEADLINE = 25


class DiscoveryError(RuntimeError):
    pass


def text(value, limit=2000):
    return re.sub(r"\s+", " ", value).strip()[:limit] if isinstance(value, str) else ""


def _url(url, hosts):
    if (not isinstance(url, str) or not url or len(url) > 4096 or not hosts
            or any(ord(c) <= 32 for c in url) or "\\" in url):
        raise ValueError("发现来源地址无效")
    try:
        p = urlsplit(url)
        if (p.scheme not in {"http", "https"} or p.hostname not in hosts or p.username or p.password
                or p.port not in (None, 80, 443) or p.fragment):
            raise ValueError
    except ValueError:
        raise ValueError("发现来源地址不在允许的范围内") from None
    return url


class _Redirect(HTTPRedirectHandler):
    max_repeats = 1
    max_redirections = 1

    def __init__(self, url, hosts):
        self.expected, self.hosts = _url(url, hosts), hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if _url(newurl, self.hosts) != self.expected:
            raise DiscoveryError("来源跳转到了其他页面，请稍后重试")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def read_text(url, *, hosts, headers=None, data=None, deadline=None, encoding=None, max_bytes=MAX_BYTES):
    """Read one metadata response, with exact endpoint redirects and a deadline.

    `hosts` is an adapter-owned tuple of exact hosts. `data`, when supplied,
    is a bounded bytes payload for an observed, read-only POST list endpoint.
    An adapter may supply `encoding` only after verifying a legacy charset.
    """
    hosts = tuple(hosts)
    url = _url(url, hosts)
    if data is not None and (not isinstance(data, bytes) or len(data) > 65536):
        raise ValueError("发现请求内容无效")
    if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_BYTES:
        raise ValueError("发现响应大小限制无效")
    deadline = time.monotonic() + DEADLINE if deadline is None else deadline
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise DiscoveryError("发现列表请求超时")
    request_headers = {"User-Agent": UA, "Accept": "text/html,application/json", "Accept-Encoding": "identity", "Referer": url}
    request_headers.update(headers or {})
    opener = build_opener(_Redirect(url, hosts), HTTPSHandler(context=_ssl_context()))
    try:
        with opener.open(Request(url, data=data, headers=request_headers), timeout=min(TIMEOUT, remaining)) as response:
            if response.geturl() != url:
                raise DiscoveryError("来源返回了其他页面")
            content_type = response.headers.get_content_type()
            if content_type not in {"text/html", "application/xhtml+xml", "application/json", "text/plain"}:
                raise DiscoveryError("来源没有返回可识别的列表")
            size = response.headers.get("Content-Length", "")
            if size.isdecimal() and int(size) > max_bytes:
                raise DiscoveryError("来源列表超过大小限制")
            parts, total = [], 0
            while True:
                if time.monotonic() >= deadline:
                    raise DiscoveryError("发现列表请求超时")
                part = response.read(min(65536, max_bytes + 1 - total))
                if not part:
                    break
                parts.append(part); total += len(part)
                if total > max_bytes:
                    raise DiscoveryError("来源列表超过大小限制")
            charset = encoding or response.headers.get_content_charset() or "utf-8-sig"
            if charset.lower().replace("_", "-") not in {"utf-8", "utf-8-sig", "gbk", "gb2312", "gb18030", "big5"}:
                raise DiscoveryError("来源列表使用了未知编码")
            return b"".join(parts).decode(charset, "strict")
    except HTTPError as exc:
        raise DiscoveryError(f"来源列表请求失败（HTTP {exc.code}）") from exc
    except (URLError, OSError, HTTPException, UnicodeError) as exc:
        raise DiscoveryError("来源列表连接失败，请稍后重试") from exc


def read_json(url, **kwargs):
    try:
        return json.loads(read_text(url, **kwargs))
    except (json.JSONDecodeError, RecursionError) as exc:
        raise DiscoveryError("来源列表返回了无效数据") from exc


def image_url(value, base, hosts):
    """Validate a source-provided cover URL against adapter-owned CDN roots."""
    if not isinstance(value, str) or not value or len(value) > 4096 or any(ord(c) <= 32 for c in value):
        return ""
    try:
        url = urljoin(base, value)
        p = urlsplit(url)
        if (p.scheme not in {"http", "https"} or p.username or p.password or p.port not in (None, 80, 443)
                or p.fragment or not any(p.hostname == h or (p.hostname or "").endswith("." + h) for h in hosts)
                or re.search(r"(?:^|/)(?:nopic|noimage|default_cover|placeholder|load)\.[^/]+$", p.path, re.I)):
            return ""
        return url
    except ValueError:
        return ""


def result(site, name, kind, period, page, items, *, has_more, source_url, label, note=""):
    if not isinstance(items, list) or len(items) > 5000 or type(has_more) is not bool:
        raise DiscoveryError("来源列表结构发生变化")
    seen = set()
    for row in items:
        if (not isinstance(row, dict) or not text(row.get("title")) or not row.get("detailUrl")
                or row.get("siteId", site) != site or row["detailUrl"] in seen):
            raise DiscoveryError("来源作品信息缺失或重复")
        seen.add(row["detailUrl"])
        if "rank" in row and (type(row["rank"]) is not int or row["rank"] < 1):
            raise DiscoveryError("来源榜单名次无效")
        row.update(siteId=site, siteName=name)
    value = {"siteId": site, "siteName": name, "kind": kind, "period": period, "page": page,
             "items": items, "hasMore": has_more, "sourceUrl": source_url, "label": label,
             "fetchedAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")}
    if note:
        value["paginationNote"] = note
    return value
