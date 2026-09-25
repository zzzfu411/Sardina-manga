"""Four public HTML sources verified from the user's second APK rule batch.

The extracted rules are audit evidence, never runtime input. These selectors
describe the live 2026-09-21 pages (several differ from the original XPath).
No JavaScript, login cookies, image tickets, or imported expressions are run.
"""
from __future__ import annotations

import base64
import html
import json
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from .chapter_order import order_chapters
from .dm5_family import UA, _SSL_CTX
from .html_metadata import Element, image_of, known_status, label_value, parse_html, text_of

SOURCES = {
    "manhua1234": ("漫画1234", "m.wmh1234.com"),
    "cocoecar": ("可可漫画", "www.cocoecar.com"),
    "guazimanhua": ("瓜子漫画", "www.guazimanhua.com"),
    "manhua6": ("6漫画", "www.hzxidou.com"),
}
HOST_ALIASES = {"manhua1234": ("reader.hqread.cc",), "cocoecar": ("keke2026.com",)}
IMAGE_REFERERS = {
    "manhua1234": "https://reader.hqread.cc/",
    "cocoecar": "https://keke2026.com/",
    "guazimanhua": "https://www.guazimanhua.com/",
    "manhua6": "https://www.hzxidou.com/",
}
_IMAGE_HOSTS = {
    "manhua1234": ("wmh1234.wszwhg.net",),
    # The current search cards use three CDNs for their different catalogues.
    "cocoecar": ("cocoimg.caiji2029.com", "copyimg.caiji2029.com", "ya2028.mzdtour.com"),
    "guazimanhua": ("img.guazicdn.com",),
    # Current ranking and book pages mix the same verified catalogue CDNs.
    "manhua6": ("manhuagui.caiji2029.com", "copyimg.caiji2029.com", "ya2028.mzdtour.com", "kanmancc.mzdtour.com"),
}
IMAGE_DOMAINS = tuple(dict.fromkeys(host for hosts in _IMAGE_HOSTS.values() for host in hosts))
_SEARCH = {
    "manhua1234": ("/search", "key"),
    "cocoecar": ("/search", "key"),
    "guazimanhua": ("/category.php", "keyword"),
    "manhua6": ("/search", "key"),
}
_MAX_HTML = 4 * 1024 * 1024
_EMPTY = Element("empty")


def _origin(site):
    if site not in SOURCES:
        raise ValueError("未接入的 HTML 漫画源")
    return "https://" + SOURCES[site][1]


def _page_url(site, url):
    _origin(site)
    parsed = urlparse(url)
    hosts = (SOURCES[site][1], *HOST_ALIASES.get(site, ()))
    if (parsed.scheme not in {"http", "https"} or parsed.hostname not in hosts
            or parsed.username or parsed.password or parsed.port not in (None, 80, 443)):
        raise ValueError("漫画地址与所选源不匹配")
    # Keep existing shelf/chapter identities while changing only the verified
    # transport host. URLs from the new site normalize to the same old IDs.
    host = SOURCES[site][1] if site == 'cocoecar' else parsed.hostname
    return parsed._replace(scheme="https", netloc=host, fragment="").geturl()


def _network_url(site, url):
    parsed = urlparse(_page_url(site, url))
    return parsed._replace(netloc='keke2026.com').geturl() if site == 'cocoecar' else parsed.geturl()


def _book_url(site, url):
    parsed = urlparse(_page_url(site, url))
    if parsed.hostname != SOURCES[site][1]:
        raise ValueError("作品地址与所选源不匹配")
    if site == "guazimanhua":
        ids = parse_qs(parsed.query).get("id", [])
        if parsed.path != "/comic.php" or len(ids) != 1 or not re.fullmatch(r"\d{1,12}", ids[0]):
            raise ValueError("作品地址格式无效，请重新搜索")
        return _origin(site) + "/comic.php?" + urlencode({"id": ids[0]})
    pattern = r"/comic/\d{1,12}\.html" if site == "manhua1234" else r"/comic/\d{1,12}/?"
    if not re.fullmatch(pattern, parsed.path):
        raise ValueError("作品地址格式无效，请重新搜索")
    return _origin(site) + parsed.path.rstrip("/")


