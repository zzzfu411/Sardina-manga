"""HTML candidates found in the user's APK/Legado source collection.

No candidate is enabled by the 2026-09-21 live audit: the old GuFeng and
ManhuaDB domains are unavailable; COLAMANGA migrated to yoyomanga.com but
still serves APP-only chapters. Its current App protocol remains unverified.
Keep the historical parsers testable without advertising these as readable
sources. See docs/2026-09-21_COLAMANGA迁移复核.md.

Only literal JSON/base64 data is decoded. Imported rule JavaScript and remote
scripts are never executed.
"""
from __future__ import annotations

import ast
import base64
import binascii
import json
import re
from urllib.parse import quote, urlencode, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from .dm5_family import UA, _SSL_CTX
from .html_metadata import Element, known_status, label_value, parse_html, text_of

# A live search + real directory + image-byte check is required before adding
# a candidate here. Returning HTTP 200 or an APP advertisement is insufficient.
SOURCES: dict[str, tuple[str, str]] = {}
IMAGE_DOMAINS: tuple[str, ...] = ()
HOST_ALIASES: dict[str, tuple[str, ...]] = {}
IMAGE_REFERERS: dict[str, str] = {}

UNAVAILABLE = {
    "colamanga": "源站已迁移至 yoyomanga.com，网页章节为 App 专属，App 阅读接口尚未验证，暂未启用",
    "gufengmh": "古风旧域名已停放或出售，未验证到可阅读的新域名，暂未启用",
    "manhuadb": "漫画 DB 旧域名失效，源表更新域名 TLS 失败且 HTTP 超时，暂未启用",
}

_CANDIDATES = {
    "colamanga": ("COLAMANGA", "https://www.yoyomanga.com", ("www.yoyomanga.com", "www.colamanga.com", "www.cocomanga.com")),
    "gufengmh": ("古风漫画", "https://www.gufengmh9.com", ("www.gufengmh9.com", "m.gufengmh9.com")),
    "manhuadb": ("漫画 DB", "https://www.manhua666.cc", ("www.manhua666.cc",)),
}
_PARKED = re.compile(r"namesilo-expired|router\.parklogic\.com|domain name is for sale|域名可以转让|域名出售", re.I)
_APP_ONLY = re.compile(r"(?:APP|应用|應用).*(?:观看|觀看|阅读|閱讀)|(?:下载|下載).*APP", re.I)
_MAX_HTML = 4 * 1024 * 1024


def _validate_url(site, url):
    if site not in _CANDIDATES:
        raise ValueError("未识别的 APK HTML 漫画源")
    parsed = urlparse(url)
    if (parsed.scheme not in {"http", "https"} or parsed.hostname not in _CANDIDATES[site][2]
            or parsed.username or parsed.password or parsed.port not in (None, 80, 443)):
        raise ValueError("漫画地址与所选 HTML 源不匹配")
    return url


def _enabled(site):
    if site not in _CANDIDATES:
        raise ValueError("未识别的 APK HTML 漫画源")
    if site not in SOURCES:
        raise RuntimeError(UNAVAILABLE[site])


def _fetch(site, url):
    _validate_url(site, url)

    class Redirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            _validate_url(site, newurl)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    opener = build_opener(Redirect(), HTTPSHandler(context=_SSL_CTX))
    with opener.open(Request(url, headers={"User-Agent": UA, "Referer": _CANDIDATES[site][1] + "/"}), timeout=18) as response:
        body = response.read(_MAX_HTML + 1)
        if len(body) > _MAX_HTML:
            raise RuntimeError("源站页面超过大小限制")
        charset = response.headers.get_content_charset() or "utf-8"
        return body.decode(charset, "replace")


def _page(source):
    if _PARKED.search(source):
        raise RuntimeError("图源域名已停放或出售，未返回漫画内容")
    return parse_html(source)


def _cover(root, base):
    for node in root.all():
        if node.tag not in {"a", "img", "mip-img", "amp-img"}:
            continue
        raw = node.attrs.get("data-original") or node.attrs.get("data-src") or (node.attrs.get("src") if node.tag != "a" else "")
        if raw:
            result = urljoin(base, raw)
            if urlparse(result).scheme in {"https", "http"}:
                return result
    return ""


def _metadata(root, base):
    return {"author": label_value(root, r"作者"), "cover": _cover(root, base),
            "description": label_value(root, r"(?:简介|簡介)"),
            "status": known_status(label_value(root, r"(?:状态|狀態)")), "latest": ""}


def parse_search(site, source, base):
    root = _page(source)
    if site == "colamanga":
        scopes = list(root.all(cls="fed-deta-info"))
    elif site == "gufengmh":
        scopes = list(root.all(cls="itemBox"))
        if not scopes:
            scopes = list((root.first(ident="contList") or Element("empty")).all("li"))
    elif site == "manhuadb":
        scopes = list(root.all(cls="comicbook-index")) + list(root.all(cls="comic-book-unit"))
    else:
        raise ValueError("未识别的 APK HTML 漫画源")
    result, seen = [], set()
    for scope in scopes:
        heading = next((scope.first(tag) for tag in ("h1", "h2", "h3") if scope.first(tag)), None)
        link = (heading.first("a") if heading else None) or scope.first("a", cls="title")
        if not link:
            link = next((a for a in scope.all("a") if text_of(a) and not re.search(r"\.html(?:\?|$)", a.attrs.get("href", ""))), None)
        if not link or not text_of(link):
            continue
        url = urljoin(base, link.attrs.get("href", ""))
        try:
            _validate_url(site, url)
        except ValueError:
            continue
        if url in seen:
            continue
        seen.add(url)
        metadata = _metadata(scope, base)
        if site == "manhuadb":
            metadata["author"] = text_of(scope.first(cls="comic-author") or scope.first(cls="comic-creators"))
        if site == "gufengmh":
            metadata["author"] = metadata["author"] or text_of(scope.first(cls="txtItme"))
            metadata["latest"] = text_of(scope.first(cls="coll") or scope.first(cls="tt"))
        result.append({"title": text_of(link), "url": url, **metadata})
    if not scopes and not re.search(r"搜索到相关的|没有找到|未找到|没有搜索到|暂无结果", root.text()):
        raise RuntimeError("源站未返回可识别的漫画搜索列表")
    return result


