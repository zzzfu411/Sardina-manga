"""Public, anonymous Terra Historicus and NamiComi catalog APIs.

Protocols were checked against the supplied APKs and current official web
clients. API failures, incomplete pagination and access restrictions are errors;
neither imported JavaScript nor guessed credentials are used. Images are only
returned when the public API supplies their addresses.
"""
from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPException
import json
import re
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from .native_sources import UA, _SSL_CTX


SOURCES = {
    "terrahistoricus": ("泰拉记事社", "comic.hypergryph.com"),
    "namicomi": ("NamiComi 漫画", "namicomi.com"),
}
HOST_ALIASES = {"terrahistoricus": ("terra-historicus.hypergryph.com",)}
IMAGE_DOMAINS = ("web.hycdn.cn", "res01.hycdn.cn", "uploads.namicomi.com")
IMAGE_REFERERS = {
    "terrahistoricus": "https://comic.hypergryph.com/",
    "namicomi": "https://namicomi.com/",
}
SOURCE_NOTICES = {
    "namicomi": "目录包含多种语言，请按章节名称末尾的语言标签选择。登录或付费章节不提供匿名读取。",
}

_API = {"terrahistoricus": "https://comic.hypergryph.com", "namicomi": "https://api.namicomi.com"}
_MAX_BYTES = 4 * 1024 * 1024
_MAX_CHAPTERS = 5000
_MAX_PAGES = 1000
_SEARCH_LIMIT = 250
_PAGE_SIZE = 100
_NAMI_ID = re.compile(r"[A-Za-z0-9]{8}")
_TERRA_ID = re.compile(r"[0-9]{1,20}")
_FILENAME = re.compile(r"[A-Za-z0-9_-]+\.(?:png|jpe?g|webp|avif)", re.I)
_EPISODE_TYPES = {1: "正篇", 2: "番外", 3: "贺图", 4: "公告"}


class SourceError(RuntimeError):
    """The upstream did not return a complete verified result."""


class AccessError(SourceError):
    """The public API requires login, purchase, or another permission."""


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _site(site):
    if site not in SOURCES:
        raise ValueError("未识别的官方漫画源")


def _url_parts(url, hosts):
    if not isinstance(url, str) or not url or any(ord(c) <= 32 for c in url) or "\\" in url:
        raise ValueError("漫画地址无效")
    try:
        p = urlparse(url)
        if (p.scheme != "https" or p.hostname not in hosts or p.username or p.password
                or p.port not in (None, 443) or p.fragment):
            raise ValueError("漫画地址与所选源不匹配")
    except ValueError:
        raise ValueError("漫画地址与所选源不匹配") from None
    for segment in p.path.split("/"):
        decoded = unquote(segment)
        if decoded in {".", ".."} or "/" in decoded or "\\" in decoded or any(ord(c) <= 32 for c in decoded):
            raise ValueError("漫画地址路径无效")
    return p


def _source_ids(site, url, chapter=False):
    _site(site)
    p = _url_parts(url, (SOURCES[site][1], *HOST_ALIASES.get(site, ())))
    if p.query:
        raise ValueError("漫画地址不支持附加参数")
    if site == "terrahistoricus":
        pattern = r"/(?:terra-historicus/)?comic/([0-9]{1,20})"
        if chapter:
            pattern += r"/episode/([0-9]{1,20})"
    else:
        locale = r"(?:[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*/)?"
        pattern = r"/" + locale + (r"chapter/([A-Za-z0-9]{8})" if chapter else r"title/([A-Za-z0-9]{8})(?:/[^/]+)?")
    match = re.fullmatch(pattern + r"/?", p.path)
    if not match:
        raise ValueError("漫画地址不是所选源的有效作品或章节")
    return match.groups()


def _api_url(site, url, expected=None):
    p = _url_parts(url, (urlparse(_API[site]).hostname,))
    if expected is not None:
        original = urlparse(expected)
        if (p.path, p.query) != (original.path, original.query):
            raise ValueError("漫画接口重定向更改了作品或章节")
    return p


