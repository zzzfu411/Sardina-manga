#!/usr/bin/env python3
"""dm5 / mangabz / manben: chapterfun(chapterimage).ashx + Dean Edwards packer.

Sites share the same ASP.NET reader:
  GET /m{cid}/  →  DM5_* / MANGABZ_* / CHAPTER_ID vars
  GET /m{cid}/{ashx}?cid=&page=&key=&_cid=&_mid=&_dt=&_sign=  (+ language/gtk on dm5/manben)
  body = eval(function(p,a,c,k,e,d){...}) → pix + pvalue[] + key query

mangabz vars are MANGABZ_* (not DM5_*); ashx name is chapterimage.ashx.
manben's cartoonupload reader has no DM5_* block; CHAPTER_ID + mangaImg_N
still work, and the old chapterfun.ashx endpoint is live.
"""

from __future__ import annotations

import argparse
import copy
import http.cookiejar
import json
import os
import re
import ssl
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.request import Request

try:
    from .html_metadata import image_of, known_status, label_value, parse_html, text_of
    from .chapter_order import order_chapters
    from .search_response import validate_search_response
except ImportError:  # Also support the existing python client/dm5_family.py CLI.
    from html_metadata import image_of, known_status, label_value, parse_html, text_of
    from chapter_order import order_chapters
    from search_response import validate_search_response

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    for ca in (
        os.environ.get("SSL_CERT_FILE"),
        "/opt/homebrew/etc/openssl@3/cert.pem",
        "/usr/local/etc/openssl@3/cert.pem",
    ):
        if ca and Path(ca).is_file():
            try:
                ctx.load_verify_locations(cafile=ca)
            except ssl.SSLError:
                pass
    return ctx


_SSL_CTX = _ssl_context()
_CHAPTER_LOOKUP_SLOTS = threading.BoundedSemaphore(4)

SITES: dict[str, dict[str, Any]] = {
    "dm5": {
        "origin": "https://www.dm5.com",
        "search_path": "/search?title={q}",
        "prefixes": ("DM5_",),
        "ashx": "chapterfun.ashx",
        "ashx_extra": {"language": "1", "gtk": "6"},
        "cookies": {},
        "comic_href": re.compile(r"^/manhua-(?!new/?$|rank/?$|jp/?$|list/?$)[^/]+/?$"),
    },
    "mangabz": {
        "origin": "https://www.mangabz.com",
        "search_path": "/search?title={q}",
        "prefixes": ("MANGABZ_",),
        "ashx": "chapterimage.ashx",
        "ashx_extra": {},
        "cookies": {"isAdult": "1"},
        "comic_href": re.compile(r"^/\d+bz/?$"),
    },
    "manben": {
        "origin": "https://www.manben.com",
        "search_path": "/search?title={q}&language=1",
        "prefixes": ("DM5_", "MANBEN_"),
        "ashx": "chapterfun.ashx",
        "ashx_extra": {"language": "1", "gtk": "6"},
        "cookies": {},
        "comic_href": re.compile(r"^/mh-(?!updated/?$|ranklist/?$|list/?$)[^/]+/?$"),
    },
}

_HOST_SITE = {
    "www.dm5.com": "dm5",
    "dm5.com": "dm5",
    "www.mangabz.com": "mangabz",
    "mangabz.com": "mangabz",
    "www.manben.com": "manben",
    "manben.com": "manben",
}

_ABS = re.compile(r"^(?:https?:)?//", re.I)
_PACKER = re.compile(
    r"eval\(function\(p,a,c,k,e,d\)\{.*?return p;?\}\("
    r"'((?:\\.|[^'\\])*)',(\d+),(\d+),'((?:\\.|[^'\\])*)'\.split\('\|'\)",
    re.S,
)
_VAR = re.compile(
    r"(?:var\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:(\d+)|\"([^\"]*)\"|'([^']*)')\s*;"
)
_TITLE_A = re.compile(
    r'<h2 class="title">\s*<a href="([^"]+)"[^>]*(?:title="([^"]*)")?[^>]*>(.*?)</a>',
    re.S | re.I,
)
_P_TITLE_A = re.compile(
    r'<p class="title">\s*<a href="([^"]+)"[^>]*>(.*?)</a>',
    re.S | re.I,
)