def _chapter_url(site, url):
    parsed = urlparse(_page_url(site, url))
    if site == "manhua1234":
        prefix = "/r/" if parsed.hostname == "reader.hqread.cc" else "/go/"
        if not re.fullmatch(re.escape(prefix) + r"[A-Za-z0-9_-]{6,160}", parsed.path):
            raise ValueError("章节地址格式无效，请重新打开目录")
        # Keep a single public book-host identity in shelf/chapter links.
        return _origin(site) + "/go/" + parsed.path.split("/")[-1]
    if site == "guazimanhua":
        ids = parse_qs(parsed.query).get("id", [])
        if parsed.path != "/chapter.php" or len(ids) != 1 or not re.fullmatch(r"\d{1,12}", ids[0]):
            raise ValueError("章节地址格式无效，请重新打开目录")
        return _origin(site) + "/chapter.php?" + urlencode({"id": ids[0]})
    if not re.fullmatch(r"/chapter/\d{1,12}/?", parsed.path):
        raise ValueError("章节地址格式无效，请重新打开目录")
    return _origin(site) + parsed.path.rstrip("/")


def _identity_url(site, url):
    for kind, normalizer in (("book", _book_url), ("chapter", _chapter_url)):
        try:
            return kind, normalizer(site, url)
        except ValueError:
            pass
    return None


def _check_redirect(site, original, redirected):
    redirected = _page_url(site, redirected)
    identity = _identity_url(site, original)
    if identity is not None and _identity_url(site, redirected) != identity:
        raise RuntimeError("漫画源重定向到了其他作品或章节，请重新打开目录")
    return redirected


def _get(site, url):
    url = _page_url(site, url)

    class Redirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return super().redirect_request(req, fp, code, msg, headers, _network_url(site, _check_redirect(site, url, newurl)))

    opener = build_opener(Redirect(), HTTPSHandler(context=_SSL_CTX))
    request = Request(_network_url(site, url), headers={"User-Agent": UA, "Referer": IMAGE_REFERERS.get(site, _origin(site) + "/"), "Accept": "text/html"})
    with opener.open(request, timeout=20) as response:
        _check_redirect(site, url, response.geturl())
        body = response.read(_MAX_HTML + 1)
        if len(body) > _MAX_HTML:
            raise RuntimeError("漫画源页面超过大小限制")
        return body.decode(response.headers.get_content_charset() or "utf-8", "replace")


def _tree(source):
    root = parse_html(source)
    title = text_of(root.first("title"))
    if re.search(r"安全验证|人机验证|访问验证|Just a moment|Attention Required", title, re.I) or "cf-chl-" in source:
        raise RuntimeError("漫画源要求验证，请在源站完成验证后再试")
    return root


def _mint_ids(url):
    token = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    try:
        decoded = base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True).decode("ascii")
    except (ValueError, UnicodeError) as error:
        raise RuntimeError("漫画源章节标识格式发生变化") from error
    match = re.fullmatch(r"(\d{1,12})-(\d{1,12})-[A-Za-z0-9_-]+", decoded)
    if not match:
        raise RuntimeError("漫画源章节标识格式发生变化")
    return match[1], match[2]


