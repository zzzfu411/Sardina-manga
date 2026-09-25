"""Public WAP adapters transcribed from the user's extracted reading rules.

No imported rule JavaScript, application secrets, cookies, or login tokens are
executed or required. The yyhao brands share a WAP gateway: its valid HTTPS host
accepts explicit kmh/mht/smh product parameters and returns brand-specific data.
"""
from __future__ import annotations

import html
import json
import re
import urllib.request
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

from .html_metadata import image_of, known_status, parse_html, text_of

SOURCES = {
    "kanman": ("看漫画", "m.kanman.com"),
    "manhuatai": ("漫画台", "m.manhuatai.com"),
    "shenmanhua": ("神漫画", "m.taomanhua.com"),
    "mkzhan": ("漫客栈", "www.mkzhan.com"),
}
HOST_ALIASES = {
    "kanman": ("www.kanman.com",),
    "manhuatai": ("www.manhuatai.com",),
    "shenmanhua": ("www.taomanhua.com",),
    "mkzhan": ("mkzhan.com", "comic.mkzhan.com", "comic.mkzcdn.com"),
}
IMAGE_DOMAINS = ("kaimanhua.com", "image.yqmh.com", "image.mhxk.com", "oss.mkzcdn.com", "content.mkzcdn.com")
IMAGE_REFERERS = {
    "kanman": "https://m.kanman.com/",
    "manhuatai": "https://m.manhuatai.com/",
    "shenmanhua": "https://m.taomanhua.com/",
    "mkzhan": "https://www.mkzhan.com/",
}
_PRODUCTS = {"kanman": (1, "kmh"), "manhuatai": (2, "mht"), "shenmanhua": (None, "smh")}
_GATEWAY = "https://m.kanman.com/api/"
_FETCH_HOSTS = {"m.kanman.com", "www.mkzhan.com", "mkzhan.com", "comic.mkzcdn.com"}
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36"
_MAX_BODY = 5 * 1024 * 1024
_SEARCH_PAGES = 2


def _safe_fetch_url(url):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in _FETCH_HOSTS or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("漫画源返回了不支持的跳转地址")
    return url


class _Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, _safe_fetch_url(newurl))


def _get(url, referer=None):
    """TLS validation remains enabled, including for redirects."""
    request = urllib.request.Request(_safe_fetch_url(url), headers={"User-Agent": _UA, "Referer": referer or url, "Accept": "application/json,text/html;q=0.9,*/*;q=0.8"})
    with urllib.request.build_opener(_Redirect()).open(request, timeout=20) as response:
        body = response.read(_MAX_BODY + 1)
        if len(body) > _MAX_BODY:
            raise RuntimeError("漫画源返回内容过大")
        return body.decode("utf-8", errors="replace")


def _json(url, referer=None):
    try:
        payload = json.loads(_get(url, referer))
    except json.JSONDecodeError as error:
        raise RuntimeError("漫画源没有返回有效数据，请稍后重试") from error
    if not isinstance(payload, dict):
        raise RuntimeError("漫画源返回数据格式发生变化")
    code = payload.get("status", payload.get("code"))
    if code is not None and str(code) not in {"0", "200"}:
        message = _text(payload.get("message") or payload.get("msg")) or "漫画源拒绝了请求"
        raise RuntimeError(message)
    return payload.get("data")


def _text(value):
    if value is None:
        return ""
    if isinstance(value, list):
        return "、".join(filter(None, (_text(item) for item in value)))
    if isinstance(value, dict):
        return _text(value.get("name") or value.get("title"))
    value = str(value).replace("\\n", " ")
    if re.search(r"</?[a-zA-Z][^>]*>", value):
        value = parse_html(value).text()
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"[\ue000-\uf8ff]", "", value))).strip()


def _image_url(value):
    if not isinstance(value, str):
        return ""
    value = html.unescape(value.strip())
    if value.startswith("//"):
        value = "https:" + value
    parsed = urlparse(value)
    host = parsed.hostname or ""
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password or parsed.port not in {None, 80, 443}:
        return ""
    if not any(host == domain or host.endswith("." + domain) for domain in IMAGE_DOMAINS):
        return ""
    return value


