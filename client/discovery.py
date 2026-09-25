"""Verified public lists and on-demand covers; no remote scripts or chapters.

Manhuagui publishes a complete seven-day update feed in one HTML page. We
paginate that complete list locally. Manben's own update page starts its
read-only pagination POST at pageindex=3 after the initial HTML batch.
Application-level caching and explicit refresh are owned by server.py.
"""
from __future__ import annotations

from datetime import datetime, timezone
from copy import deepcopy
import html
import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit, urlencode, parse_qs
from urllib.request import Request, HTTPRedirectHandler, HTTPSHandler, build_opener

from .html_metadata import Element, image_of, known_status, parse_html, text_of
from .native_sources import UA, _ssl_context

_SITES = {"manhuagui": ("漫画柜", "https://www.manhuagui.com"), "manben": ("漫本", "https://www.manben.com")}
_PERIODS = {"day": ("日排行", "/rank/"), "week": ("周排行", "/rank/week.html"),
            "month": ("月排行", "/rank/month.html"), "total": ("总排行", "/rank/total.html")}
_MAX_PAGE = 1000
_PAGE_SIZE = 24
_MAX_RESPONSE = 2 * 1024 * 1024
_MAX_ITEMS = 5000
_TIMEOUT = 12
_DEADLINE = 25
_MANBEN_MORE = "https://www.manben.com/mh-updated/pagerdata.ashx"
_EMPTY = Element("empty")


class DiscoveryError(RuntimeError):
    """An unavailable or structurally changed source must not appear empty."""


def sources():
    built_in = [{"siteId": site, "siteName": name, "coverLookup": True, "modes": [
        {"kind": "popular", "label": "热门榜", "periods":
            [{"id": key, "label": value[0]} for key, value in _PERIODS.items()] if site == "manhuagui" else [], "maxPage": 1},
        {"kind": "latest", "label": "最近更新", "periods": [], "maxPage": _MAX_PAGE},
    ]} for site, (name, _) in _SITES.items()]
    result, seen = built_in, set(_SITES)
    for adapter in _extensions():
        for source in adapter.sources():
            if source["siteId"] in seen:
                raise RuntimeError("发现来源重复注册")
            seen.add(source["siteId"])
            result.append(deepcopy(source))
    return result


def _extensions():
    # Keep source families independent of the legacy HTML/cover parsers.
    from . import discovery_html, discovery_apk, discovery_api
    return discovery_html, discovery_apk, discovery_api


def image_domains():
    return tuple(dict.fromkeys(host for adapter in _extensions() for host in getattr(adapter, "IMAGE_DOMAINS", ())))


def normalize_request(body):
    if not isinstance(body, dict):
        raise ValueError("发现请求必须是对象")
    if set(body) - {"siteId", "kind", "period", "page", "refresh"}:
        raise ValueError("发现请求包含不支持的参数")
    site = body.get("siteId", "manhuagui")
    kind = body.get("kind", "popular")
    available = {source["siteId"]: source for source in sources()}
    if not isinstance(site, str) or site not in available:
        raise ValueError("未启用的发现来源")
    if not isinstance(kind, str) or kind not in {"popular", "latest"}:
        raise ValueError("不支持的发现类型")
    mode = next((item for item in available[site]["modes"] if item["kind"] == kind), None)
    if mode is None:
        raise ValueError("该来源尚未接入此类发现列表")
    periods = {item["id"] for item in mode["periods"]}
    default = mode["periods"][0]["id"] if mode["periods"] else ""
    period = body.get("period", default)
    if not isinstance(period, str) or (period not in periods if periods else period != ""):
        raise ValueError("该来源不支持此榜单周期")
    page = body.get("page", 1)
    if isinstance(page, str) and re.fullmatch(r"[1-9]\d{0,3}", page):
        page = int(page)
    if type(page) is not int or not 1 <= page <= _MAX_PAGE:
        raise ValueError("发现页码必须是 1 至 1000 的整数")
    if page > mode.get("maxPage", _MAX_PAGE):
        raise ValueError("页码超出此列表已接入的范围")
    return site, kind, period, page