def _verify_identity(site, root, url, kind):
    normalizer = _book_url if kind == "book" else _chapter_url
    expected = normalizer(site, url)
    checked = 0

    def check_url(value):
        nonlocal checked
        if not value:
            return
        try:
            actual = normalizer(site, urljoin(url, value))
        except ValueError as error:
            raise RuntimeError("漫画源返回的页面身份与请求不一致") from error
        if actual != expected:
            raise RuntimeError("漫画源返回的页面身份与请求不一致")
        checked += 1

    def check_id(value, expected_id):
        nonlocal checked
        if value is None:
            return
        if value != expected_id:
            raise RuntimeError("漫画源返回的页面身份与请求不一致")
        checked += 1

    for link in root.all("link"):
        if "canonical" in link.attrs.get("rel", "").lower().split():
            check_url(link.attrs.get("href"))
    for meta in root.all("meta"):
        if meta.attrs.get("property", "").lower() == "og:url":
            check_url(meta.attrs.get("content"))
    if site == "manhua1234":
        if kind == "book":
            scope = root.first(cls="mint-detail")
            if scope:
                check_id(scope.attrs.get("data-comic-id"), urlparse(expected).path.rsplit("/", 1)[-1].removesuffix(".html"))
        else:
            scope = root.first("main", cls="reader-content")
            if scope:
                reader_base = "https://reader.hqread.cc/"
                if scope.attrs.get("data-chapter-url"):
                    check_url(urljoin(reader_base, scope.attrs["data-chapter-url"]))
                if "data-comic-id" in scope.attrs or "data-chapter-id" in scope.attrs:
                    mid, cid = _mint_ids(expected)
                    check_id(scope.attrs.get("data-comic-id"), mid)
                    check_id(scope.attrs.get("data-chapter-id"), cid)
    elif site in {"cocoecar", "manhua6"}:
        if kind == "book":
            collect = root.first("a", cls="j-user-collect")
            if collect:
                check_id(collect.attrs.get("data-id"), urlparse(expected).path.rsplit("/", 1)[-1])
        else:
            crumb = root.first("a", cls="last-crumb")
            if crumb:
                check_url(crumb.attrs.get("href"))
    elif site == "guazimanhua" and kind == "chapter":
        expected_id = parse_qs(urlparse(expected).query)["id"][0]
        for script in root.all("script"):
            # Corroborate the public page's numeric ID; never execute its JS.
            value = "".join(item for item in script.children if isinstance(item, str))
            for match in re.finditer(r"\b(?:var|let|const)\s+chapterId\s*=\s*(['\"])(\d{1,12})\1", value):
                check_id(match[2], expected_id)
    if not checked:
        raise RuntimeError("漫画源页面缺少可核验的作品或章节标识")


def _ld_item_counts(root, suffix):
    result = []
    for script in root.all("script"):
        if script.attrs.get("type") != "application/ld+json":
            continue
        try:
            data = json.loads("".join(item for item in script.children if isinstance(item, str)))
        except (ValueError, TypeError):
            continue
        objects = data.get("@graph", [data]) if isinstance(data, dict) else data
        if not isinstance(objects, list):
            continue
        for item in objects:
            if not isinstance(item, dict) or item.get("@type") != "ItemList" or not str(item.get("name", "")).endswith(suffix):
                continue
            value = item.get("numberOfItems")
            if not re.fullmatch(r"\d{1,6}", str(value)):
                raise RuntimeError("漫画源声明的内容总数格式发生变化")
            result.append(int(value))
    return result


def _check_counts(actual, expected, label):
    for count in expected:
        if actual != count:
            raise RuntimeError(f"漫画源{label}不完整：声明 {count} 项，实际解析 {actual} 项")


def _image(site, value, base):
    url = urljoin(base, html.unescape(str(value or "").strip()))
    parsed = urlparse(url)
    if (parsed.scheme not in {"http", "https"} or parsed.hostname not in _IMAGE_HOSTS[site]
            or parsed.username or parsed.password or parsed.port not in (None, 80, 443)):
        return ""
    return url


def _cover(site, scope, base):
    return _image(site, image_of(scope, base), base)


def _direct(scope, tag):
    return [node for node in scope.children if isinstance(node, Element) and node.tag == tag] if scope else []


def parse_search(site, source, base=None):
    base = base or _origin(site) + "/"
    root = _tree(source)
    if site == "manhua1234":
        scope = root.first(cls="comic-grid")
        rows = _direct(scope, "article")
    elif site == "cocoecar":
        scope = root.first(cls="cy_list_mh")
        rows = _direct(scope, "ul")
    elif site == "guazimanhua":
        scope = root.first("section", cls="grid")
        rows = _direct(scope, "article")
    elif site == "manhua6":
        scope = root.first(cls="search-comic-list")
        rows = list(scope.all(cls="common-comic-item")) if scope else []
    else:
        raise ValueError("未接入的 HTML 漫画源")
    if scope is None:
        raise RuntimeError("漫画源搜索页面结构发生变化，请稍后再试")
    result, seen = [], set()
    for row in rows:
        if site == "manhua1234":
            heading = row.first(cls="comic-card__title")
            link = row.first("a", cls="comic-card__link")
        elif site == "cocoecar":
            heading = row.first("li", cls="title")
            link = heading.first("a") if heading else None
        elif site == "manhua6":
            heading = row.first(cls="comic__title")
            link = heading.first("a") if heading else None
        else:
            heading = row.first("h3")
            link = heading.first("a") if heading else None
        title = text_of(heading)
        if not title or not link:
            continue
        try:
            url = _book_url(site, urljoin(base, link.attrs.get("href", "")))
        except ValueError:
            continue
        if url in seen:
            continue
        seen.add(url)
        author_node = row.first(cls="comic-card__author") or row.first(cls="comic__author") or row.first(cls="author")
        author = re.sub(r"^作者\s*[：:]?\s*", "", text_of(author_node))
        result.append({"title": title, "url": url, "cover": _cover(site, row, base), "author": author,
                       "latest": text_of(row.first(cls="comic__feature")), "description": "", "status": ""})
    return result


