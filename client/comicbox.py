"""Comicbox HTML metadata and logical image URLs; image recovery lives elsewhere.

Only the actual search grid, book hero/directory, and comic page containers are
parsed. Imported page JavaScript is never executed. BMI versions and optional
per-image transport metadata are carried from the current HTML, never guessed.
"""
from __future__ import annotations

import html
import re
import urllib.request
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from .html_metadata import known_status, parse_html, text_of

ORIGIN = "https://www.comicbox.xyz"
PAGE_HOSTS = ("www.comicbox.xyz", "comicbox.xyz")
IMAGE_HOSTS = ("bmigmi-global-wuwu.ccavbox.com",)
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"
_MAX_HTML = 5 * 1024 * 1024
_KEY = re.compile(r"(?<![\w$])(?:window\s*\.\s*)?BMI_CACHE_KEY\s*=\s*(['\"])([^'\"\r\n]+)\1")


def _page_url(url, kind=None):
    parsed = urlparse(urljoin(ORIGIN + "/", url))
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in PAGE_HOSTS or parsed.username or parsed.password or parsed.port not in {None, 80, 443}:
        raise ValueError("漫画地址不属于歪歪源")
    if kind == "book" and not re.fullmatch(r"/book/\d+/?", parsed.path):
        raise ValueError("歪歪漫画详情地址无效")
    if kind == "chapter" and not re.fullmatch(r"/(?:free-chapter|chapter)/\d+/?", parsed.path):
        raise ValueError("歪歪章节地址无效")
    return urlunparse(("https", "www.comicbox.xyz", parsed.path, "", parsed.query, ""))


class _Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, _page_url(newurl))


def _page(url):
    request = urllib.request.Request(_page_url(url), headers={"User-Agent": _UA, "Referer": ORIGIN + "/", "Accept": "text/html"})
    with urllib.request.build_opener(_Redirect()).open(request, timeout=20) as response:
        body = response.read(_MAX_HTML + 1)
        if len(body) > _MAX_HTML:
            raise RuntimeError("歪歪页面返回内容过大")
        return body.decode("utf-8", errors="replace")


def fetch_page(url):
    """Read same-site HTML for the image service's legacy compatibility path."""
    return _page(url)


def cache_key(page):
    """Read only inline script assignments, supporting either quotation style."""
    root = parse_html(page) if isinstance(page, str) else page
    keys = []
    for script in root.all("script"):
        if script.attrs.get("src"):
            continue
        body = "".join(child for child in script.children if isinstance(child, str))
        body = re.sub(r"/\*[\s\S]*?\*/|^\s*//[^\n]*", "", body, flags=re.M)
        keys.extend(match[2] for match in _KEY.finditer(body))
    if not keys or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", keys[-1]):
        raise RuntimeError("歪歪上游图片格式发生变化：当前页面缺少有效 BMI_CACHE_KEY，请稍后重试。")
    return keys[-1]


def _is_ad(node, scope):
    while node is not None and node is not scope:
        if "data-ad-slot" in node.attrs or "data-ad-zone" in node.attrs:
            return True
        if any(re.search(r"(?:^|[-_])(?:ad|ads|advert|gg)(?:[-_]|$)", cls, re.I) for cls in node.attrs.get("class", "").split()):
            return True
        node = node.parent
    return False


def _logical_url(node, version, book_id=None, chapter_id=None):
    raw = node.attrs.get("data-src", "").strip()
    if not raw:
        return ""
    parsed = urlparse(urljoin(ORIGIN + "/", html.unescape(raw)))
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in IMAGE_HOSTS or parsed.username or parsed.password or parsed.port not in {None, 80, 443}:
        return ""
    match = re.fullmatch(r"/(?:break(?:_(?:\d+|avif))?/)?static/upload/book/(\d+)/(?:([^/]+)/)?([^/]+)\.(?:jpe?g|png|gif|webp|avif)", parsed.path, re.I)
    # Explicit v3 manifests define immutable chunk URLs. Their placeholder URL
    # is not required to use the legacy path layout, and must not be rewritten.
    if (not match and not node.attrs.get("data-bmi-manifest")) or not parsed.path:
        return ""
    if match and ((book_id and match[1] != book_id) or (chapter_id and match[2] != chapter_id)):
        return ""
    replacements = {"v": version}
    # Keep the attribute's decoded string exactly; urlencode is only transport
    # escaping. The image service validates explicit hosts/manifests itself.
    if node.attrs.get("data-hosts"):
        replacements["hosts"] = node.attrs["data-hosts"]
    if node.attrs.get("data-bmi-manifest"):
        replacements["manifest"] = node.attrs["data-bmi-manifest"]
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key not in replacements]
    query.extend(replacements.items())
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", urlencode(query), ""))


