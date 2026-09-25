"""Sunday Webry's public website interface, identified in the supplied APK.

Search/directory/pageImages use the public GraphQL endpoint. Before requesting
any image metadata, check the normal viewer HTML's actual can_read/isPublic
flags and chapter identity. This adapter supplies anonymous free reading only;
it never signs in, purchases, rents, modifies permissions, or executes scripts.
The website directory is not a claim to include the separate app's catalogue.
"""
from __future__ import annotations

import json
import math
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from .html_metadata import parse_html
from .native_sources import UA, _ssl_context

SOURCES = {"sundaywebry": ("Sunday Webry（日文）", "www.sunday-webry.com")}
SOURCE_NOTICES = {"sundaywebry": "日文漫画；部分章节需在源站授权"}
IMAGE_DOMAINS = ("cdn-img.www.sunday-webry.com", "cdn-scissors.gigaviewer.com")
IMAGE_REFERERS = {"sundaywebry": "https://www.sunday-webry.com/"}
ORIGIN = "https://www.sunday-webry.com"
PAGE_SIZE = 100  # Verified on both episodes and pageImages, not a guessed cap.
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_DIRECTORY_PAGES = 100
MAX_SEARCH_PAGES = 10
MAX_IMAGE_PAGES = 30
REQUEST_BUDGET = 35
READING_RESTRICTION = "该章节当前未在 Sunday Webry 向匿名访客公开免费开放，请在源站查看或切换漫画源"

_SERIES_FIELDS = """databaseId title description thumbnailUri
  author { name } authors { name }
  firstEpisode { databaseId } latestEpisode { title }"""
_PAGE_INFO = "pageInfo { hasNextPage endCursor }"
SEARCH_QUERY = """query SundaySearch($q: String!, $after: String, $first: Int!) {
  search(keyword: $q, types: [SERIES], first: $first, after: $after) {
    edges { cursor node { ... on Series { """ + _SERIES_FIELDS + """ } } }
    """ + _PAGE_INFO + """
  }
}"""
DETAIL_QUERY = """query SundayDirectory($id: String!, $after: String, $first: Int!) {
  episode(databaseId: $id) { databaseId series { """ + _SERIES_FIELDS + """
    episodes(first: $first, after: $after) {
      edges { cursor node { databaseId title number publishedAt } }
      """ + _PAGE_INFO + """
    }
  } }
}"""
IMAGES_QUERY = """query SundayImages($id: String!, $after: String, $first: Int!) {
  episode(databaseId: $id) {
    databaseId series { databaseId }
    pageImages(first: $first, after: $after) {
      edges { cursor node { src width height } }
      """ + _PAGE_INFO + """
    }
  }
}"""


def _site(site):
    if site not in SOURCES:
        raise ValueError("Sunday Webry 来源无效")


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _id(value):
    if type(value) not in (str, int) or not re.fullmatch(r"[1-9][0-9]{0,19}", str(value)):
        raise ValueError("Sunday Webry 作品或章节编号无效")
    return str(value)


def _episode_url(ident):
    return ORIGIN + "/episode/" + _id(ident)


def _url_id(site, url):
    _site(site)
    if not isinstance(url, str) or len(url) > 2048 or any(ord(c) <= 32 for c in url):
        raise ValueError("Sunday Webry 地址无效")
    parsed = urlparse(url)
    if (parsed.scheme not in {"https", "http"} or parsed.hostname != "www.sunday-webry.com"
            or parsed.port not in (None, 443 if parsed.scheme == "https" else 80)
            or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.params):
        raise ValueError("Sunday Webry 地址不在已接入的官网范围")
    match = re.fullmatch(r"/episode/([1-9][0-9]{0,19})/?", parsed.path)
    if not match:
        raise ValueError("Sunday Webry 章节地址格式无效")
    return match[1]


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # No source request, its origin headers, or a POST body is forwarded to
        # a login page or a different host. Public canonical endpoints are fixed.
        raise RuntimeError("Sunday Webry 请求发生跳转，请在源站查看")