def parse_details(site, source, url):
    root = _page(source)
    if site == "colamanga":
        scope = root.first(cls="fed-deta-info")
        directory = root.first(cls="fed-play-item")
        intro = None
    elif site == "gufengmh":
        scope = root.first(cls="book-detail") or root.first(cls="pic")
        directory = root.first(ident="chapter-list-1")
        intro = root.first(ident="intro-all") or root.first(ident="intro-cut") or root.first(cls="txtDesc")
    elif site == "manhuadb":
        scope = root.first(cls="comic-info") or root
        directory = root.first(cls="num_div")
        intro = root.first(cls="comic_story")
    else:
        raise ValueError("未识别的 APK HTML 漫画源")
    if scope is None or directory is None:
        raise RuntimeError("源站未返回可识别的作品目录")
    metadata = _metadata(scope, url)
    title = text_of(scope.first("h1") or root.first(cls="comic-title") or root.first(cls="book-title"))
    if intro:
        metadata["description"] = text_of(intro)
    if site == "manhuadb":
        metadata["author"] = text_of(root.first(cls="creators"))
    chapters, seen, app_only = [], set(), False
    for anchor in directory.all("a"):
        name, href = text_of(anchor), anchor.attrs.get("href", "")
        if _APP_ONLY.search(name):
            app_only = True
            continue
        if not name or not href or not re.search(r"\.html(?:\?|$)", href):
            continue
        chapter_url = urljoin(url, href)
        try:
            _validate_url(site, chapter_url)
        except ValueError:
            continue
        if chapter_url in seen:
            continue
        seen.add(chapter_url)
        chapters.append({"id": chapter_url, "name": name, "url": chapter_url, "order": len(chapters), "group": ""})
    # The supplied HTML selectors list chapters in chronological DOM order;
    # do not sort by loose numbers and mix side stories with the main series.
    result = {"title": title, "author": metadata["author"], "description": metadata["description"],
              "status": metadata["status"], "coverUrl": metadata["cover"], "chapters": chapters, "sourceUrl": url}
    if not chapters:
        result["unavailableReason"] = "此作品当前只提供 APP 导流入口，网页没有可读章节" if app_only else "此源暂未提供可读章节"
    return result


def _literal_assignment(source, name):
    pattern = r"\b" + re.escape(name) + r"\s*=\s*(\[[^;]*?\]|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')\s*;"
    match = re.search(pattern, source, re.S)
    if not match or len(match[1]) > 1024 * 1024:
        raise RuntimeError("章节未提供可识别的图片数据")
    try:
        return ast.literal_eval(match[1])
    except (ValueError, SyntaxError, RecursionError) as error:
        raise RuntimeError("章节图片数据不是安全的字面量") from error


def parse_images(site, source, url):
    root = _page(source)
    if site == "colamanga":
        raise RuntimeError("COLAMANGA 当前网页目录为 APP 导流，尚无通过图片实测的阅读接口")
    if site == "manhuadb":
        encoded = _literal_assignment(source, "img_data")
        if not isinstance(encoded, str):
            raise RuntimeError("漫画 DB 图片数据格式错误")
        try:
            data = json.loads(base64.b64decode(encoded, validate=True))
        except (ValueError, binascii.Error, UnicodeDecodeError) as error:
            raise RuntimeError("漫画 DB 图片数据解码失败") from error
        node = next((node for node in root.all() if "data-host" in node.attrs and "data-img_pre" in node.attrs), None)
        if not node or not isinstance(data, list):
            raise RuntimeError("漫画 DB 图片路径信息缺失")
        prefix = urljoin(node.attrs["data-host"], node.attrs["data-img_pre"])
        rows = [item.get("img") if isinstance(item, dict) else item for item in data]
    elif site == "gufengmh":
        rows = _literal_assignment(source, "chapterImages")
        path = _literal_assignment(source, "chapterPath")
        if not isinstance(rows, list) or not isinstance(path, str):
            raise RuntimeError("古风图片列表格式错误")
        prefix = urljoin("https://res.xiaoqinre.com/", path)
    else:
        raise ValueError("未识别的 APK HTML 漫画源")
    images = []
    for item in rows:
        if not isinstance(item, str) or not item.strip():
            raise RuntimeError("章节含有无效图片项")
        image = urljoin(prefix, item)
        parsed = urlparse(image)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
            raise RuntimeError("章节含有无效图片地址")
        if image not in images:
            images.append(image)
    if not images:
        raise RuntimeError("章节没有图片")
    return images


def search(site, keyword):
    _enabled(site)
    origin = _CANDIDATES[site][1]
    if site == "colamanga":
        url = origin + "/search?" + urlencode({"type": 1, "searchString": keyword})
    elif site == "manhuadb":
        url = origin + "/search?" + urlencode({"q": keyword, "p": 1})
    else:
        url = origin + "/search/?keywords=" + quote(keyword)
    return parse_search(site, _fetch(site, url), url)


def details(site, url):
    _enabled(site)
    return parse_details(site, _fetch(site, url), url)


def images(site, url):
    _enabled(site)
    return parse_images(site, _fetch(site, url), url)