def search(site, keyword):
    origin = _origin(site)
    path, parameter = _SEARCH[site]
    url = origin + path + "?" + urlencode({parameter: keyword})
    return parse_search(site, _get(site, url), url)


def _metadata(site, root, url):
    if site == "manhua1234":
        scope = root.first(cls="mint-work-info") or _EMPTY
        title = text_of(scope.first(ident="mintWorkTitle"))
        author = next((re.sub(r"\s*著$", "", node.text()) for node in _direct(scope, "p") if node.text().endswith("著")), "")
        intro = root.first(cls="mint-intro")
        description = text_of(intro.first("p") if intro else None)
        cover = _cover(site, root.first(ident="mintWorkCover"), url)
        status = known_status(text_of(scope.first(cls="mint-tag")))
    elif site == "cocoecar":
        scope = root.first(cls="cy_intro_l") or _EMPTY
        title = text_of(scope.first("h1"))
        meta = scope.first(cls="cy_xinxi") or _EMPTY
        author = label_value(meta, "作者")
        description = text_of(scope.first(cls="cy_desc"))
        cover = _cover(site, scope, url)
        status = known_status(label_value(meta, "状态"))
    elif site == "guazimanhua":
        title = text_of(root.first(cls="mobile-comic-title"))
        author = label_value(root.first(cls="cinema-strip") or _EMPTY, "作者")
        description = text_of(root.first(cls="mobile-comic-desc"))
        cover = _cover(site, root.first(cls="mobile-comic-cover"), url)
        status = known_status(text_of(root.first(cls="mobile-comic-meta")))
    elif site == "manhua6":
        title = text_of(root.first(cls="j-comic-title"))
        author_scope = root.first(cls="comic-author") or _EMPTY
        author = text_of(author_scope.first(cls="name"))
        description = text_of(root.first(cls="intro-total") or root.first(cls="intro"))
        cover = _cover(site, root.first(cls="de-info__cover"), url)
        status = known_status(text_of(root.first(cls="comic-status")))
    else:
        raise ValueError("未接入的 HTML 漫画源")
    if not title:
        raise RuntimeError("漫画源作品信息结构发生变化，请重新搜索")
    return {"title": title, "author": author, "description": description, "coverUrl": cover, "status": status}