def _request(url, deadline, body=None, referer=None):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RuntimeError("Sunday Webry 请求超时，未取得完整数据，请稍后重试")
    headers = {"User-Agent": UA, "Referer": referer or ORIGIN + "/", "Origin": ORIGIN}
    if body is not None:
        headers.update({"Content-Type": "application/json", "Accept": "application/json"})
    request = Request(url, data=body, headers=headers)
    opener = build_opener(_NoRedirect(), HTTPSHandler(context=_ssl_context()))
    try:
        with opener.open(request, timeout=min(15, remaining)) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if response.geturl() != url:
                raise RuntimeError("Sunday Webry 未返回所请求的页面")
        if len(raw) > MAX_RESPONSE_BYTES:
            raise RuntimeError("Sunday Webry 返回的数据过大")
        return raw.decode("utf-8")
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise RuntimeError(READING_RESTRICTION) from None
        if exc.code == 429:
            raise RuntimeError("Sunday Webry 当前请求频率受限，请稍后重试") from None
        raise RuntimeError(f"Sunday Webry 接口暂不可用（HTTP {exc.code}）") from None
    except (URLError, TimeoutError, UnicodeError):
        raise RuntimeError("Sunday Webry 请求失败，请稍后重试") from None


def _json(text):
    try:
        return json.loads(text)
    except (ValueError, UnicodeError):
        raise RuntimeError("Sunday Webry 返回的数据格式已变化") from None


def _query(query, variables, deadline, referer=None):
    body = json.dumps({"query": query, "variables": variables}).encode()
    payload = _json(_request(ORIGIN + "/graphql", deadline, body, referer))
    if not isinstance(payload, dict) or payload.get("errors") or not isinstance(payload.get("data"), dict):
        # Partial GraphQL data is not a complete catalogue or image list.
        raise RuntimeError("Sunday Webry 未返回完整可用数据，请在源站查看或稍后重试")
    return payload["data"]


def _connection(value, previous):
    if not isinstance(value, dict) or not isinstance(value.get("edges"), list) or not isinstance(value.get("pageInfo"), dict):
        raise RuntimeError("Sunday Webry 分页数据格式已变化")
    edges, info = value["edges"], value["pageInfo"]
    if len(edges) > PAGE_SIZE or type(info.get("hasNextPage")) is not bool:
        raise RuntimeError("Sunday Webry 分页状态无效")
    nodes = []
    for edge in edges:
        if not isinstance(edge, dict) or not isinstance(edge.get("node"), dict):
            raise RuntimeError("Sunday Webry 目录条目不完整")
        nodes.append(edge["node"])
    after = info.get("endCursor") if info["hasNextPage"] else None
    if info["hasNextPage"] and (not nodes or not isinstance(after, str)
                               or not re.fullmatch(r"[A-Za-z0-9_+/=-]{1,512}", after) or after == previous):
        raise RuntimeError("Sunday Webry 分页没有前进，未取得完整目录")
    if previous is not None and not nodes:
        raise RuntimeError("Sunday Webry 后续分页为空，目录不完整")
    return nodes, after


def _image_url(value, original=False):
    if not isinstance(value, str) or len(value) > 8192 or any(ord(c) <= 32 for c in value):
        raise RuntimeError("Sunday Webry 图片地址无效")
    try:
        p = urlparse(value)
        if (p.scheme != "https" or p.hostname not in IMAGE_DOMAINS or p.port not in (None, 443)
                or p.username or p.password or p.fragment or p.params):
            raise ValueError
        if original and (p.hostname != IMAGE_DOMAINS[0] or not p.path.startswith("/public/original/")):
            raise ValueError
    except ValueError:
        raise RuntimeError("Sunday Webry 返回了未接入的图片地址") from None
    return value  # Preserve the cover timestamp and any signed query exactly.


def _authors(series):
    values = series.get("authors")
    if not isinstance(values, list) or not values:
        values = [series.get("author")]
    return " / ".join(dict.fromkeys(_text(row.get("name")) for row in values
                                    if isinstance(row, dict) and _text(row.get("name"))))