class Dm5FamilyError(RuntimeError):
    pass


def detect_site(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower().split(":")[0]
    if host in _HOST_SITE:
        return _HOST_SITE[host]
    for key, sid in _HOST_SITE.items():
        if host.endswith(key):
            return sid
    raise Dm5FamilyError(f"unknown host: {host}")


# --- Dean Edwards packer ---


def _js_unesc(s: str) -> str:
    out: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        if s[i] != "\\" or i + 1 >= n:
            out.append(s[i])
            i += 1
            continue
        c = s[i + 1]
        simple = {"n": "\n", "r": "\r", "t": "\t", "\\": "\\", "'": "'", '"': '"', "/": "/"}
        if c in simple:
            out.append(simple[c])
            i += 2
        elif c == "x" and i + 3 < n:
            out.append(chr(int(s[i + 2 : i + 4], 16)))
            i += 4
        elif c == "u" and i + 5 < n:
            out.append(chr(int(s[i + 2 : i + 6], 16)))
            i += 6
        else:
            out.append(c)
            i += 2
    return "".join(out)


def _unbase(word: str, radix: int) -> int | None:
    n = 0
    for ch in word:
        if "0" <= ch <= "9":
            d = ord(ch) - 48
        elif "a" <= ch <= "z":
            d = ord(ch) - 87
        elif "A" <= ch <= "Z":
            d = ord(ch) - 29
        else:
            return None
        if d >= radix:
            return None
        n = n * radix + d
    return n


def unpack_packer(source: str) -> str:
    """Unpack `eval(function(p,a,c,k,e,d){...}('..',radix,count,'a|b'.split('|'),0,{}))`."""
    m = _PACKER.search(source)
    if not m:
        raise Dm5FamilyError("not a Dean Edwards packer payload")
    payload = _js_unesc(m.group(1))
    radix = int(m.group(2))
    count = int(m.group(3))
    if not 2 <= radix <= 62 or not 0 <= count <= 100000:
        raise Dm5FamilyError("章节编码参数超出支持范围")
    words = _js_unesc(m.group(4)).split("|")
    if len(words) < count:
        words.extend([""] * (count - len(words)))

    def lookup(mo: re.Match[str]) -> str:
        tok = mo.group(0)
        idx = _unbase(tok, radix)
        if idx is None or idx >= len(words):
            return tok
        return words[idx] or tok

    size = len(payload)
    def bounded_lookup(match):
        nonlocal size
        value = lookup(match)
        size += len(value) - (match.end() - match.start())
        if size > 8 * 1024 * 1024:
            raise Dm5FamilyError("章节解码结果过大")
        return value
    return re.sub(r"\b\w+\b", bounded_lookup, payload)


def unpack(source: str) -> str:
    if len(source) > 4 * 1024 * 1024:
        raise Dm5FamilyError("章节编码数据过大")
    source = source.strip()
    if not source:
        raise Dm5FamilyError("empty packer body")
    if not source.lstrip().startswith("eval("):
        return source
    try:
        return unpack_packer(source)
    except (ValueError, Dm5FamilyError) as exc:
        raise Dm5FamilyError("章节编码格式暂不支持，请更换漫画源或稍后重试") from exc


def images_from_unpacked(js: str) -> list[str]:
    pix_m = re.search(r"""\bpix\s*=\s*(?:"([^"]*)"|'([^']*)')""", js)
    pix = (pix_m.group(1) or pix_m.group(2)) if pix_m else ""
    pv_m = re.search(r"\bpvalue\s*=\s*\[(.*?)\]", js, re.S)
    files = [a or b for a, b in re.findall(r""""([^"]+)"|'([^']+)'""", pv_m.group(1) if pv_m else "")]
    suf_m = re.search(
        r"""pvalue\[i\]\s*=\s*pix\s*\+\s*pvalue\[i\]\s*\+\s*(?:'([^']*)'|"([^"]*)")""",
        js,
    )
    suffix = ""
    if suf_m:
        suffix = suf_m.group(1) or suf_m.group(2) or ""
    else:
        cid_m = re.search(r"\bcid\s*=\s*(\d+)", js)
        key_m = re.search(r"""\bkey\s*=\s*(?:'([^']*)'|"([^"]*)")""", js)
        if cid_m and key_m:
            suffix = f"?cid={cid_m.group(1)}&key={key_m.group(1) or key_m.group(2)}"
    urls: list[str] = []
    for item in files:
        if _ABS.match(item):
            urls.append("https:" + item if item.startswith("//") else item)
        else:
            urls.append(pix + item + suffix)
    return urls


# --- HTTP ---


def _set_cookie(jar: http.cookiejar.CookieJar, name: str, value: str, domain: str) -> None:
    jar.set_cookie(
        http.cookiejar.Cookie(
            0,
            name,
            value,
            None,
            False,
            domain,
            True,
            domain.startswith("."),
            "/",
            True,
            False,
            None,
            True,
            None,
            None,
            {},
            False,
        )
    )


def _https_opener(jar: http.cookiejar.CookieJar, ctx: ssl.SSLContext) -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar),
        urllib.request.HTTPSHandler(context=ctx),
    )