def parse_details(site, source, url):
    url = _book_url(site, url)
    root = _tree(source)
    _verify_identity(site, root, url, "book")
    result = _metadata(site, root, url)
    if site == "manhua1234":
        scopes = [root.first(cls="mint-chapter-grid")]
    elif site == "cocoecar":
        scopes = [node for node in root.all("ul") if re.fullmatch(r"mh-chapter-list-ol-\d+", node.attrs.get("id", ""))]
    elif site == "guazimanhua":
        scopes = [root.first(cls="mobile-chapter-grid")]
    else:
        scopes = list(root.all("ul", cls="chapter__list-box"))
    chapters, seen = [], set()
    for scope in scopes:
        for link in scope.all("a") if scope else []:
            name = text_of(link)
            raw = link.attrs.get("href", "")
            # Navigation/APP controls are not chapter entries. All other links
            # inside the observed directory must remain parseable.
            if (site == "manhua1234" and urlparse(raw).path == "/boluoyuedu.html") or name in {"查看所有章节", "收起", "正序", "倒序"}:
                continue
            if not name:
                raise RuntimeError("漫画源章节目录缺少章节名称")
            try:
                chapter_url = _chapter_url(site, urljoin(url, raw))
            except ValueError as error:
                raise RuntimeError("漫画源章节目录包含无法解析的章节地址") from error
            if site == "manhua1234" and "data-chapter-id" in link.attrs:
                mid, cid = _mint_ids(chapter_url)
                if mid != urlparse(url).path.rsplit("/", 1)[-1].removesuffix(".html") or cid != link.attrs["data-chapter-id"]:
                    raise RuntimeError("漫画源章节目录的作品或章节标识不一致")
            if chapter_url in seen:
                continue
            seen.add(chapter_url)
            chapters.append({"id": chapter_url.rsplit("/", 1)[-1], "name": name, "url": chapter_url, "group": ""})
    if site == "guazimanhua":
        expected = _ld_item_counts(root, "章节目录")
        meta_count = re.search(r"(\d+)\s*[话話]", text_of(root.first(cls="mobile-comic-meta")))
        if meta_count:
            expected.append(int(meta_count[1]))
        if not expected:
            raise RuntimeError("漫画源章节目录缺少声明总数，无法确认目录完整")
        _check_counts(len(chapters), expected, "章节目录")
    # These three directories explicitly display the newest entries first.
    # Preserve the observed ascending DOM of 6漫画; database IDs are not order.
    if site in {"manhua1234", "cocoecar", "guazimanhua"}:
        chapters.reverse()
    if site in {"manhua1234", "guazimanhua"}:
        chapters = order_chapters(chapters)
    for index, chapter in enumerate(chapters):
        chapter["order"] = index
    result.update(chapters=chapters, sourceUrl=url)
    if site == "guazimanhua":
        result["catalogCompleteness"] = "complete"
    if not chapters:
        result["unavailableReason"] = "漫画源未返回公开章节目录，请在源站确认作品状态"
    return result


def details(site, url):
    url = _book_url(site, url)
    return parse_details(site, _get(site, url), url)


def parse_images(site, source, url):
    _chapter_url(site, url)
    root = _tree(source)
    _verify_identity(site, root, url, "chapter")
    expected = []
    if site == "manhua1234":
        scope = root.first("main", cls="reader-content") or _EMPTY
        # The current reader puts every page directly under <main>; the book
        # cover lives inside its metadata div. A renamed page class is an error.
        nodes = _direct(scope, "img")
        if any("reader-image" not in node.attrs.get("class", "").split() for node in nodes):
            raise RuntimeError("漫画源正文图片节点结构发生变化")
    elif site in {"cocoecar", "manhua6"}:
        scope = root.first(cls="rd-article-wr") or _EMPTY
        nodes = []
        for page in scope.all("div", cls="rd-article__pic"):
            images = list(page.all("img", cls="lazy-read"))
            if len(images) != 1:
                raise RuntimeError("漫画源正文图片节点缺失或重复")
            nodes.extend(images)
        count = text_of((root.first(cls="page-index__btn") or _EMPTY).first("span", cls="count"))
        if not re.fullmatch(r"\d{1,6}", count):
            raise RuntimeError("漫画源阅读页缺少声明页数，无法确认图片完整")
        expected.append(int(count))
    elif site == "guazimanhua":
        scope = root.first("section", cls="reader-images") or _EMPTY
        nodes = scope.all("img", cls="reading-image")
        expected = _ld_item_counts(root, "图片列表")
        if not expected:
            raise RuntimeError("漫画源阅读页缺少声明页数，无法确认图片完整")
    else:
        raise ValueError("未接入的 HTML 漫画源")
    result, seen = [], set()
    for image in nodes:
        raw = image.attrs.get("data-src") or image.attrs.get("data-original") or image.attrs.get("src", "")
        image_url = _image(site, raw, url)
        if not image_url:
            # A new CDN or changed reader payload must not silently remove pages.
            raise RuntimeError("漫画源正文图片地址发生变化，请稍后更新该源")
        if image_url and image_url not in seen:
            seen.add(image_url)
            result.append(image_url)
    if not result:
        raise RuntimeError("漫画源未返回公开正文图片，页面结构可能变化或该章节受限")
    _check_counts(len(result), expected, "正文图片")
    return result


def images(site, url):
    url = _chapter_url(site, url)
    if site == "manhua1234":
        # The public /go/ link uses the reader host published in the source rule.
        # This is a fixed host/path mapping, not evaluation of its JS expression.
        url = "https://reader.hqread.cc/r/" + url.rsplit("/", 1)[-1]
    return parse_images(site, _get(site, url), url)
