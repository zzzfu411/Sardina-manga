"""Public Kuaikan rules recovered from the local Legado source packs.

The old mobile detail address now serves the desktop Nuxt page. Decode its
literal state, including the full directory, without executing website code.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlencode, urlparse
from urllib.request import Request

from .native_sources import UA, _urlopen
from .serialized_state import nuxt_state

SOURCES = {"kuaikan": ("快看漫画", "www.kuaikanmanhua.com")}
HOST_ALIASES = {"kuaikan": ("m.kuaikanmanhua.com",)}
SOURCE_NOTICES = {"kuaikan": "支持公开章节；部分章节需在快看源站解锁"}
IMAGE_DOMAINS = ("kkmh.com",)
IMAGE_REFERERS = {"kuaikan": "https://www.kuaikanmanhua.com/"}
ORIGIN = "https://www.kuaikanmanhua.com"


def _get(url):
    request = Request(url, headers={"User-Agent": UA, "Referer": ORIGIN + "/"})
    with _urlopen(request, 18) as response:
        body = response.read(4_000_001)
    if len(body) > 4_000_000:
        raise RuntimeError("快看返回的数据过大，请在源站查看")
    return body.decode("utf-8")


def _id(url, chapter=False):
    parsed = urlparse(url)
    if (parsed.scheme not in {"https", "http"} or parsed.hostname not in
            {"www.kuaikanmanhua.com", "m.kuaikanmanhua.com", "kuaikanmanhua.com"}
            or parsed.username or parsed.password or parsed.port not in (None, 80, 443)):
        raise ValueError("快看漫画地址无效")
    pattern = r"/(?:web/comic|mobile/comics)/(\d+)/?" if chapter else r"/(?:web/topic/(\d+)|mobile/(\d+)/list)/?"
    match = re.fullmatch(pattern, parsed.path)
    if not match:
        raise ValueError("快看漫画地址格式不匹配")
    return next(value for value in match.groups() if value)


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _data(page):
    try:
        state = nuxt_state(page)
    except (ValueError, IndexError) as exc:
        raise RuntimeError("快看页面解析失败：" + str(exc)) from exc
    blocks = state.get("data")
    if not isinstance(blocks, list) or not blocks or not isinstance(blocks[0], dict):
        raise RuntimeError("快看未返回漫画信息，请稍后重试")
    data = blocks[0]
    response = data.get("res", {})
    if response.get("code", 200) != 200:
        raise RuntimeError(_text(response.get("message")) or "快看漫画暂时不可用")
    return data


def search(site, keyword):
    payload = json.loads(_get("https://m.kuaikanmanhua.com/search/mini/topic/title_and_author?" +
                              urlencode({"page": 1, "size": 50, "q": keyword})))
    if payload.get("code") != 200:
        raise RuntimeError(_text(payload.get("message")) or "快看搜索失败")
    rows = payload.get("hits")
    if not isinstance(rows, list):
        raise RuntimeError("快看搜索数据格式已变化")
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        ident = str(row.get("topic_id") or row.get("id") or "")
        if not ident.isdigit() or ident in seen:
            continue
        seen.add(ident)
        title = row.get("title", "")
        if isinstance(title, dict):
            title = title.get("text", "")
        result.append({"title": _text(title), "url": f"{ORIGIN}/web/topic/{ident}/",
                       "cover": _text(row.get("vertical_image_url") or row.get("cover_image_url")),
                       "author": _text(row.get("author_name")), "latest": _text(row.get("latest_comic_title")),
                       "description": _text(row.get("description")), "status": ""})
    return result


def details(site, url):
    ident = _id(url)
    data = _data(_get(f"{ORIGIN}/web/topic/{ident}/"))
    info = data.get("topicInfo") or data.get("res", {}).get("data", {}).get("topic_info") or {}
    if str(info.get("id")) != ident:
        raise RuntimeError("快看未返回所选作品的信息")
    rows = data.get("comics", info.get("comics"))
    if not isinstance(rows, list):
        raise RuntimeError("快看章节目录格式已变化")
    chapters, seen = [], set()
    for row in rows:
        chapter_id = str(row.get("id", ""))
        if not chapter_id.isdigit() or chapter_id in seen:
            continue
        seen.add(chapter_id)
        chapter_url = f"{ORIGIN}/web/comic/{chapter_id}"
        locked = row.get("locked") is True or row.get("need_vip") is True
        title = _text(row.get("title")) or f"第{len(chapters) + 1}话"
        chapters.append({"id": chapter_url, "url": chapter_url, "name": title + ("（源站受限）" if locked else ""),
                         "order": len(chapters), "group": "", "locked": locked})
    expected = info.get("comics_count")
    if expected is not None and (type(expected) is not int or expected < 0 or expected != len(chapters)):
        raise RuntimeError("快看目录未完整返回，请稍后重试或在源站查看")
    result = {"title": _text(info.get("title")), "author": _text((info.get("user") or {}).get("nickname")),
              "description": _text(info.get("description")),
              "coverUrl": _text(info.get("vertical_image_url") or info.get("cover_image_url")),
              "status": _text(info.get("update_status")), "chapters": chapters, "sourceUrl": url,
              "catalogCompleteness": "complete" if expected is not None else "unknown"}
    if any(row["locked"] for row in chapters):
        result["unavailableReason"] = "部分章节受源站权限限制，标注“源站受限”的章节请前往快看查看；其他公开章节可直接阅读。"
    return result


def images(site, url):
    ident = _id(url, chapter=True)
    data = _data(_get(f"{ORIGIN}/web/comic/{ident}"))
    info = data.get("res", {}).get("data", {}).get("comic_info") or data.get("comicInfo") or {}
    if str(info.get("id")) != ident:
        raise RuntimeError("快看未返回所选章节")
    if info.get("locked") is True or info.get("need_vip") is True:
        raise RuntimeError("该章节受快看源站权限限制，请在源站解锁或切换其他漫画源")
    images = []
    for row in info.get("comic_images", []):
        # Preserve signed URLs exactly; rebuilding query parameters breaks them.
        url = (row.get("url1280") or row.get("url")) if isinstance(row, dict) else row
        if not isinstance(url, str):
            continue
        parsed = urlparse(url)
        if parsed.scheme in {"https", "http"} and (parsed.hostname or "").endswith(".kkmh.com") and url not in images:
            images.append(url)
    if not images:
        raise RuntimeError("快看未提供可公开阅读的章节图片，请在源站查看或换源")
    return images