def search(site, keyword):
    _site(site)
    if not isinstance(keyword, str) or len(keyword) > 200:
        raise ValueError("Sunday Webry 搜索词无效")
    if not keyword.strip():
        return []
    deadline, after, seen, cursors, result = time.monotonic() + REQUEST_BUDGET, None, set(), set(), []
    for _ in range(MAX_SEARCH_PAGES):
        data = _query(SEARCH_QUERY, {"q": keyword.strip(), "after": after, "first": PAGE_SIZE}, deadline)
        nodes, next_cursor = _connection(data.get("search"), after)
        fresh = 0
        for node in nodes:
            ident = _id(node.get("databaseId"))
            if ident in seen:
                continue
            first = node.get("firstEpisode")
            if not isinstance(first, dict) or not _text(node.get("title")):
                raise RuntimeError("Sunday Webry 搜索作品缺少有效阅读入口")
            url = _episode_url(first.get("databaseId"))
            seen.add(ident)
            fresh += 1
            cover = _text(node.get("thumbnailUri"))
            latest = node.get("latestEpisode")
            result.append({"title": _text(node.get("title")), "url": url,
                           "cover": _image_url(cover) if cover else "", "author": _authors(node),
                           "latest": _text(latest.get("title")) if isinstance(latest, dict) else "",
                           "description": _text(node.get("description")), "status": ""})
        if nodes and not fresh:
            raise RuntimeError("Sunday Webry 搜索分页重复，未取得完整结果")
        if next_cursor is None:
            return result
        if next_cursor in cursors:
            raise RuntimeError("Sunday Webry 搜索分页循环")
        cursors.add(next_cursor)
        after = next_cursor
    raise RuntimeError("Sunday Webry 搜索结果超过分页上限，未返回部分结果")


def _episode(data, ident):
    episode = data.get("episode")
    if not isinstance(episode, dict) or str(episode.get("databaseId")) != ident:
        raise RuntimeError("Sunday Webry 未返回所选章节")
    series = episode.get("series")
    if not isinstance(series, dict):
        raise RuntimeError("Sunday Webry 未返回所选作品")
    _id(series.get("databaseId"))
    return episode, series


def details(site, url):
    ident = _url_id(site, url)
    deadline, after, seen, cursors, chapters = time.monotonic() + REQUEST_BUDGET, None, set(), set(), []
    series_id = None
    metadata = None
    for _ in range(MAX_DIRECTORY_PAGES):
        data = _query(DETAIL_QUERY, {"id": ident, "after": after, "first": PAGE_SIZE}, deadline, _episode_url(ident))
        _, series = _episode(data, ident)
        current_id = _id(series.get("databaseId"))
        if series_id is not None and series_id != current_id:
            raise RuntimeError("Sunday Webry 后续分页返回了其他作品")
        series_id, metadata = current_id, metadata or series
        nodes, next_cursor = _connection(series.get("episodes"), after)
        fresh = 0
        for node in nodes:
            chapter_id = _id(node.get("databaseId"))
            if chapter_id in seen:
                continue
            name = _text(node.get("title"))
            if not name:
                raise RuntimeError("Sunday Webry 章节缺少标题")
            seen.add(chapter_id)
            fresh += 1
            chapter_url = _episode_url(chapter_id)
            number = node.get("number")
            number = number if type(number) in (int, float) and math.isfinite(number) else None
            chapters.append({"id": chapter_url, "url": chapter_url, "name": name, "group": "", "_number": number})
        if nodes and not fresh:
            raise RuntimeError("Sunday Webry 目录分页重复，未取得完整目录")
        if next_cursor is None:
            break
        if next_cursor in cursors:
            raise RuntimeError("Sunday Webry 目录分页循环")
        cursors.add(next_cursor)
        after = next_cursor
    else:
        raise RuntimeError("Sunday Webry 目录超过分页上限，未返回部分目录")
    # GraphQL's source order is descending and can mix historical free windows.
    # Use its real episode numbers; leave unnumbered entries in their slots.
    numeric_slots = [i for i, row in enumerate(chapters) if row["_number"] is not None]
    sorted_rows = sorted((chapters[i] for i in numeric_slots), key=lambda row: row["_number"])
    for index, row in zip(numeric_slots, sorted_rows):
        chapters[index] = row
    for index, chapter in enumerate(chapters):
        chapter.pop("_number")
        chapter["order"] = index
    title = _text(metadata.get("title"))
    if not title:
        raise RuntimeError("Sunday Webry 未返回作品标题")
    cover = _text(metadata.get("thumbnailUri"))
    result = {"title": title, "author": _authors(metadata), "description": _text(metadata.get("description")),
              "coverUrl": _image_url(cover) if cover else "", "status": "", "chapters": chapters,
              "sourceUrl": _episode_url(ident), "sourceNotice": SOURCE_NOTICES[site],
              "catalogCompleteness": "complete"}
    if not chapters:
        result["unavailableReason"] = "Sunday Webry 官网当前没有返回该作品的章节目录"
    return result