def _source_url(site, url):
    if site not in SOURCES:
        raise ValueError("未接入的 WAP 漫画源")
    parsed = urlparse(url)
    hosts = (SOURCES[site][1], *HOST_ALIASES.get(site, ()))
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in hosts or parsed.username or parsed.password or parsed.port not in {None, 80, 443}:
        raise ValueError("漫画地址不属于当前源")
    return parsed


def _comic_id(site, url):
    parsed = _source_url(site, url)
    candidate = parse_qs(parsed.query).get("comic_id", [""])[0]
    if not candidate:
        match = re.match(r"^/(\d+)(?:/|$)", parsed.path)
        candidate = match[1] if match else ""
    if not re.fullmatch(r"\d{1,12}", candidate):
        raise ValueError("漫画地址缺少有效编号，请重新搜索这本漫画")
    return candidate


def _wap_url(site, endpoint, **extra):
    product_id, product = _PRODUCTS[site]
    params = {"productname": product, "platformname": "wap"}
    if product_id is not None:
        params["product_id"] = product_id
    params.update(extra)
    return _GATEWAY + endpoint + "/?" + urlencode(params)


def _book_url(site, comic_id, slug=""):
    if site == "kanman":
        return f"https://{SOURCES[site][1]}/{comic_id}/"
    slug = slug if re.fullmatch(r"[a-zA-Z0-9_-]+", str(slug)) else comic_id
    return f"https://{SOURCES[site][1]}/{slug}/?" + urlencode({"comic_id": comic_id})


def _chapter_url(site, comic_id, chapter_id):
    return f"https://{SOURCES[site][1]}/api/getchapterinfov2/?" + urlencode({"comic_id": comic_id, "chapter_newid": chapter_id})


def _wap_rows(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        return data["data"]
    return []


def _search_wap(site, keyword):
    rows, seen = [], set()
    for page in range(1, _SEARCH_PAGES + 1):
        data = _json(_wap_url(site, "getsortlist", search_key=keyword, search_type="", page=page, size=48))
        if not isinstance(data, list) and not (isinstance(data, dict) and isinstance(data.get("data"), list)):
            raise RuntimeError("漫画源搜索数据格式发生变化")
        for item in _wap_rows(data):
            if not isinstance(item, dict):
                continue
            comic_id = str(item.get("comic_id", ""))
            title = _text(item.get("comic_name"))
            if not re.fullmatch(r"\d{1,12}", comic_id) or not title or comic_id in seen:
                continue
            seen.add(comic_id)
            cover = item.get("cover_img") or item.get("cover") or f"https://image.yqmh.com/mh/{comic_id}.jpg-300x400.webp"
            rows.append({"title": title, "url": _book_url(site, comic_id, item.get("comic_newid", "")), "cover": _image_url(cover),
                         "author": _text(item.get("comic_author") or item.get("cartoon_author_list_name")),
                         "latest": _text(item.get("last_chapter_name") or item.get("latest_cartoon_topic_name") or item.get("last_qmmh_chapter_name")),
                         "description": _text(item.get("cartoon_desc") or item.get("comic_desc")),
                         "status": known_status(_text(item.get("update_status_str")))})
        total = data.get("page", {}).get("total_page", 1) if isinstance(data, dict) else 1
        try:
            if page >= int(total):
                break
        except (TypeError, ValueError):
            break
    return rows


def _chapters_wap(site, comic_id, rows):
    valid = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    if valid and all(isinstance(row.get("order_num"), (int, float)) for row in valid):
        valid.sort(key=lambda row: row["order_num"])
    else:
        # Both extracted WAP rules and the live response list newest first.
        valid.reverse()
    chapters, seen = [], set()
    for row in valid:
        chapter_id = str(row.get("chapter_newid") or row.get("chapter_id") or "")
        if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,200}", chapter_id) or chapter_id in seen:
            continue
        seen.add(chapter_id)
        paid = str(row.get("price", "0")) not in {"", "0", "0.0", "None"}
        chapters.append({"id": chapter_id, "name": _text(row.get("chapter_name")) or f"第 {len(chapters) + 1} 章",
                         "url": _chapter_url(site, comic_id, chapter_id), "order": len(chapters), "group": "源站收费章节" if paid else "章节"})
    return chapters