class _Redirect(HTTPRedirectHandler):
    def __init__(self, site):
        self.site = site

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _api_url(self.site, newurl, req.full_url)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _get_json(site, path, params=()):
    url = _API[site] + path
    if params:
        url += "?" + urlencode(params)
    _api_url(site, url)
    request = Request(url, headers={"User-Agent": UA, "Accept": "application/json", "Referer": IMAGE_REFERERS[site]})
    opener = build_opener(_Redirect(site), HTTPSHandler(context=_SSL_CTX))
    try:
        with opener.open(request, timeout=20) as response:
            _api_url(site, response.geturl(), url)
            raw = response.read(_MAX_BYTES + 1)
    except HTTPError as exc:
        if exc.code in (401, 402, 403):
            raise AccessError("源站要求登录、付费或访问许可，当前章节无法匿名读取") from None
        raise SourceError(f"源站请求失败（HTTP {exc.code}），请稍后重试") from None
    except (URLError, TimeoutError, OSError, HTTPException):
        raise SourceError("源站暂时无法连接，请稍后重试") from None
    except ValueError:
        raise SourceError("源站接口发生了未验证的跳转") from None
    if len(raw) > _MAX_BYTES:
        raise SourceError("源站响应超过大小限制")
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise SourceError("源站未返回有效的 JSON 数据") from None
    if not isinstance(payload, dict):
        raise SourceError("源站接口格式已变化")
    return payload


def _image_url(value, hosts):
    try:
        p = _url_parts(value, hosts)
        if not p.path or p.path == "/":
            raise ValueError()
    except ValueError:
        raise SourceError("源站返回了未验证的图片地址") from None
    return value


def _cover(value, hosts):
    if not value:
        return ""
    return _image_url(value, hosts)


def _normalized(value):
    return "".join(c for c in unicodedata.normalize("NFKC", _text(value)).casefold() if c.isalnum())


def _terra(path, params=()):
    payload = _get_json("terrahistoricus", path, params)
    if type(payload.get("code")) is not int or payload["code"] != 0 or "data" not in payload:
        raise SourceError("泰拉记事社未返回有效内容，可能尚未公开或暂时不可用")
    return payload["data"]


def _terra_identity(row, expected=None):
    if not isinstance(row, dict) or not isinstance(row.get("cid"), str) or not _TERRA_ID.fullmatch(row["cid"]):
        raise SourceError("泰拉记事社返回了无效的作品标识")
    if expected is not None and row["cid"] != expected:
        raise SourceError("泰拉记事社返回的作品与所选作品不一致")
    return row["cid"]


def _terra_metadata(row):
    _terra_identity(row)
    title = _text(row.get("title"))
    if not title:
        raise SourceError("泰拉记事社未返回作品名称")
    authors = row.get("authors")
    authors = list(dict.fromkeys(_text(x) for x in authors if _text(x))) if isinstance(authors, list) else []
    return {"title": title, "author": " / ".join(authors), "description": _text(row.get("introduction")),
            "coverUrl": _cover(row.get("cover"), ("web.hycdn.cn", "res01.hycdn.cn")), "status": ""}


def _terra_book(cid):
    book = _terra("/api/comic/" + cid)
    _terra_identity(book, cid)
    episodes = book.get("episodes")
    if not isinstance(episodes, list) or len(episodes) > _MAX_CHAPTERS:
        raise SourceError("泰拉记事社未返回完整章节目录")
    ids = [_terra_identity(row) for row in episodes]
    if len(set(ids)) != len(ids):
        raise SourceError("泰拉记事社章节目录存在重复标识")
    if any(type(row.get("type")) is not int for row in episodes):
        raise SourceError("泰拉记事社章节分类格式已变化")
    return book


def _terra_book_url(cid):
    return f"https://comic.hypergryph.com/terra-historicus/comic/{cid}"


def _terra_details(cid):
    book = _terra_book(cid)
    # The official client labels 1/2/3/4 as 正篇/番外/贺图/公告.
    # Source episodes are newest first; timestamps retain release order without
    # interpreting unrelated database IDs as chapter numbers.
    indexed = list(enumerate(book["episodes"]))
    def episode_key(pair):
        index, row = pair
        kind = row.get("type") if type(row.get("type")) is int else 99
        stamp = row.get("displayTime")
        return kind, stamp if type(stamp) in (int, float) else -index
    chapters = []
    for _, row in sorted(indexed, key=episode_key):
        kind = row.get("type")
        group = _EPISODE_TYPES.get(kind, "其他")
        name = " ".join(x for x in (_text(row.get("shortTitle")), _text(row.get("title"))) if x)
        if not name:
            raise SourceError("泰拉记事社章节名称缺失")
        if group != "正篇":
            name = group + " · " + name
        eid = row["cid"]
        chapters.append({"id": eid, "name": name, "url": _terra_book_url(cid) + "/episode/" + eid,
                         "order": len(chapters), "group": group})
    result = {**_terra_metadata(book), "chapters": chapters, "sourceUrl": _terra_book_url(cid),
              "catalogCompleteness": "complete"}
    if not chapters:
        result["unavailableReason"] = "源站尚未公开可阅读的章节"
    return result


