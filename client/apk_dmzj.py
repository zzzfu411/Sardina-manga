"""Public Zaimanhua APIs identified in the supplied Tachiyomi APK.

The legacy DMZJ endpoints did not pass transport/search validation and are not
registered. Zaimanhua search, directories and permitted chapters work without
an account; some works deny anonymous reading. Never work around ``canRead`` or
silently turn permission/network failures into an empty search result.
"""
from __future__ import annotations

import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from .native_sources import UA, _ssl_context

SOURCES = {"zaimanhua": ("再漫画", "m.zaimanhua.com")}
SOURCE_NOTICES = {"zaimanhua": "部分作品受源站权限限制"}
IMAGE_DOMAINS = ("images.zaimanhua.com",)
IMAGE_REFERERS = {"zaimanhua": "https://manhua.zaimanhua.com/"}

API_BASE = "https://v4api.zaimanhua.com/app/v1"
WEB_BASE = "https://m.zaimanhua.com"
API_VERSION = "2.2.5"
PAGE_SIZE = 20
MAX_SEARCH_PAGES = 3
READING_RESTRICTION = "源站未授予当前访问阅读权限；搜索和目录可用，请在源站完成所需授权或选择其他漫画源"


def _site(site):
    if site not in SOURCES:
        raise ValueError("未通过可用性验证的动漫之家来源")


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _id(value):
    value = str(value)
    if not re.fullmatch(r"[1-9]\d{0,11}", value):
        raise ValueError("漫画或章节编号无效")
    return value


def _book_url(mid):
    return WEB_BASE + "/pages/comic/detail?" + urlencode({"id": _id(mid)})


def _chapter_url(mid, cid):
    return WEB_BASE + "/pages/comic/page?" + urlencode({"comic_id": _id(mid), "chapter_id": _id(cid)})


def _url_ids(site, url, chapter=False):
    _site(site)
    p = urlparse(url)
    if (p.scheme not in {"https", "http"} or p.hostname != SOURCES[site][1]
            or p.username or p.password or p.port not in {None, 80, 443}
            or p.path != ("/pages/comic/page" if chapter else "/pages/comic/detail")):
        raise ValueError("再漫画地址与所选作品类型不匹配")
    query = parse_qs(p.query)
    fields = ("comic_id", "chapter_id") if chapter else ("id",)
    if any(len(query.get(field, [])) != 1 for field in fields):
        raise ValueError("漫画或章节地址缺少唯一编号")
    return tuple(_id(query[field][0]) for field in fields)


def _get_json(path, params=None, platform=None):
    url = API_BASE + path + ("?" + urlencode(params) if params else "")
    headers = {"User-Agent": UA, "Referer": IMAGE_REFERERS["zaimanhua"], "Accept": "application/json"}
    if platform:
        headers["Platform"] = platform
    try:
        # Use normal certificate verification; no legacy-host TLS bypass.
        with urlopen(Request(url, headers=headers), timeout=15, context=_ssl_context()) as response:
            body = response.read(4 * 1024 * 1024 + 1)
    except HTTPError as exc:
        raise RuntimeError(f"再漫画接口返回 HTTP {exc.code}") from None
    except (URLError, TimeoutError):
        raise RuntimeError("再漫画接口暂时无法连接，请稍后重试") from None
    if len(body) > 4 * 1024 * 1024:
        raise RuntimeError("再漫画接口响应过大")
    try:
        return json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise RuntimeError("再漫画未返回有效 JSON 数据") from None


def _data(response):
    if not isinstance(response, dict):
        raise RuntimeError("再漫画返回了未知的数据格式")
    if response.get("errno", 0) not in (None, 0, "0"):
        message = _text(response.get("errmsg"))[:180] or "请求失败"
        raise RuntimeError("再漫画：" + message)
    data = response.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("再漫画响应缺少有效数据")
    return data


def _book_data(response):
    data = _data(response).get("data")
    if not isinstance(data, dict):
        raise RuntimeError("再漫画未返回作品或章节信息")
    return data


def _tags(value):
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, list):
        return ""
    return " / ".join(_text(item.get("tag_name")) for item in value
                      if isinstance(item, dict) and _text(item.get("tag_name")))


def _image_url(value):
    value = _text(value)
    if not value:
        return ""
    p = urlparse(value)
    if (p.scheme not in {"https", "http"} or p.hostname not in IMAGE_DOMAINS
            or p.username or p.password or p.port not in {None, 80, 443}):
        raise RuntimeError("再漫画返回了未接入的图片地址")
    return value