@dataclass
class Session:
    jar: http.cookiejar.CookieJar = field(default_factory=http.cookiejar.CookieJar)
    opener: urllib.request.OpenerDirector = field(init=False)

    def __post_init__(self) -> None:
        self.opener = _https_opener(self.jar, _SSL_CTX)

    def get(self, url: str, headers: dict[str, str] | None = None, timeout: float = 30.0) -> tuple[bytes, str, int]:
        h = {"User-Agent": UA, "Accept": "*/*", **(headers or {})}
        req = Request(url, headers=h)
        try:
            with self.opener.open(req, timeout=timeout) as resp:
                return resp.read(), resp.geturl(), getattr(resp, "status", 200)
        except urllib.error.HTTPError as exc:
            body = exc.read() if exc.fp else b""
            return body, url, exc.code
        except urllib.error.URLError as exc:
            reason = str(exc.reason if exc.reason else exc)
            if "CERTIFICATE_VERIFY_FAILED" not in reason:
                raise
            raise Dm5FamilyError("源站证书校验失败，请检查系统时间或 CA 证书后重试") from exc


# --- chapter ---


def _strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s).strip()


def extract_js_vars(html: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in _VAR.finditer(html):
        name = m.group(1)
        if m.group(2) is not None:
            out[name] = m.group(2)
        else:
            out[name] = m.group(3) if m.group(3) is not None else (m.group(4) or "")
    return out


def _pick_var(vars_: dict[str, str], *names: str) -> str | None:
    for n in names:
        if n in vars_ and vars_[n] != "":
            return vars_[n]
    return None


@dataclass
class ChapterMeta:
    site: str
    chapter_url: str
    cid: str
    mid: str
    image_count: int
    sign: str
    sign_dt: str
    page_key: str
    vars: dict[str, str]
    note: str = ""


def parse_chapter_meta(site: str, chapter_url: str, html: str) -> ChapterMeta:
    cfg = SITES[site]
    vars_ = extract_js_vars(html)
    prefixes: tuple[str, ...] = cfg["prefixes"]
    names = {
        "cid": [*(f"{p}CID" for p in prefixes), "CHAPTER_ID", "DM5_CID", "MANGABZ_CID"],
        "mid": [*(f"{p}MID" for p in prefixes), "COMIC_MID", "DM5_MID", "MANGABZ_MID"],
        "count": [*(f"{p}IMAGE_COUNT" for p in prefixes), "DM5_IMAGE_COUNT", "MANGABZ_IMAGE_COUNT"],
        "sign": [*(f"{p}VIEWSIGN" for p in prefixes), "DM5_VIEWSIGN", "MANGABZ_VIEWSIGN"],
        "dt": [*(f"{p}VIEWSIGN_DT" for p in prefixes), "DM5_VIEWSIGN_DT", "MANGABZ_VIEWSIGN_DT"],
    }
    cid = _pick_var(vars_, *names["cid"])
    mid = _pick_var(vars_, *names["mid"]) or ""
    count_s = _pick_var(vars_, *names["count"])
    sign = _pick_var(vars_, *names["sign"]) or ""
    dt = _pick_var(vars_, *names["dt"]) or ""
    notes: list[str] = []
    if not cid:
        path_m = re.search(r"/m(\d+)/?", urllib.parse.urlparse(chapter_url).path)
        if path_m:
            cid = path_m.group(1)
            notes.append("cid from URL path (no *CID var)")
        else:
            raise Dm5FamilyError(f"no chapter id in {chapter_url}: {_wall_reason(html)}")
    img_ids = [int(x) for x in re.findall(r'id=["\']mangaImg_(\d+)["\']', html)]
    if count_s:
        image_count = int(count_s)
    elif img_ids:
        image_count = max(img_ids)
        notes.append(f"IMAGE_COUNT from mangaImg_* (max={image_count})")
    else:
        image_count = 0
        notes.append("IMAGE_COUNT missing; will stop on empty ashx")
    if not sign:
        notes.append("no VIEWSIGN (manben cartoonupload reader still serves chapterfun.ashx)")
    km = re.search(
        r'<input[^>]+id=["\']dm5_key["\'][^>]*value=["\']([^"\']*)["\']',
        html,
        re.I,
    )
    if not km:
        km = re.search(
            r'<input[^>]+value=["\']([^"\']*)["\'][^>]+id=["\']dm5_key["\']',
            html,
            re.I,
        )
    page_key = km.group(1) if km else ""
    return ChapterMeta(
        site=site,
        chapter_url=chapter_url,
        cid=str(cid),
        mid=str(mid),
        image_count=image_count,
        sign=sign,
        sign_dt=dt,
        page_key=page_key,
        vars=vars_,
        note="; ".join(notes),
    )


def _wall_reason(html: str) -> str:
    low = html.lower()
    hints: list[str] = []
    if len(html) < 8000:
        hints.append(f"short HTML ({len(html)} bytes)")
    for pat, label in [
        ("请登录", "login"),
        ("請登錄", "login"),
        ("請登入", "login"),
        ("请登入", "login"),
        ("付费", "paywall"),
        ("付費", "paywall"),
        ("vip專", "vip"),
        ("vip专", "vip"),
        ("年龄", "age-gate"),
        ("年齡", "age-gate"),
        ("isadult", "adult-cookie"),
    ]:
        if pat in low or pat in html:
            hints.append(label)
    if "MANGABZ_CID" in html:
        hints.append("has MANGABZ_* (not DM5_*)")
    if "CHAPTER_ID" in html and "DM5_CID" not in html:
        hints.append("cartoonupload reader (CHAPTER_ID, no DM5_*)")
    return ", ".join(hints) or "vars not in page"


def _canon_chapter_url(url: str) -> str:
    p = urllib.parse.urlparse(url)
    m = re.search(r"/m(\d+)", p.path)
    if not m:
        raise Dm5FamilyError(f"not a /m{{cid}}/ chapter URL: {url}")
    origin = f"{p.scheme}://{p.netloc}"
    return f"{origin}/m{m.group(1)}/"


def fetch_chapter_html(sess: Session, url: str, site: str) -> tuple[str, str]:
    cfg = SITES[site]
    domain = urllib.parse.urlparse(cfg["origin"]).netloc
    cookie_domain = domain[4:] if domain.startswith("www.") else domain
    for k, v in cfg["cookies"].items():
        _set_cookie(sess.jar, k, v, cookie_domain)
        _set_cookie(sess.jar, k, v, domain)
    canon = _canon_chapter_url(url)
    body, final, status = sess.get(
        canon,
        headers={"Referer": cfg["origin"] + "/", "Accept": "text/html,application/xhtml+xml"},
    )
    html = body.decode("utf-8", "replace")
    if status >= 400:
        raise Dm5FamilyError(f"chapter GET {status} {canon}")
    return html, final.split("?")[0].rstrip("/") + "/"


def ashx_urls_for_page(sess: Session, meta: ChapterMeta, page: int) -> list[str]:
    cfg = SITES[meta.site]
    params: dict[str, str] = {
        "cid": meta.cid,
        "page": str(page),
        "key": meta.page_key,
        "_cid": meta.cid,
        "_mid": meta.mid,
        "_dt": meta.sign_dt,
        "_sign": meta.sign,
    }
    params.update(cfg["ashx_extra"])
    url = meta.chapter_url + cfg["ashx"] + "?" + urllib.parse.urlencode(params)
    body, _, status = sess.get(
        url,
        headers={
            "Referer": meta.chapter_url,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "*/*",
        },
    )
    text = body.decode("utf-8", "replace").strip()
    if status >= 400:
        raise Dm5FamilyError(f"{cfg['ashx']} page={page} HTTP {status}")
    if not text or text.startswith("<"):
        return []
    return images_from_unpacked(unpack(text))


def _mangabz_parallel_images(sess: Session, meta: ChapterMeta) -> list[str]:
    """Resolve numbered batches concurrently, retaining positions and completeness checks."""
    with _CHAPTER_LOOKUP_SLOTS:
        first = ashx_urls_for_page(sess, meta, 1)
    count = meta.image_count
    slots: list[str | None] = [None] * count

    def fill(page: int, batch: list[str]) -> None:
        if len(batch) > count - page + 1:
            raise Dm5FamilyError("源站返回了超出声明页数的图片，请重试")
        for offset, url in enumerate(batch):
            if slots[page - 1 + offset] is None:
                slots[page - 1 + offset] = url

    fill(1, first)
    local = threading.local()
    cookies = [copy.copy(cookie) for cookie in sess.jar]

    def fetch(page: int) -> tuple[int, list[str]]:
        if not hasattr(local, "session"):
            jar = http.cookiejar.CookieJar()
            for cookie in cookies:
                jar.set_cookie(copy.copy(cookie))
            local.session = Session(jar=jar)
        with _CHAPTER_LOOKUP_SLOTS:
            return page, ashx_urls_for_page(local.session, meta, page)

    stride = max(1, len(first))
    with ThreadPoolExecutor(max_workers=4) as pool:
        for page, batch in pool.map(fetch, range(1 + stride, count + 1, stride)):
            fill(page, batch)
        # A source may shorten individual batches. Explicitly fill those gaps.
        missing = [index + 1 for index, value in enumerate(slots) if value is None]
        for page, batch in pool.map(fetch, missing):
            fill(page, batch)
    if any(value is None for value in slots) or len(set(slots)) != count:
        raise Dm5FamilyError(f"章节图片页数不完整或重复（源站声明 {count} 页），请重试或切换漫画源")
    return slots


def chapter_images(
    url: str,
    sess: Session | None = None,
    skip_ahead: bool = True,
) -> tuple[list[str], ChapterMeta]:
    site = detect_site(url)
    sess = sess or Session()
    html, canon = fetch_chapter_html(sess, url, site)
    meta = parse_chapter_meta(site, canon, html)
    if site == "mangabz" and skip_ahead and 0 < meta.image_count <= 2000:
        return _mangabz_parallel_images(sess, meta), meta
    seen: list[str] = []
    known: set[str] = set()
    limit = meta.image_count if meta.image_count > 0 else 400
    page = 1
    empty = 0
    while page <= limit:
        batch = ashx_urls_for_page(sess, meta, page)
        if not batch:
            empty += 1
            if empty >= 3 or meta.image_count == 0:
                break
            page += 1
            continue
        empty = 0
        for u in batch:
            if u not in known:
                known.add(u)
                seen.append(u)
        page += len(batch) if skip_ahead else 1
        if meta.image_count and len(seen) >= meta.image_count:
            break
    if meta.image_count > 0 and len(seen) != meta.image_count:
        raise Dm5FamilyError(
            f"章节图片页数不完整（源站声明 {meta.image_count} 页，取得 {len(seen)} 页），请重试或切换漫画源"
        )
    return seen, meta


# --- search ---


def _abs(origin: str, href: str) -> str:
    return urllib.parse.urljoin(origin + "/", href)


def parse_search_html(site: str, html: str, origin: str) -> list[dict[str, str]]:
    href_ok: re.Pattern[str] = SITES[site]["comic_href"]
    root = parse_html(html)
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    blocks = []
    if site == "manben":
        search_root = root.first(cls="mainSearch")
        listing = search_root.first(cls="bookList_2") if search_root else None
        blocks = list(listing.all(cls="item")) if listing else []
    else:
        # DM5 puts the exact title in a separate banner, before the list of
        # fuzzy matches. Ignoring this banner dropped the most useful result.
        if site == "dm5":
            blocks.extend(root.all(cls="banner_detail_form"))
        # The first result list is the search result set. Do not scan later
        # recommendation lists or generic title links in the page shell.
        listing = root.first("ul", cls="mh-list")
        if listing:
            blocks.extend(listing.all(cls="mh-item"))
    for block in blocks:
        title_node = block.first(cls="title")
        link = title_node.first("a") if title_node else None
        if not link:
            continue
        parsed = urllib.parse.urlparse(_abs(origin, link.attrs.get("href", "").strip()))
        if parsed.hostname != urllib.parse.urlparse(origin).hostname or not href_ok.fullmatch(parsed.path):
            continue
        url = _abs(origin, parsed.path)
        title = link.attrs.get("title", "").strip() or link.text()
        if not title or url in seen:
            continue
        seen.add(url)
        info = block.first(cls="manga-info")
        chapter = block.first(cls="chapter")
        cover = image_of(block.first(cls="mh-cover") or block.first(cls="cover") or block.first(cls="book"), origin)
        author = label_value(block, "作者")
        if info and not author:
            # DM5's hover data stores display-name / search-key pairs.
            author = " / ".join(pair.rsplit("-", 1)[0].strip() for pair in info.attrs.get("au", "").split(",") if pair.strip())
        description = text_of(block.first(cls="content"))
        if site == "manben":
            description = text_of(block.first(cls="tip"))
        if info:
            description = description or info.attrs.get("tt", "")
        items.append({
            "title": title, "url": url, "href": parsed.path, "cover": cover,
            "author": author, "description": description,
            "latest": text_of(chapter.first("a")) if chapter else (info.attrs.get("tn", "") if info else ""),
            "status": known_status(label_value(block, "状态|狀態")) or known_status(text_of(chapter.first("span")) if chapter else ""),
        })
    return items


def search(site: str, title: str, sess: Session | None = None, limit: int = 3) -> list[dict[str, str]]:
    if site not in SITES:
        raise Dm5FamilyError(f"unknown site {site}")
    cfg = SITES[site]
    sess = sess or Session()
    q = urllib.parse.quote(title)
    url = cfg["origin"] + cfg["search_path"].format(q=q)
    body, _, status = sess.get(
        url,
        headers={"Referer": cfg["origin"] + "/", "Accept": "text/html"},
    )
    html = body.decode("utf-8", "replace")
    if status >= 400:
        raise Dm5FamilyError(f"search GET {status} {url}")
    rows = parse_search_html(site, html, cfg["origin"])
    validate_search_response(site, html, rows)
    return rows[:limit]


def parse_directory_html(site: str, page: str) -> tuple[list[dict[str, str]], str]:
    """Read chapters only from the directory; sidebar links are other books."""
    root = parse_html(page)
    listing = root.first(ident="chapterlistload") or root.first(ident="chapterList")
    rows, seen = [], set()
    if listing:
        for link in listing.all("a"):
            href = link.attrs.get("href", "")
            if not re.fullmatch(r"/m\d+/", href) or href in seen:
                continue
            name = link.text()
            if not name:
                continue
            seen.add(href)
            rows.append({"name": name, "url": SITES[site]["origin"] + href})
        # These three source directories render newest first. Normalize that
        # direction before the stable title sort so two parts of the same
        # numbered volume retain their reading order. IDs are not ordinals.
        rows = order_chapters(reversed(rows))
    notice = ""
    warning = text_of(root.first(cls="warning-bar"))
    if not rows and warning and ("版权" in warning or "版權" in warning):
        notice = "源站已按版权方要求下架本作品目录，请选择其他漫画源"
    if not rows and not notice:
        blocked = text_of(root.first(cls="banForm"))
        if blocked and any(word in blocked for word in ("章节数据", "章節數據", "屏蔽", "下架")):
            notice = "源站未公开本作品目录（章节数据缺失或已屏蔽），请选择其他漫画源"
    return rows, notice


def first_chapter_url(comic_url: str, sess: Session | None = None) -> str | None:
    site = detect_site(comic_url)
    sess = sess or Session()
    body, final, status = sess.get(comic_url, headers={"Referer": SITES[site]["origin"] + "/"})
    html = body.decode("utf-8", "replace")
    if status >= 400:
        raise Dm5FamilyError(f"detail GET {status} {comic_url}")
    hrefs = re.findall(r'href="(/m\d+/)"', html)
    if not hrefs:
        return None
    uniq: list[str] = []
    seen: set[str] = set()
    for h in hrefs:
        if h not in seen:
            seen.add(h)
            uniq.append(h)
    parsed = urllib.parse.urlparse(final)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return urllib.parse.urljoin(origin + "/", uniq[0])


def dm5_search(q: str, limit: int = 3) -> list[dict[str, str]]:
    return search("dm5", q, limit=limit)


def mangabz_search(q: str, limit: int = 3) -> list[dict[str, str]]:
    return search("mangabz", q, limit=limit)


def manben_search(q: str, limit: int = 3) -> list[dict[str, str]]:
    return search("manben", q, limit=limit)


def chapterfun_images(chapter_url: str, origin: str | None = None) -> list[str]:
    imgs, _meta = chapter_images(chapter_url)
    return imgs


# --- CLI ---


def _print_chapter(url: str) -> int:
    imgs, meta = chapter_images(url)
    print(f"site={meta.site} cid={meta.cid} mid={meta.mid} IMAGE_COUNT={meta.image_count}")
    if meta.sign:
        print(f"VIEWSIGN={meta.sign} DT={meta.sign_dt}")
    if meta.note:
        print(f"note: {meta.note}")
    print(f"unique_images={len(imgs)}")
    for u in imgs[:2]:
        print(u)
    if not imgs:
        print("NO_IMAGES")
        return 1
    return 0


def _print_search(site: str, title: str) -> int:
    rows = search(site, title, limit=3)
    print(f"search {site!r} {title!r} -> {len(rows)}")
    for r in rows:
        print(f"  {r['title']}  {r['url']}")
    return 0 if rows else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="dm5_family")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("chapter")
    c.add_argument("url")
    s = sub.add_parser("search")
    s.add_argument("site", choices=sorted(SITES))
    s.add_argument("title")
    sub.add_parser("demo")
    u = sub.add_parser("unpack")
    u.add_argument("file")
    args = p.parse_args(argv)
    if args.cmd == "chapter":
        return _print_chapter(args.url)
    if args.cmd == "search":
        return _print_search(args.site, args.title)
    if args.cmd == "unpack":
        src = open(args.file, encoding="utf-8", errors="replace").read()
        js = unpack(src)
        print(js)
        for img in images_from_unpacked(js):
            print(img)
        return 0
    if args.cmd == "demo":
        rc = 0
        print("=== dm5 m463652 ===")
        rc |= _print_chapter("https://www.dm5.com/m463652/")
        print("=== mangabz m7274 ===")
        rc |= _print_chapter("https://www.mangabz.com/m7274/")
        print("=== manben search 古惑仔 / 山海逆战 ===")
        rc |= _print_search("manben", "古惑仔")
        rc |= _print_search("manben", "山海逆战")
        manben_rows = search("manben", "古惑仔", limit=3)
        ch = None
        if manben_rows:
            ch = first_chapter_url(manben_rows[0]["url"])
            print(f"manben first chapter of {manben_rows[0]['url']}: {ch}")
        if ch:
            print("=== manben chapter ===")
            rc |= _print_chapter(ch)
        print("=== search dm5 一拳超人 ===")
        rc |= _print_search("dm5", "一拳超人")
        print("=== search mangabz 一拳超人 ===")
        rc |= _print_search("mangabz", "一拳超人")
        print("=== search manben 古惑仔 ===")
        rc |= _print_search("manben", "古惑仔")
        return rc
    return 2


if __name__ == "__main__":
    sys.exit(main())