def _details_wap(site, url):
    comic_id = _comic_id(site, url)
    data = _json(_wap_url(site, "getcomicinfo_body", comic_id=comic_id))
    if not isinstance(data, dict) or not data.get("comic_name"):
        raise RuntimeError("这本漫画已下架或源站没有返回详情")
    if str(data.get("comic_id", comic_id)) != comic_id:
        raise RuntimeError("源站返回了另一部漫画，请重新搜索")
    raw_chapters = data.get("comic_chapter")
    if not isinstance(raw_chapters, list):
        raw_chapters = _json(_wap_url(site, "getchapterlist", comic_id=comic_id))
    chapters = _chapters_wap(site, comic_id, raw_chapters)
    covers = data.get("cover_list") or []
    cover = next((_image_url(value) for value in covers if _image_url(value)), "") if isinstance(covers, list) else ""
    result = {"title": _text(data.get("comic_name")), "author": _text(data.get("comic_author")),
              "description": _text(data.get("comic_desc")), "coverUrl": cover,
              "status": known_status(_text(data.get("update_status_str"))), "chapters": chapters,
              "sourceUrl": _book_url(site, comic_id, data.get("comic_newid", ""))}
    if not chapters:
        result["unavailableReason"] = "源站未公开可阅读目录，可能需要在源站登录或已停止提供。"
    return result


def _images_wap(site, url):
    parsed = _source_url(site, url)
    comic_id = _comic_id(site, url)
    params = parse_qs(parsed.query)
    chapter_id = params.get("chapter_newid", [""])[0]
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,200}", chapter_id):
        raise ValueError("章节地址缺少有效编号，请重新打开目录")
    data = _json(_wap_url(site, "getchapterinfov2", comic_id=comic_id, chapter_newid=chapter_id, isWebp=1, quality="low"))
    if isinstance(data, dict) and data.get("comic_id") and str(data["comic_id"]) != comic_id:
        raise RuntimeError("漫画源返回了另一部漫画，请重新打开目录")
    chapter = data.get("current_chapter", {}) if isinstance(data, dict) else {}
    if not isinstance(chapter, dict):
        raise RuntimeError("漫画源返回章节格式发生变化")
    if chapter.get("chapter_newid") and str(chapter["chapter_newid"]) != chapter_id:
        raise RuntimeError("漫画源返回了另一章节，请重新打开目录")
    urls = chapter.get("chapter_img_list")
    images = list(dict.fromkeys(filter(None, (_image_url(value) for value in urls)))) if isinstance(urls, list) else []
    if not images:
        raise RuntimeError("本章未公开阅读图片，可能需要源站登录、购买或等待开放；可尝试其他章节或漫画源。")
    return images


def _search_mkzhan(keyword):
    url = "https://www.mkzhan.com/search/?" + urlencode({"keyword": keyword})
    root = parse_html(_get(url))
    scope = root.first(cls="search-comic-list")
    if scope is None:
        raise RuntimeError("漫客栈搜索页面结构发生变化")
    heading = text_of(scope.first(cls="search_head"))
    if "没有找到" in heading or "为您推荐" in heading or re.search(r"搜索结果\s*[（(]\s*0\s*[）)]", heading):
        return []
    result, seen = [], set()
    for item in scope.all(cls="common-comic-item"):
        title_node = item.first(cls="comic__title")
        title = _text(text_of(title_node))
        link = item.first("a", cls="cover") or (title_node.first("a") if title_node else None)
        raw = link.attrs.get("href", "") if link else ""
        match = re.fullmatch(r"/(\d{1,12})/?", urlparse(urljoin(url, raw)).path)
        if not title or not match or match[1] in seen:
            continue
        seen.add(match[1])
        author_scope = item.first(cls="comic-author")
        result.append({"title": title, "url": f"https://www.mkzhan.com/{match[1]}/", "cover": _image_url(image_of(item, url)),
                       "author": _text(text_of(author_scope)), "latest": re.sub(r"^更至\s*[：:]?\s*", "", _text(text_of(item.first(cls="comic-update")))),
                       "description": _text(text_of(item.first(cls="comic-feature"))), "status": ""})
    return result