def _terra_images(cid, eid):
    book = _terra_book(cid)
    episode = next((x for x in book["episodes"] if x["cid"] == eid), None)
    if episode is None:
        raise SourceError("所选章节不属于这部作品的公开目录")
    path = f"/api/comic/{cid}/episode/{eid}"
    info = _terra(path)
    if not isinstance(info, dict) or any(info.get(k) != episode.get(k) for k in ("title", "shortTitle", "type")):
        raise SourceError("泰拉记事社返回的章节与所选章节不一致")
    pages = info.get("pageInfos")
    if not isinstance(pages, list) or not 0 < len(pages) <= _MAX_PAGES or not all(isinstance(x, dict) for x in pages):
        raise SourceError("泰拉记事社未返回可阅读的页面")
    def fetch_page(number):
        row = _terra(path + "/page", (("pageNum", number),))
        if not isinstance(row, dict) or type(row.get("pageNum")) is not int or row["pageNum"] != number:
            raise SourceError("泰拉记事社返回的页码与所选页面不一致")
        return _image_url(row.get("url"), ("res01.hycdn.cn", "web.hycdn.cn"))
    # Every page has an independent public URL endpoint. map preserves page order.
    with ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(fetch_page, range(1, len(pages) + 1)))


def _nami_payload(path, params=(), kind=None):
    payload = _get_json("namicomi", path, params)
    if payload.get("result") != "ok":
        # Never retry a rejected public request using tokens or constructed URLs.
        errors = payload.get("errors")
        if isinstance(errors, list) and any(isinstance(e, dict) and (
                e.get("status") in (401, 402, 403) or e.get("key") == "error_payment_required") for e in errors):
            raise AccessError("NamiComi 要求登录、付费或访问许可，请在源站确认")
        raise SourceError("NamiComi 未返回有效内容，请稍后重试")
    if kind and payload.get("type") != kind:
        raise SourceError("NamiComi 返回的数据类型与请求不一致")
    return payload


def _nami_entity(row, kind, expected=None):
    if (not isinstance(row, dict) or row.get("type") != kind or not isinstance(row.get("id"), str)
            or not _NAMI_ID.fullmatch(row["id"]) or not isinstance(row.get("attributes"), dict)):
        raise SourceError("NamiComi 返回了无效的作品或章节")
    if expected is not None and row["id"] != expected:
        raise SourceError("NamiComi 返回的作品或章节标识不一致")
    attrs = row["attributes"]
    if attrs.get("state") != "published" or attrs.get("workInProgress") is True:
        raise AccessError("NamiComi 尚未公开此作品或章节")
    if not isinstance(row.get("relationships", []), list):
        raise SourceError("NamiComi 作品关联格式已变化")
    return row


def _nami_get(kind, ident, params=()):
    return _nami_entity(_nami_payload(f"/{kind}/{ident}", params, "entity").get("data"), kind, ident)


def _nami_collection(path, params, kind, maximum):
    rows, seen, offset, total = [], set(), 0, None
    while True:
        payload = _nami_payload(path, (*params, ("limit", _PAGE_SIZE), ("offset", offset)), "collection")
        batch, meta = payload.get("data"), payload.get("meta")
        if (not isinstance(batch, list) or not isinstance(meta, dict)
                or any(type(meta.get(k)) is not int for k in ("limit", "offset", "total"))
                or meta["offset"] != offset or not 0 < meta["limit"] <= _PAGE_SIZE
                or len(batch) > meta["limit"] or meta["total"] < 0):
            raise SourceError("NamiComi 未返回有效分页数据")
        if meta["total"] > maximum:
            raise SourceError("NamiComi 结果过多，请使用更完整的作品名" if kind == "title" else "NamiComi 目录超过完整读取限制")
        if total is not None and meta["total"] != total:
            raise SourceError("NamiComi 目录在读取时发生变化，请重试")
        total = meta["total"]
        for row in batch:
            _nami_entity(row, kind)
            if row["id"] in seen:
                raise SourceError("NamiComi 返回了重复分页，未得到完整目录")
            seen.add(row["id"])
            rows.append(row)
        if len(rows) > total or (not batch and len(rows) < total):
            raise SourceError("NamiComi 分页结果不完整")
        if len(rows) == total:
            return rows
        offset += len(batch)