def _permission(page, ident):
    root = parse_html(page)
    document = root.first("html")
    script = root.first("script", ident="episode-json")
    if not document or document.attrs.get("data-route") != "core:viewer" or not script:
        raise RuntimeError("Sunday Webry 未提供公开阅读页面，请在源站查看")
    layer = _json(document.attrs.get("data-gtm-data-layer", ""))
    value = _json(script.attrs.get("data-value", ""))
    episode = layer.get("episode") if isinstance(layer, dict) else None
    product = value.get("readableProduct") if isinstance(value, dict) else None
    if (not isinstance(episode, dict) or not isinstance(product, dict)
            or str(episode.get("episode_id")) != ident or str(product.get("id")) != ident
            or product.get("typeName") != "episode"):
        raise RuntimeError("Sunday Webry 阅读权限与所选章节不匹配")
    if episode.get("can_read") is not True or product.get("isPublic") is not True:
        raise RuntimeError(READING_RESTRICTION)
    if _url_id("sundaywebry", product.get("permalink")) != ident:
        raise RuntimeError("Sunday Webry 阅读页面与所选章节不匹配")
    series = product.get("series")
    if not isinstance(series, dict) or str(series.get("id")) != str(episode.get("series_id")):
        raise RuntimeError("Sunday Webry 阅读页面作品信息不匹配")
    series_id = _id(series.get("id"))
    structure = product.get("pageStructure")
    if not isinstance(structure, dict) or not isinstance(structure.get("pages"), list):
        raise RuntimeError("Sunday Webry 未返回当前公开章节的页面结构")
    main_pages = [row for row in structure["pages"] if isinstance(row, dict) and row.get("type") == "main"]
    if not main_pages or len(main_pages) > MAX_IMAGE_PAGES * PAGE_SIZE:
        raise RuntimeError("Sunday Webry 公开章节页数无效")
    return series_id, len(main_pages)


def images(site, url):
    ident = _url_id(site, url)
    canonical = _episode_url(ident)
    deadline = time.monotonic() + REQUEST_BUDGET
    # The denial branch must return before asking GraphQL for pageImages.
    series_id, expected_count = _permission(_request(canonical, deadline), ident)
    after, cursors, result = None, set(), []
    for _ in range(MAX_IMAGE_PAGES):
        data = _query(IMAGES_QUERY, {"id": ident, "after": after, "first": PAGE_SIZE}, deadline, canonical)
        episode, series = _episode(data, ident)
        if _id(series.get("databaseId")) != series_id:
            raise RuntimeError("Sunday Webry 图片与获准阅读的作品不匹配")
        nodes, next_cursor = _connection(episode.get("pageImages"), after)
        for node in nodes:
            if (type(node.get("width")) is not int or type(node.get("height")) is not int
                    or not 0 < node["width"] * node["height"] <= 32_000_000
                    or node["width"] <= 0 or node["height"] <= 0):
                raise RuntimeError("Sunday Webry 返回了无效的图片尺寸")
            result.append(_image_url(node.get("src"), original=True))
        if len(result) > expected_count:
            raise RuntimeError("Sunday Webry 图片数量超出本章公开页面范围")
        if next_cursor is None:
            if len(result) != expected_count:
                raise RuntimeError("Sunday Webry 未返回完整的公开章节图片")
            return result
        if next_cursor in cursors:
            raise RuntimeError("Sunday Webry 图片分页循环")
        cursors.add(next_cursor)
        after = next_cursor
    raise RuntimeError("Sunday Webry 图片超过分页上限，未返回部分图片")