def search(site, keyword):
    _site(site)
    keyword = _text(keyword)
    if not 1 <= len(keyword) <= 100:
        raise ValueError("请输入 1–100 字的漫画名")
    rows, seen = [], set()
    for page in range(1, MAX_SEARCH_PAGES + 1):
        data = _data(_get_json("/search/index", {"source": 0, "size": PAGE_SIZE, "keyword": keyword, "page": page}))
        batch = data.get("list")
        if not isinstance(batch, list):
            raise RuntimeError("再漫画搜索结果格式异常")
        try:
            total = max(0, int(data.get("total", len(batch))))
        except (TypeError, ValueError):
            raise RuntimeError("再漫画搜索分页信息异常") from None
        before = len(rows)
        for item in batch:
            if not isinstance(item, dict):
                continue
            try:
                mid = _id(item.get("comic_id") or item.get("id"))
            except ValueError:
                continue
            title = _text(item.get("title"))
            if mid in seen or not title:
                continue
            seen.add(mid)
            rows.append({"title": title, "url": _book_url(mid),
                         "cover": _image_url(item.get("cover")), "author": _tags(item.get("authors")),
                         "latest": _text(item.get("last_update_chapter_name")) or _text(item.get("last_name")),
                         "description": _text(item.get("description")), "status": _text(item.get("status"))})
        if len(rows) == before and total > before:
            raise RuntimeError("再漫画搜索分页重复或缺失，请稍后重试")
        if page * PAGE_SIZE >= total or not batch:
            break
    return rows


def metadata(site, url):
    mid, = _url_ids(site, url)
    data = _book_data(_get_json("/comic/detail/" + mid, {"_v": API_VERSION}, platform="pc"))
    if _id(data.get("id")) != mid:
        raise RuntimeError("再漫画返回的作品编号不匹配")
    return _metadata(data)


def _metadata(data):
    return {"title": _text(data.get("title")), "author": _tags(data.get("authors")),
            "description": _text(data.get("description")), "coverUrl": _image_url(data.get("cover")),
            "status": _tags(data.get("status")),
            "tags": [value.strip() for value in _tags(data.get("types")).split('/') if value.strip()][:20]}


def details(site, url):
    mid, = _url_ids(site, url)
    data = _book_data(_get_json("/comic/detail/" + mid, {"_v": API_VERSION}, platform="pc"))
    if _id(data.get("id")) != mid:
        raise RuntimeError("再漫画返回的作品编号不匹配")
    groups = data.get("chapters")
    if groups is None:
        groups = []
    if not isinstance(groups, list):
        raise RuntimeError("再漫画目录格式异常")
    chapters, seen = [], set()
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("data"), list):
            raise RuntimeError("再漫画章节分组格式异常")
        batch = group["data"]
        # The observed API gives chapter_order within each group; otherwise
        # its native newest-first sequence is reversed, without guessing IDs.
        if all(isinstance(row, dict) and isinstance(row.get("chapter_order"), (int, float)) for row in batch):
            batch = sorted(batch, key=lambda row: row["chapter_order"])
        else:
            batch = list(reversed(batch))
        for row in batch:
            if not isinstance(row, dict):
                raise RuntimeError("再漫画章节数据格式异常")
            cid = _id(row.get("chapter_id"))
            name = _text(row.get("chapter_name")) or _text(row.get("chapter_title"))
            if cid in seen or not name:
                continue
            seen.add(cid)
            chapter_url = _chapter_url(mid, cid)
            chapters.append({"id": cid, "name": name, "url": chapter_url, "order": len(chapters), "group": _text(group.get("title"))})
    result = {**_metadata(data), "chapters": chapters, "sourceUrl": _book_url(mid)}
    if data.get("canRead") is False:
        result["unavailableReason"] = READING_RESTRICTION
    elif data.get("isHideChapter") == 1 or not chapters:
        result["unavailableReason"] = "源站未向当前访问提供可用目录，请在源站查看或选择其他漫画源"
    return result


def images(site, url):
    mid, cid = _url_ids(site, url, chapter=True)
    data = _book_data(_get_json("/comic/chapter/" + mid + "/" + cid, {"_v": API_VERSION}, platform="h5"))
    # Do not trust returned image URLs when the source denies access.
    if data.get("canRead") is not True:
        raise RuntimeError(READING_RESTRICTION)
    if _id(data.get("comic_id")) != mid or _id(data.get("chapter_id")) != cid:
        raise RuntimeError("再漫画返回的章节编号不匹配")
    raw = data.get("page_url_hd")
    if not isinstance(raw, list) or not raw:
        raise RuntimeError("再漫画未返回有效章节图片，请稍后重试或切换漫画源")
    urls = [_image_url(item) for item in raw]
    if not all(urls):
        raise RuntimeError("再漫画章节包含无效图片地址")
    return list(dict.fromkeys(urls))