def _cover_endpoint(url):
    """Canonical book pages only; this does not extend the list allowlist."""
    if not isinstance(url, str):
        raise ValueError("封面请求需要原站作品地址")
    patterns = {
        "manhuagui": r"https://www\.manhuagui\.com/comic/[1-9][0-9]{0,11}/?",
        "manben": r"https://www\.manben\.com/mh-(?!(?:ranklist(?:-[^/]*)?|updated|list)/?$)[A-Za-z0-9][A-Za-z0-9_-]{0,199}/?",
    }
    if not any(re.fullmatch(pattern, url) for pattern in patterns.values()):
        raise ValueError("封面请求只接受已接入来源的 HTTPS 作品地址")
    return url.rstrip("/") + "/"


def normalize_cover_request(body):
    if not isinstance(body, dict) or set(body) - {"siteId", "detailUrl", "refresh"}:
        raise ValueError("封面请求参数无效")
    site = body.get("siteId")
    if not isinstance(site, str) or site not in _SITES:
        raise ValueError("未启用的封面来源")
    url = _cover_endpoint(body.get("detailUrl"))
    if urlsplit(url).netloc != urlsplit(_SITES[site][1]).netloc:
        raise ValueError("封面作品地址与来源不匹配")
    refresh = body.get("refresh", False)
    if type(refresh) is not bool:
        raise ValueError("刷新参数无效")
    return site, url, refresh


def _endpoint(url):
    """Only the observed list endpoints, never user-supplied URLs."""
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port is not None
                or parsed.query or parsed.fragment or any(c.isspace() for c in url)):
            raise ValueError
        allowed = {
            "www.manhuagui.com": {path for _, path in _PERIODS.values()} | {"/update/"},
            "www.manben.com": {"/mh-ranklist/", "/mh-updated/", "/mh-updated/pagerdata.ashx"},
        }
        if parsed.netloc not in allowed or parsed.path not in allowed[parsed.netloc]:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError("发现地址不在允许的列表内") from None
    return url