def _localized(values, preferred=""):
    if not isinstance(values, dict):
        return "", ""
    for locale in dict.fromkeys((preferred, "zh-Hans", "zh-CN", "zh", "zh-Hant", "zh-TW", "en", *values)):
        if _text(values.get(locale)):
            return values[locale].strip(), locale
    return "", ""


def _nami_book_url(row):
    slug = _text(row["attributes"].get("slug"))
    suffix = "/" + quote(slug, safe="-_") if slug else ""
    result = "https://namicomi.com/en/title/" + row["id"] + suffix
    _source_ids("namicomi", result)
    return result


def _nami_metadata(row):
    _nami_entity(row, "title")
    attrs = row["attributes"]
    title, locale = _localized(attrs.get("title"))
    if not title:
        raise SourceError("NamiComi 未返回作品名称")
    relationships = [r for r in row.get("relationships", []) if isinstance(r, dict)]
    covers = [r.get("attributes", {}) for r in relationships if r.get("type") == "cover_art" and isinstance(r.get("attributes"), dict)]
    covers.sort(key=lambda c: c.get("locale") != locale)
    cover = ""
    for c in covers:
        filename = _text(c.get("fileName"))
        if not _FILENAME.fullmatch(filename):
            continue
        cover = "https://uploads.namicomi.com/covers/" + row["id"] + "/" + filename
        break
    # The public title response credits its publishing creator organization.
    names = [_text(r.get("attributes", {}).get("name")) for r in relationships
             if r.get("type") == "organization" and isinstance(r.get("attributes"), dict)]
    return {"title": title, "author": " / ".join(dict.fromkeys(x for x in names if x)),
            "coverUrl": cover, "description": _localized(attrs.get("description"), locale)[0],
            "status": _text(attrs.get("publicationStatus"))}


def _nami_parent(row, expected=None):
    parents = [r.get("id") for r in row.get("relationships", []) if isinstance(r, dict) and r.get("type") == "title"]
    if len(parents) != 1 or not isinstance(parents[0], str) or not _NAMI_ID.fullmatch(parents[0]):
        raise SourceError("NamiComi 未返回章节所属作品")
    if expected is not None and parents[0] != expected:
        raise SourceError("NamiComi 返回的章节不属于所选作品")
    return parents[0]


def _language_label(code):
    return {"en": "English", "es": "Español", "es-419": "Español (Latinoamérica)",
            "zh": "中文", "zh-Hans": "简体中文", "zh-CN": "简体中文", "zh-Hant": "繁體中文", "zh-TW": "繁體中文",
            "ja": "日本語", "ko": "한국어", "fr": "Français", "pt-br": "Português (Brasil)", "pt-BR": "Português (Brasil)"}.get(code, code or "语言未标注")


def _nami_details(ident):
    row = _nami_get("title", ident, (("includes[]", "cover_art"), ("includes[]", "organization")))
    entries = _nami_collection("/chapter", (("titleIds[]", ident), ("order[natural]", "asc")), "chapter", _MAX_CHAPTERS)
    by_language = OrderedDict()
    for chapter in entries:
        _nami_parent(chapter, ident)
        language = _text(chapter["attributes"].get("translatedLanguage"))
        by_language.setdefault(language, []).append(chapter)
    # Keep each translation's API natural order intact. Never interleave equal
    # chapter numbers from different translations in the reader's next sequence.
    languages = sorted(by_language, key=lambda code: 0 if code.lower().startswith("zh") else 1 if code == "en" else 2)
    chapters = []
    for language in languages:
        for chapter in by_language[language]:
            a = chapter["attributes"]
            number, volume, name = _text(a.get("chapter")), _text(a.get("volume")), _text(a.get("name"))
            label = " ".join(x for x in (("第" + volume + "卷") if volume else "", ("第" + number + "章") if number else "", name) if x)
            if not label:
                raise SourceError("NamiComi 章节名称缺失")
            chapters.append({"id": chapter["id"], "name": label + " · " + _language_label(language),
                             "url": "https://namicomi.com/en/chapter/" + chapter["id"], "order": len(chapters), "group": language,
                             "language": language, "sequenceId": "language:" + (language or "unknown")})
    result = {**_nami_metadata(row), "chapters": chapters, "sourceUrl": _nami_book_url(row),
              "sourceNotice": SOURCE_NOTICES["namicomi"], "catalogCompleteness": "complete"}
    if not chapters:
        result["unavailableReason"] = "源站尚未公开可阅读的章节；登录或付费内容需前往源站确认"
    return result