def _details_mkzhan(url):
    comic_id = _comic_id("mkzhan", url)
    canonical = f"https://www.mkzhan.com/{comic_id}/"
    root = parse_html(_get(canonical))
    scope = root.first(cls="de-info__box")
    title = _text(text_of(scope.first(cls="comic-title"))) if scope else ""
    if not title:
        raise RuntimeError("这本漫画已下架或漫客栈详情结构发生变化")
    chapters, seen = [], set()
    # The site can repeat its visible/latest chapters; only the full directory is parsed.
    containers = list(root.all(cls="chapter__list-box"))
    raw_chapters = [node for container in containers for node in container.all(cls="j-chapter-link")]
    for node in reversed(raw_chapters):
        chapter_id = node.attrs.get("data-chapterid", "")
        if not re.fullmatch(r"\d{1,12}", chapter_id) or chapter_id in seen:
            continue
        seen.add(chapter_id)
        chapters.append({"id": chapter_id, "name": _text(node.text()) or f"第 {len(chapters) + 1} 章",
                         "url": f"https://www.mkzhan.com/{comic_id}/{chapter_id}.html", "order": len(chapters),
                         "group": "源站收费章节" if node.first(cls="lock") else "章节"})
    result = {"title": title, "author": _text(text_of(scope.first(cls="name"))),
              "description": _text(text_of(scope.first(cls="intro-total") or scope.first(cls="comic-intro"))),
              "coverUrl": _image_url(image_of(root.first(cls="de-info__cover"), canonical)),
              "status": known_status(text_of(root.first(cls="de-chapter__title"))), "chapters": chapters, "sourceUrl": canonical}
    if not chapters:
        result["unavailableReason"] = "漫客栈未公开可阅读目录，可能需要在源站登录或已停止提供。"
    return result


def _images_mkzhan(url):
    parsed = _source_url("mkzhan", url)
    comic_id = _comic_id("mkzhan", url)
    chapter_id = parse_qs(parsed.query).get("chapter_id", [""])[0]
    if not chapter_id:
        match = re.fullmatch(r"/\d+/(\d+)(?:\.html|/)?", parsed.path)
        chapter_id = match[1] if match else ""
    if not re.fullmatch(r"\d{1,12}", chapter_id):
        raise ValueError("章节地址缺少有效编号，请重新打开目录")
    endpoint = "https://comic.mkzcdn.com/chapter/content/v1/?" + urlencode({"chapter_id": chapter_id, "comic_id": comic_id, "format": 1, "quality": 1, "sign": 0, "type": 1, "uid": 0})
    data = _json(endpoint, "https://www.mkzhan.com/")
    pages = data.get("page") if isinstance(data, dict) else None
    images = list(dict.fromkeys(filter(None, (_image_url(page.get("image")) for page in pages if isinstance(page, dict))))) if isinstance(pages, list) else []
    if not images:
        raise RuntimeError("漫客栈未公开本章图片，可能需要源站登录、购买或等待开放；可尝试其他章节或漫画源。")
    return images


def search(site, keyword):
    if site not in SOURCES:
        raise ValueError("未接入的 WAP 漫画源")
    keyword = str(keyword).strip()
    if not keyword:
        return []
    return _search_mkzhan(keyword) if site == "mkzhan" else _search_wap(site, keyword)


def details(site, url):
    return _details_mkzhan(url) if site == "mkzhan" else _details_wap(site, url)


def images(site, url):
    return _images_mkzhan(url) if site == "mkzhan" else _images_wap(site, url)