def _cover(scope, version, book_id):
    if scope is None:
        return ""
    for node in scope.all(cls="cropped"):
        if not _is_ad(node, scope):
            url = _logical_url(node, version, book_id=book_id)
            if url:
                return url
    return ""


def search(keyword):
    keyword = str(keyword).strip()
    if not keyword:
        return []
    root = parse_html(_page(ORIGIN + "/search?" + urlencode({"keyword": keyword})))
    version = cache_key(root)
    scope = root.first(cls="sp-search-grid")
    if scope is None:
        # The site uses a dedicated empty block when there are no results.
        if root.first(cls="sp-empty") or root.first(cls="sp-search-empty"):
            return []
        raise RuntimeError("歪歪搜索主体格式发生变化，无法解析结果")
    output, seen = [], set()
    for card in scope.all("a", cls="sp-search-card"):
        if _is_ad(card, scope):
            continue
        try:
            url = _page_url(card.attrs.get("href", ""), "book")
        except ValueError:
            continue
        book_id = re.search(r"/book/(\d+)", url)[1]
        title = text_of(card.first(cls="sp-search-card-title")) or card.attrs.get("title", "").strip()
        if not title or book_id in seen:
            continue
        seen.add(book_id)
        output.append({"title": title, "url": url, "cover": _cover(card.first(cls="sp-search-card-cover"), version, book_id),
                       "author": "", "latest": "", "description": "", "status": ""})
    return output


def details(url):
    url = _page_url(url, "book")
    book_id = re.search(r"/book/(\d+)", url)[1]
    root = parse_html(_page(url))
    version = cache_key(root)
    hero = root.first(cls="sp-book-hero")
    info = hero.first(cls="sp-book-info") if hero else None
    title = text_of(info.first(cls="sp-book-title")) if info else ""
    if not title:
        raise RuntimeError("歪歪漫画详情主体格式发生变化或作品已下架")
    author = re.sub(r"^(?:作者|作家)\s*[：:]\s*", "", text_of(info.first(cls="sp-book-author")))
    chapters, seen = [], set()
    section = root.first(cls="sp-chapters")
    grid = section.first(cls="sp-chapter-grid") if section else None
    if grid:
        # Current server HTML is already oldest-to-newest, including side stories.
        # Preserve that order rather than sorting chapter IDs or unrelated labels.
        for node in grid.all("a", cls="sp-chapter-item"):
            if _is_ad(node, grid):
                continue
            try:
                chapter_url = _page_url(node.attrs.get("href", ""), "chapter")
            except ValueError:
                continue
            chapter_id = re.search(r"/(?:free-chapter|chapter)/(\d+)", chapter_url)[1]
            if chapter_id in seen:
                continue
            seen.add(chapter_id)
            name = node.attrs.get("title", "").strip() or text_of(node)
            chapters.append({"id": chapter_id, "name": name or f"第 {len(chapters) + 1} 章", "url": chapter_url,
                             "order": len(chapters), "group": "章节"})
    result = {"title": title, "author": author, "description": "", "coverUrl": _cover(hero.first(cls="sp-book-cover"), version, book_id),
              "status": known_status(text_of(info.first(cls="sp-book-meta"))), "chapters": chapters, "sourceUrl": url}
    if not chapters:
        result["unavailableReason"] = "歪歪源未公开可阅读目录，可能需要在源站登录或确认访问权限。"
    return result


def images(url):
    url = _page_url(url, "chapter")
    chapter_id = re.search(r"/(?:free-chapter|chapter)/(\d+)", url)[1]
    root = parse_html(_page(url))
    version = cache_key(root)
    reader = root.first(cls="sp-read-wrap")
    scope = reader.first(cls="comiclist") if reader else None
    if scope is None:
        raise RuntimeError("歪歪源未公开本章正文，可能需要登录或购买，或上游正文格式发生变化。")
    result, seen = [], set()
    for page in scope.all(cls="comicpage"):
        if _is_ad(page, scope):
            continue
        for node in page.all(cls="cropped"):
            if _is_ad(node, scope):
                continue
            logical = _logical_url(node, version, chapter_id=chapter_id)
            if not logical or logical in seen:
                continue
            seen.add(logical)
            result.append(logical)
    if not result:
        raise RuntimeError("歪歪源未返回本章可用图片地址；请在源站确认是否需要登录或购买。")
    return result