def _nami_images(ident):
    chapter = _nami_get("chapter", ident)
    _nami_parent(chapter)
    if chapter["attributes"].get("gating"):
        raise AccessError("NamiComi 此章节要求访问许可，无法匿名读取")
    count = chapter["attributes"].get("pages")
    if type(count) is not int or not 0 < count <= _MAX_PAGES:
        raise SourceError("NamiComi 未返回可阅读的页数")
    data = _nami_payload("/images/chapter/" + ident, (("newQualities", "true"),), "image_data").get("data")
    if not isinstance(data, dict) or data.get("baseUrl") != "https://uploads.namicomi.com":
        raise SourceError("NamiComi 返回了未验证的图片服务器")
    digest = data.get("hash")
    if not isinstance(digest, str) or not re.fullmatch(r"[a-fA-F0-9]{32,128}", digest):
        raise SourceError("NamiComi 未返回有效章节图片标识")
    pages = data.get("high")
    if not isinstance(pages, list) or len(pages) != count:
        raise SourceError("NamiComi 未返回完整章节图片")
    urls = []
    for page in pages:
        filename = page.get("filename") if isinstance(page, dict) else None
        if not isinstance(filename, str) or not _FILENAME.fullmatch(filename):
            raise SourceError("NamiComi 返回了无效的页面文件名")
        urls.append(f"{data['baseUrl']}/chapter/{ident}/{digest}/high/{filename}")
    if len(set(urls)) != count:
        raise SourceError("NamiComi 返回了重复图片，未得到完整章节")
    return urls


def search(site, keyword):
    """Search public title metadata; a finite Terra catalog is filtered locally."""
    _site(site)
    if not isinstance(keyword, str) or len(keyword) > 300:
        raise ValueError("请输入有效的作品名")
    normalized = _normalized(keyword)
    if not normalized:
        return []
    if site == "terrahistoricus":
        rows = _terra("/api/comic", (("topicKey", "terra-historicus"),))
        if not isinstance(rows, list) or len(rows) > 500:
            raise SourceError("泰拉记事社未返回有效作品列表")
        seen, result = set(), []
        for row in rows:
            cid = _terra_identity(row)
            if cid in seen:
                raise SourceError("泰拉记事社目录存在重复作品")
            seen.add(cid)
            if not any(normalized in _normalized(row.get(k)) for k in ("title", "subtitle")):
                continue
            metadata = _terra_metadata(row)
            metadata["cover"] = metadata.pop("coverUrl")
            titles = list(dict.fromkeys(_text(row.get(k)) for k in ("title", "subtitle") if _text(row.get(k))))
            result.append({**metadata, "url": _terra_book_url(cid), "latest": "",
                           "alternateTitles": [title for title in titles if title != metadata["title"]],
                           "matchedTitle": next(title for title in titles if normalized in _normalized(title))})
        return result
    rows = _nami_collection("/title/search", (("title", keyword.strip()), ("includes[]", "cover_art"),
                                              ("includes[]", "organization")), "title", _SEARCH_LIMIT)
    results = []
    for row in rows:
        titles = row["attributes"].get("title")
        if not isinstance(titles, dict) or not any(normalized in _normalized(t) for t in titles.values()):
            continue
        metadata = _nami_metadata(row)
        metadata["cover"] = metadata.pop("coverUrl")
        names = list(dict.fromkeys(_text(title) for title in titles.values() if _text(title)))
        results.append({**metadata, "url": _nami_book_url(row), "latest": "",
                        "alternateTitles": [title for title in names if title != metadata["title"]],
                        "matchedTitle": next(title for title in names if normalized in _normalized(title))})
    return results


def details(site, url):
    ident, = _source_ids(site, url)
    return _terra_details(ident) if site == "terrahistoricus" else _nami_details(ident)


def images(site, url):
    identities = _source_ids(site, url, chapter=True)
    return _terra_images(*identities) if site == "terrahistoricus" else _nami_images(*identities)