class _Redirect(HTTPRedirectHandler):
    max_repeats = 1
    max_redirections = 1

    def __init__(self, expected, *, book_cover=False):
        self.book_cover = book_cover
        self.expected = (_cover_endpoint if book_cover else _endpoint)(expected)

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate = _cover_endpoint if self.book_cover else _endpoint
        if validate(newurl) != self.expected or newurl != self.expected:
            raise DiscoveryError("来源重定向到了其他页面，请稍后重试")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download(url, *, pageindex=None, deadline=None, book_cover=False):
    url = (_cover_endpoint if book_cover else _endpoint)(url)
    subject = "作品页" if book_cover else "列表"
    if (pageindex is not None and (url != _MANBEN_MORE or type(pageindex) is not int
                                  or not 3 <= pageindex <= _MAX_PAGE + 2)):
        raise ValueError("无效的来源分页请求")
    if (url == _MANBEN_MORE) != (pageindex is not None):
        raise ValueError("来源分页必须使用已验证的请求方法")
    deadline = deadline if deadline is not None else time.monotonic() + _DEADLINE
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise DiscoveryError(f"发现{subject}请求超时")
    headers = {"User-Agent": UA, "Accept": "text/html,application/json", "Accept-Encoding": "identity",
               "Referer": "https://www.manben.com/mh-updated/" if pageindex else url}
    data = None
    if pageindex is not None:
        data = urlencode({"t": 8, "pageindex": pageindex, "sc": 1}).encode("ascii")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    opener = build_opener(_Redirect(url, book_cover=book_cover), HTTPSHandler(context=_ssl_context()))
    try:
        with opener.open(Request(url, data=data, headers=headers), timeout=min(_TIMEOUT, remaining)) as response:
            if response.geturl() != url:
                raise DiscoveryError("来源返回了其他页面")
            allowed_types = {"text/html", "application/xhtml+xml"} if book_cover else {"text/html", "application/json", "text/plain"}
            if response.headers.get_content_type() not in allowed_types:
                raise DiscoveryError(f"来源返回的{subject}格式不受支持")
            size = response.headers.get("Content-Length")
            if size and size.isdecimal() and int(size) > _MAX_RESPONSE:
                raise DiscoveryError(f"来源{subject}超过大小限制")
            pieces, total = [], 0
            while True:
                if time.monotonic() >= deadline:
                    raise DiscoveryError(f"发现{subject}请求超时")
                chunk = response.read(min(65536, _MAX_RESPONSE + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > _MAX_RESPONSE:
                    raise DiscoveryError(f"来源{subject}超过大小限制")
                pieces.append(chunk)
            return b"".join(pieces).decode("utf-8-sig", "strict")
    except HTTPError as error:
        raise DiscoveryError(f"来源{subject}访问失败（HTTP {error.code}）") from error
    except (URLError, OSError, UnicodeError) as error:
        raise DiscoveryError(f"来源{subject}连接失败或返回无效编码，请稍后重试") from error


def _plain(value, limit=2000):
    return re.sub(r"\s+", " ", html.unescape(value)).strip()[:limit] if isinstance(value, str) else ""


def _tree(source):
    root = parse_html(source)
    if re.search(r"安全验证|人机验证|访问验证|Just a moment|Attention Required", text_of(root.first("title")), re.I) or "cf-chl-" in source:
        raise DiscoveryError("来源要求访问验证，暂时无法读取列表")
    return root


def _book_url(site, raw):
    if not isinstance(raw, str) or not raw or any(c.isspace() for c in raw):
        raise DiscoveryError("来源列表缺少作品地址")
    url = urljoin(_SITES[site][1], raw)
    try:
        parsed = urlsplit(url)
    except ValueError as error:
        raise DiscoveryError("来源列表作品地址格式发生变化") from error
    pattern = r"/comic/\d{1,12}/?" if site == "manhuagui" else r"/mh-(?!(?:ranklist(?:-[^/]*)?|updated|list)/?$)[A-Za-z0-9_-]+/?"
    if (parsed.scheme not in {"http", "https"} or parsed.netloc != urlsplit(_SITES[site][1]).netloc
            or parsed.query or parsed.fragment or not re.fullmatch(pattern, parsed.path)):
        raise DiscoveryError("来源列表作品地址格式发生变化")
    return _SITES[site][1] + parsed.path.rstrip("/") + "/"


def _cover(site, raw):
    if not isinstance(raw, str) or not raw:
        return ""
    url = urljoin(_SITES[site][1], raw)
    try:
        p = urlsplit(url)
        roots = ("mhgui.com", "hamreus.com") if site == "manhuagui" else ("cdndm5.com",)
        if (p.scheme not in {"https", "http"} or p.username or p.password or p.port not in (None, 80, 443)
                or not any(p.hostname == domain or (p.hostname or "").endswith("." + domain) for domain in roots)):
            return ""
    except ValueError:
        return ""
    return url


def _only(nodes, label):
    found = list(nodes)
    if len(found) != 1:
        raise DiscoveryError(f"来源作品页的{label}结构发生变化，无法读取封面")
    return found[0]


def _direct(node, *, cls=None, tag=None):
    return (child for child in node.children if isinstance(child, Element)
            and (tag is None or child.tag == tag)
            and (cls is None or cls in child.attrs.get("class", "").split()))


def _main_cover(site, source, detail_url):
    """Read only the identified book's primary cover, never recommendations.

    The sources' main cover, title and identity markers are observed HTML.
    Share links are parsed as data only; their destinations are never fetched.
    No chapter directory, inline script, or guessed image path is used.
    """
    root = _tree(source)
    identities = []
    for node in root.all("link"):
        if "canonical" in node.attrs.get("rel", "").lower().split():
            identities.append(node.attrs.get("href", ""))
    for node in root.all("meta"):
        if node.attrs.get("property", "").lower() == "og:url":
            identities.append(node.attrs.get("content", ""))
    if site == "manhuagui":
        scope = _only(root.all("div", cls="book-cont"), "主作品区")
        info = _only(_direct(scope, cls="book-detail"), "作品信息")
        title = text_of(_only(info.all("h1"), "作品标题"))
        cover = _only(_direct(scope, cls="book-cover"), "主封面区")
        image = _only(_only(_direct(cover, cls="hcover"), "主封面").all("img"), "主封面图片")
        crumb = _only(root.all("div", cls="crumb"), "作品位置")
        links = [node for node in crumb.all("a") if re.fullmatch(
            r"(?:https?://www\.manhuagui\.com)?/comic/[0-9]+/?", node.attrs.get("href", ""))]
        identity = _only(links, "作品地址")
        identities.append(identity.attrs["href"])
        if text_of(identity) != title:
            raise DiscoveryError("来源作品标题不一致，无法读取封面")
    else:
        scope = _only(root.all("div", cls="comicInfo"), "主作品区")
        info = _only(_direct(scope, cls="info"), "作品信息")
        title_node = _only(_direct(info, cls="title"), "作品标题")
        # The same title paragraph also contains rating/score descendants.
        title = _plain(" ".join(child for child in title_node.children if isinstance(child, str)))
        cover = _only(_direct(scope, cls="cover"), "主封面区")
        image = _only(_only(_direct(cover, cls="img"), "主封面").all("img"), "主封面图片")
        share = _only(info.all(cls="shareDetail"), "作品分享地址")
        share_urls = []
        try:
            for link in share.all("a"):
                query = urlsplit(link.attrs.get("href", "")).query
                share_urls.extend(parse_qs(query, max_num_fields=20).get("url", []))
        except ValueError as error:
            raise DiscoveryError("来源作品地址格式发生变化，无法读取封面") from error
        if not share_urls:
            raise DiscoveryError("来源缺少作品身份标记，无法读取封面")
        identities.extend(share_urls)
    if not title or any(_book_url(site, value) != detail_url for value in identities):
        raise DiscoveryError("来源返回了其他作品，无法读取封面")
    alt = _plain(image.attrs.get("alt", ""))
    if alt and alt != title:
        raise DiscoveryError("来源主封面与作品标题不一致")
    result = _cover(site, image_of(image, detail_url))
    if (not result or len(result) > 2048 or any(c.isspace() or ord(c) < 32 for c in result)
            or urlsplit(result).fragment):
        raise DiscoveryError("来源未提供有效的作品主封面，请稍后重试")
    return result


def book_cover(site_id, detail_url):
    """One bounded HTML request; return a real main-cover URL or fail clearly."""
    site_id, detail_url, _ = normalize_cover_request({"siteId": site_id, "detailUrl": detail_url})
    source = _download(detail_url, book_cover=True)
    return {"siteId": site_id, "detailUrl": detail_url,
            "coverUrl": _main_cover(site_id, source, detail_url)}


def _item(site, title, url, **extra):
    title = _plain(title, 500)
    if not title:
        raise DiscoveryError("来源列表缺少作品标题")
    return {"siteId": site, "siteName": _SITES[site][0], "title": title, "detailUrl": _book_url(site, url),
            "coverUrl": "", "author": "", "latestChapter": "", "description": "", "status": "", **extra}


def _unique(items, *, ranked=False):
    if not items or len(items) > _MAX_ITEMS:
        raise DiscoveryError("来源没有返回有效列表或条目数异常")
    result, seen = [], set()
    for item in items:
        if item["detailUrl"] in seen:
            if ranked:
                raise DiscoveryError("来源榜单有重复作品")
            continue
        if ranked and item["rank"] != len(result) + 1:
            raise DiscoveryError("来源榜单名次不完整")
        seen.add(item["detailUrl"])
        result.append(item)
    return result


def _gui_popular(source, period):
    root = _tree(source)
    table = root.first("table", cls="rank-detail")
    if not table or _PERIODS[period][0] not in text_of(root.first("title")):
        raise DiscoveryError("来源榜单类型或页面结构发生变化")
    items = []
    for row in table.all("tr"):
        title = row.first(cls="rank-title")
        if title is None:
            # The source has table headers and blank separator rows.
            if row.first(cls="rank-no") is not None:
                raise DiscoveryError("来源榜单条目不完整")
            continue
        link = title.first("a") or _EMPTY
        number = text_of(row.first(cls="rank-no"))
        if not number.isdecimal():
            raise DiscoveryError("来源榜单缺少名次")
        items.append(_item("manhuagui", text_of(link), link.attrs.get("href"), rank=int(number),
                           author=" / ".join(a.text() for a in (row.first(cls="rank-author") or _EMPTY).all("a")),
                           latestChapter=text_of(row.first(cls="rank-update")),
                           updatedAtText=text_of(row.first(cls="rank-time")), status=known_status(text_of(title))))
    return _unique(items, ranked=True)


def _gui_latest(source):
    root = _tree(source)
    scope = root.first(cls="latest-cont")
    if not scope or "7天内" not in text_of(root.first("title")):
        raise DiscoveryError("来源更新列表范围或结构发生变化")
    items = []
    for group in scope.all(cls="latest-list"):
        for row in group.all("li"):
            link = (row.first("p", cls="ell") or _EMPTY).first("a") or _EMPTY
            items.append(_item("manhuagui", text_of(link), link.attrs.get("href"),
                               coverUrl=_cover("manhuagui", image_of(row.first("a", cls="cover"), _SITES["manhuagui"][1])),
                               latestChapter=text_of(row.first(cls="tt")), updatedAtText=text_of(row.first(cls="dt"))))
    return _unique(items)


def _manben_popular(source):
    root = _tree(source)
    nav = root.first(cls="rankNavNew")
    scope = root.first(cls="searchResult")
    if not scope or text_of((nav or _EMPTY).first(cls="active")) != "人气榜":
        raise DiscoveryError("来源榜单类型或结构发生变化")
    items = []
    for row in scope.all("div", cls="item"):
        info = row.first(cls="info") or _EMPTY
        link = (info.first(cls="title") or _EMPTY).first("a") or _EMPTY
        number = text_of(row.first(cls="sign"))
        if not number.isdecimal():
            raise DiscoveryError("来源榜单缺少名次")
        description = ""
        for line in info.all("p", cls="line"):
            if line.first(cls="logo_3"):
                description = line.text()
        items.append(_item("manben", text_of(link), link.attrs.get("href"), rank=int(number),
                           coverUrl=_cover("manben", image_of(row.first(cls="img"), _SITES["manben"][1])),
                           author=" / ".join(a.text() for a in info.all("a", cls="avatar")),
                           latestChapter=text_of(row.first("a", cls="tip")), description=description))
    return _unique(items, ranked=True)


def _manben_latest(source):
    root = _tree(source)
    scope = root.first(cls="updateList")
    if not scope:
        raise DiscoveryError("来源更新列表结构发生变化")
    scripts = " ".join(child for node in scope.all("script") for child in node.children if isinstance(child, str))
    # Verify the static binding we implement, without evaluating any JavaScript.
    for pattern in (r"var\s+mypage\s*=\s*2\s*;", r"mypage\+\+", r"['\"]pagerdata\.ashx\?d=", r"t:\s*8\s*,\s*pageindex:\s*mypage\s*,\s*sc:\s*1", r"type:\s*['\"]POST['\"]"):
        if not re.search(pattern, scripts):
            raise DiscoveryError("来源更新分页协议发生变化")
    items = []
    for row in scope.all("div", cls="item"):
        link = (row.first("p", cls="title") or _EMPTY).first("a") or _EMPTY
        items.append(_item("manben", text_of(link), link.attrs.get("href"),
                           coverUrl=_cover("manben", image_of(row.first(cls="book"), _SITES["manben"][1])),
                           latestChapter=text_of(row.first(cls="msg")), description=text_of(row.first("p", cls="tip"))))
    return _unique(items)


def _manben_batch(source):
    try:
        rows = json.loads(source)
    except (ValueError, TypeError) as error:
        raise DiscoveryError("来源更新分页未返回有效 JSON") from error
    if not isinstance(rows, list) or len(rows) > 100:
        raise DiscoveryError("来源更新分页格式发生变化")
    items = []
    for row in rows:
        if not isinstance(row, dict):
            raise DiscoveryError("来源更新分页条目格式发生变化")
        authors = row.get("Author")
        items.append(_item("manben", row.get("Title"), row.get("Url"),
                           coverUrl=_cover("manben", row.get("BigPic")),
                           author=" / ".join(_plain(a, 200) for a in authors if isinstance(a, str)) if isinstance(authors, list) else "",
                           latestChapter=_plain(row.get("LastPartShowName"), 200),
                           updatedAtText=_plain(row.get("LastPartTime"), 100),
                           description=_plain(row.get("Content")), status=known_status(_plain(row.get("Status"), 50))))
    return _unique(items) if items else []


def fetch(site_id, kind, period, page):
    site_id, kind, period, page = normalize_request({"siteId": site_id, "kind": kind, "period": period, "page": page})
    if site_id not in _SITES:
        adapter = next(module for module in _extensions() if any(item["siteId"] == site_id for item in module.sources()))
        result = adapter.fetch(site_id, kind, period, page)
        expected = {"siteId": site_id, "kind": kind, "period": period, "page": page}
        if not isinstance(result, dict) or any(result.get(key) != value for key, value in expected.items()):
            raise DiscoveryError("来源返回了其他列表")
        if not isinstance(result.get("items"), list) or type(result.get("hasMore")) is not bool:
            raise DiscoveryError("来源列表结构发生变化")
        return result
    deadline = time.monotonic() + _DEADLINE
    origin = _SITES[site_id][1]
    note = ""
    if site_id == "manhuagui":
        url = origin + (_PERIODS[period][1] if kind == "popular" else "/update/")
        content = _download(url, deadline=deadline)
        if kind == "popular":
            items, more, label = _gui_popular(content, period), False, _PERIODS[period][0]
        else:
            complete = _gui_latest(content)
            start = (page - 1) * _PAGE_SIZE
            items, more, label = complete[start:start + _PAGE_SIZE], len(complete) > start + _PAGE_SIZE, "最近7天更新"
            note = "来源提供完整近7天列表，云漫按原顺序每页显示24项。"
    elif kind == "popular":
        url = origin + "/mh-ranklist/"
        items, more, label = _manben_popular(_download(url, deadline=deadline)), False, "人气榜"
    else:
        url, label = origin + "/mh-updated/", "最近更新"
        items = (_manben_latest(_download(url, deadline=deadline)) if page == 1 else
                 _manben_batch(_download(_MANBEN_MORE, pageindex=page + 1, deadline=deadline)))
        # An actual adjacent batch confirms hasMore; an error is never "the end".
        following = _manben_batch(_download(_MANBEN_MORE, pageindex=page + 2, deadline=deadline)) if items else []
        if {row["detailUrl"] for row in items} & {row["detailUrl"] for row in following}:
            raise DiscoveryError("来源更新分页重复，请稍后重试")
        more = bool(following)
        if more and page == _MAX_PAGE:
            raise DiscoveryError("来源更新列表超过可浏览页数上限")
    result = {"siteId": site_id, "siteName": _SITES[site_id][0], "kind": kind, "period": period,
              "page": page, "items": items, "hasMore": more, "sourceUrl": url, "label": label,
              "fetchedAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")}
    if note:
        result["paginationNote"] = note
    return result
