#!/usr/bin/env python3
"""Native (non-MangaYun) clients for aggregator sources.

JSON APIs: hipmh (Comichub), mangacopy, komiic.
HTML / packer: baozimh, manhuazhijia, tuku, comicbox, rumanhua.
manhuagui: client/manhuagui.py (Dean Edwards + LZString splic).
dm5 / mangabz / manben: client/dm5_family.py (chapterfun.ashx + packer).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import os
import re
import ssl
import subprocess
import sys
from decimal import Decimal
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

try:
    from .search_response import validate_search_response
except ImportError:
    from search_response import validate_search_response

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
ROOT = Path(__file__).resolve().parent
DECODER_JS = ROOT / "vendor" / "hipmh-chapter-decoder.js"


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
_SSL_INSECURE = ssl._create_unverified_context()


def _urlopen(req: urllib.request.Request, timeout: float):
    try:
        return urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX)
    except urllib.error.URLError as e:
        if "CERTIFICATE_VERIFY_FAILED" not in str(e.reason if e.reason else e):
            raise
        return urllib.request.urlopen(req, timeout=timeout, context=_SSL_INSECURE)


def _get(url: str, headers: dict[str, str] | None = None, timeout: float = 25.0) -> bytes:
    h = {"User-Agent": UA, **(headers or {})}
    req = urllib.request.Request(url, headers=h)
    with _urlopen(req, timeout) as resp:
        return resp.read()


class SourceBusinessError(RuntimeError):
    """An explicit upstream business failure, separate from transport/parsing."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = str(code)


def _json(url: str, headers: dict[str, str] | None = None, data: bytes | None = None) -> Any:
    h = {"User-Agent": UA, "Accept": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=h, method="POST" if data else "GET")
    with _urlopen(req, 30) as resp:
        result = json.loads(resp.read().decode())
    if isinstance(result, dict):
        if "code" in result and str(result["code"]) not in ("0", "200"):
            raise SourceBusinessError(result["code"], str(result.get("message") or result.get("msg") or result["code"]))
        if result.get("errors"):
            raise RuntimeError("; ".join(e.get("message", "GraphQL 请求失败") for e in result["errors"]))
    return result


def _post_form(url: str, fields: dict[str, str], headers: dict[str, str] | None = None) -> bytes:
    body = urllib.parse.urlencode(fields).encode()
    h = {
        "User-Agent": UA,
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        **(headers or {}),
    }
    req = urllib.request.Request(url, data=body, headers=h, method="POST")
    with _urlopen(req, 30) as resp:
        return resp.read()


def _abs(origin: str, href: str) -> str:
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return urllib.parse.urljoin(origin.rstrip("/") + "/", href.lstrip("/"))


# --- hipmh / 嬉皮漫畫 / Comichub ---

HIP_API = "https://hipapi1.s3file.top"
HIP_IMG = "https://hip-tx-1.s3imgs.top"


def hipmh_search(q: str) -> list[dict[str, Any]]:
    d = _json(f"{HIP_API}/v1/search?q={urllib.parse.quote(q)}")
    return ((d.get("data") or {}).get("data") or [])


def hipmh_chapters(mid: str, page: int = 1, per_page: int = 20, order: str = "asc") -> dict[str, Any]:
    # mid accepts numeric 4296 or token bTo0Mjk2
    url = f"{HIP_API}/v1/manga/chapters?mid={urllib.parse.quote(str(mid))}&page={page}&per_page={per_page}&order={order}"
    return _json(url)


def hipmh_api_hid(chapter_id: int | str, manga_id: int | str, order: float) -> str:
    """v2 hid: b64(c:{cid})-{b64url(mangaId:order)}."""
    import base64

    def b64(s: str, urlsafe: bool = False) -> str:
        raw = base64.b64encode(s.encode()).decode().rstrip("=")
        return raw.replace("+", "-").replace("/", "_") if urlsafe else raw

    left = b64(f"c:{chapter_id}")
    right = b64(f"{manga_id}:{order:.2f}" if isinstance(order, float) else f"{manga_id}:{order}", urlsafe=True)
    return f"{left}-{right}"


def hipmh_chapter_images(api_hid: str) -> list[str]:
    d = _json(
        f"{HIP_API}/v2/chapter?hid={urllib.parse.quote(api_hid)}",
        headers={"Origin": "https://reader.hipmh.top"},
    )
    blob = (d.get("data") or {}).get("images")
    if not isinstance(blob, str):
        raise RuntimeError(f"unexpected chapter payload: {d.get('message')}")
    rel = _hipmh_decode_images(blob)
    return [HIP_IMG + p if p.startswith("/") else p for p in rel]


def _hipmh_decode_images(blob: str) -> list[str]:
    if not DECODER_JS.exists():
        raise RuntimeError(f"missing {DECODER_JS}")
    script = f"""
const fs=require('fs'); const vm=require('vm'); const {{TextDecoder,TextEncoder}}=require('util');
const code=fs.readFileSync({json.dumps(str(DECODER_JS))},'utf8');
const ctx={{console, atob:(s)=>Buffer.from(s,'base64').toString('binary'),
  btoa:(s)=>Buffer.from(s,'binary').toString('base64'), TextDecoder, TextEncoder}};
ctx.window=ctx; vm.createContext(ctx); vm.runInContext(code, ctx);
ctx.window.__cimg.r({json.dumps(blob)}).then(a=>process.stdout.write(JSON.stringify(a)));
"""
    out = subprocess.check_output(["node", "-e", script], timeout=30)
    arr = json.loads(out.decode())
    if not isinstance(arr, list):
        raise RuntimeError("decoder returned non-list")
    return arr


# --- mangacopy ---

COPY_API = "https://api.mangacopy.com"
COPY_HEADERS = {
    "platform": "1",
    "version": "2.3.9",
    "region": "1",
    "webp": "1",
    "accept": "application/json",
}


def copy_search(q: str, limit: int = 5) -> dict[str, Any]:
    url = f"{COPY_API}/api/v3/search/comic?q={urllib.parse.quote(q)}&limit={limit}&offset=0&q_type="
    return _json(url, COPY_HEADERS)


def copy_comic(path_word: str) -> dict[str, Any]:
    return _json(f"{COPY_API}/api/v3/comic2/{path_word}?platform=1", COPY_HEADERS)


def copy_chapters(path_word: str, group: str = "default", limit: int = 100) -> dict[str, Any]:
    return _json(
        f"{COPY_API}/api/v3/comic/{path_word}/group/{group}/chapters?limit={limit}&offset=0&platform=1",
        COPY_HEADERS,
    )


def copy_chapter_images(path_word: str, uuid: str) -> list[str]:
    d = _json(f"{COPY_API}/api/v3/comic/{path_word}/chapter2/{uuid}?platform=1", COPY_HEADERS)
    ch = ((d.get("results") or {}).get("chapter") or {})
    return [x["url"] for x in (ch.get("contents") or []) if x.get("url")]


# --- komiic GraphQL ---

KOMIIC = "https://komiic.com/api/query"


def komiic_gql(query: str, variables: dict[str, Any]) -> Any:
    body = json.dumps({"query": query, "variables": variables}).encode()
    return _json(
        KOMIIC,
        headers={"Content-Type": "application/json", "Origin": "https://komiic.com", "Referer": "https://komiic.com/"},
        data=body,
    )


def komiic_search(q: str) -> list[dict[str, Any]]:
    d = komiic_gql(
        """query searchComicAndAuthorQuery($keyword: String!) {
          searchComicsAndAuthors(keyword: $keyword) { comics { id title status year imageUrl } }
        }""",
        {"keyword": q},
    )
    return (((d.get("data") or {}).get("searchComicsAndAuthors") or {}).get("comics") or [])


def komiic_chapters(comic_id: str) -> list[dict[str, Any]]:
    d = komiic_gql(
        """query chapterByComicId($comicId: ID!) {
          chaptersByComicId(comicId: $comicId) { id serial type dateCreated size }
        }""",
        {"comicId": comic_id},
    )
    return ((d.get("data") or {}).get("chaptersByComicId") or [])


def komiic_tickets(chapter_id: str) -> list[dict[str, Any]]:
    d = komiic_gql(
        """fragment ImageTicketFields on ImageTicket { url ticket kid width height expiresAt }
        query imageTicketsByChapterId($chapterId: ID!) {
          imageTicketsByChapterId(chapterId: $chapterId) { ...ImageTicketFields }
        }""",
        {"chapterId": chapter_id},
    )
    return ((d.get("data") or {}).get("imageTicketsByChapterId") or [])


# --- HTML scrapers ---

BAOZI_ORIGIN = "https://www.baozimh.com"
MHZJ_ORIGIN = "https://www.manhuazhijia.cc"
TUKU_ORIGIN = "https://www.tuku.cc"


def _page(url: str, referer: str | None = None) -> str:
    headers = {"Referer": referer} if referer else None
    return _get(url, headers=headers).decode("utf-8", "replace")


def _strip_tags(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def _id_from_url(url_or_id: str, pattern: str) -> str:
    if "://" in url_or_id or url_or_id.startswith("/"):
        m = re.search(pattern, url_or_id)
        if m:
            return m.group(1)
    return url_or_id.strip().strip("/")


def baozimh_search(q: str) -> list[dict[str, Any]]:
    url = f"{BAOZI_ORIGIN}/search?{urllib.parse.urlencode({'q': q})}"
    page = _page(url, BAOZI_ORIGIN + "/")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for block in re.split(r'<div class="comics-card\b', page)[1:]:
        m = re.search(r'href="(/comic/([^"]+))"\s+title="([^"]*)"', block)
        if not m:
            continue
        cid = m.group(2)
        if cid in seen:
            continue
        seen.add(cid)
        cover = ""
        for src in re.findall(r"<amp-img[^>]+src=\"(https://[^\"]+)\"", block):
            src = html.unescape(src)
            if "default_cover" in src:
                continue
            cover = src
            break
        author = re.search(r'<small\b[^>]*class="[^"]*\btags\b[^"]*"[^>]*>(.*?)</small>', block, re.S)
        out.append(
            {
                "id": cid,
                "title": html.unescape(m.group(3)),
                "url": BAOZI_ORIGIN + m.group(1),
                "cover": cover,
                "author": _strip_tags(author.group(1)) if author else "",
            }
        )
    validate_search_response("baozimh", page, out)
    return out


def baozimh_chapter_url(comic_id: str, section_slot: int, chapter_slot: int) -> str:
    return (
        f"{BAOZI_ORIGIN}/user/page_direct?"
        + urllib.parse.urlencode(
            {"comic_id": comic_id, "section_slot": section_slot, "chapter_slot": chapter_slot}
        )
    )


def baozimh_chapters(comic_id: str, *, page: str | None = None) -> list[dict[str, Any]]:
    comic_id = _id_from_url(comic_id, r"/comic/([^/?#]+)")
    if page is None:
        page = _page(f"{BAOZI_ORIGIN}/comic/{comic_id}", BAOZI_ORIGIN + "/")
    found: dict[tuple[int, int], dict[str, Any]] = {}
    for m in re.finditer(r"<a\s+([^>]+)>(.*?)</a>", page, re.S):
        attrs = m.group(1)
        if "comics-chapters__item" not in attrs:
            continue
        href_m = re.search(r'href="([^"]+)"', attrs)
        if not href_m:
            continue
        href = html.unescape(href_m.group(1))
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
        try:
            sec = int((qs.get("section_slot") or ["0"])[0])
            slot = int((qs.get("chapter_slot") or ["0"])[0])
        except ValueError:
            continue
        key = (sec, slot)
        if key in found:
            continue
        cid = (qs.get("comic_id") or [comic_id])[0]
        found[key] = {
            "comic_id": cid,
            "section_slot": sec,
            "chapter_slot": slot,
            "name": _strip_tags(m.group(2)),
            "url": urllib.parse.urljoin(BAOZI_ORIGIN + "/", href),
        }
    return [found[k] for k in sorted(found)]


def baozimh_chapter_images(comic_id: str, section_slot: int, chapter_slot: int) -> list[str]:
    url = baozimh_chapter_url(comic_id, section_slot, chapter_slot)
    page = _page(url, BAOZI_ORIGIN + "/")
    imgs = re.findall(r'<amp-img[^>]+src="(https://s\d+\.bzcdn\.net/[^"]+)"', page)
    return list(dict.fromkeys(html.unescape(u) for u in imgs))


def manhuazhijia_search(q: str) -> list[dict[str, Any]]:
    url = f"{MHZJ_ORIGIN}/search?{urllib.parse.urlencode({'q': q})}"
    page = _page(url, MHZJ_ORIGIN + "/")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for m in re.finditer(r'<a href="(/comic/([^"]+))" class="comics-card">(.*?)</a>', page, re.S):
        slug = m.group(2)
        if slug in seen:
            continue
        seen.add(slug)
        block = m.group(3)
        title_m = re.search(r'<p class="title">([^<]*)</p>', block)
        author_m = re.search(r'<p class="author">([^<]*)</p>', block)
        img_m = re.search(r'<img[^>]+src="([^"]+)"', block)
        cover = html.unescape(img_m.group(1)) if img_m else ""
        if "placeholder.com" in cover:
            cover = ""
        out.append(
            {
                "id": slug,
                "title": html.unescape(title_m.group(1)).strip() if title_m else slug,
                "author": html.unescape(author_m.group(1)).strip() if author_m else "",
                "url": MHZJ_ORIGIN + m.group(1),
                "cover": cover,
            }
        )
    validate_search_response("manhuazhijia", page, out)
    return out


def manhuazhijia_chapters(slug: str, *, page: str | None = None) -> list[dict[str, Any]]:
    slug = _id_from_url(slug, r"/comic/([^/?#]+)")
    if page is None:
        page = _page(f"{MHZJ_ORIGIN}/comic/{slug}", MHZJ_ORIGIN + "/")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for m in re.finditer(
        r'<a href="(/chapter/(\d+))" class="chapter-item"(?: title="([^"]*)")?>(.*?)</a>',
        page,
        re.S,
    ):
        cid = m.group(2)
        if cid in seen:
            continue
        seen.add(cid)
        name = html.unescape(m.group(3) or "").strip() or _strip_tags(m.group(4))
        out.append({"id": cid, "name": name, "url": MHZJ_ORIGIN + m.group(1)})
    out.reverse()
    return out


def manhuazhijia_chapter_url(chapter: str) -> str:
    if chapter.startswith("http"):
        return chapter
    cid = _id_from_url(chapter, r"/chapter/(\d+)")
    return f"{MHZJ_ORIGIN}/chapter/{cid}"


def manhuazhijia_chapter_images(chapter: str) -> list[str]:
    page = _page(manhuazhijia_chapter_url(chapter), MHZJ_ORIGIN + "/")
    imgs: list[str] = []
    for tag in re.findall(r"<img\b[^>]*>", page, re.I | re.S):
        data = re.search(r'\bdata-src="(https://[^"]+)"', tag)
        src = re.search(r'\bsrc="(https://[^"]+)"', tag)
        raw = (data or src).group(1) if (data or src) else ""
        url = html.unescape(raw)
        if "/scomic/" not in url:
            continue
        imgs.append(url)
    return list(dict.fromkeys(imgs))


def tuku_search(q: str) -> list[dict[str, Any]]:
    # homepage search.js: window.location.href = "/search?title=" + encodeURIComponent(title)
    url = f"{TUKU_ORIGIN}/search?{urllib.parse.urlencode({'title': q})}"
    page = _page(url, TUKU_ORIGIN + "/")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for m in re.finditer(
        r'<a class="swiper-card-item-img[^"]*" href="(/manga-(\d+)/)" title="([^"]*)"[^>]*>\s*<img[^>]+src="([^"]+)"',
        page,
    ):
        mid = m.group(2)
        if mid in seen:
            continue
        seen.add(mid)
        out.append(
            {
                "id": mid,
                "title": html.unescape(m.group(3)),
                "url": TUKU_ORIGIN + m.group(1),
                "cover": html.unescape(m.group(4)),
            }
        )
    validate_search_response("tuku", page, out)
    return out


def tuku_chapters(manga_id: str, *, page: str | None = None) -> list[dict[str, Any]]:
    manga_id = _id_from_url(manga_id, r"/manga-(\d+)")
    if page is None:
        page = _page(f"{TUKU_ORIGIN}/manga-{manga_id}/", TUKU_ORIGIN + "/")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for m in re.finditer(
        r'<a class="[^"]*manga-chapter-item[^"]*"[^>]*href="(/chapter(\d+)/)"[^>]*>(.*?)</a>',
        page,
        re.S,
    ):
        cid = m.group(2)
        if cid in seen:
            continue
        seen.add(cid)
        out.append(
            {
                "id": cid,
                "name": _strip_tags(m.group(3)),
                "url": TUKU_ORIGIN + m.group(1),
            }
        )
    return out


def tuku_chapter_url(chapter: str) -> str:
    if chapter.startswith("http"):
        return chapter if chapter.endswith("/") else chapter + "/"
    cid = _id_from_url(chapter, r"/chapter(\d+)")
    return f"{TUKU_ORIGIN}/chapter{cid}/"


def tuku_chapter_images(chapter_url: str) -> list[str]:
    page = _page(tuku_chapter_url(chapter_url), TUKU_ORIGIN + "/")
    return list(dict.fromkeys(re.findall(r"https://image\d+\.tuku\.cc/[^\"']+", page)))


# --- 如漫画 rumanhua ---
# Search is POST /s {k: keyword} (GET /search.html is 404). Chapter pages pack
# image URLs in a Dean Edwards eval -> __c0rst96, decoded by /static/js/all2.js:
# b64 -> XOR with keys[data-id] -> b64 -> JSON list. Covers stay on ecombdimg;
# page images are signed shimolife URLs. HTTPS cert on rumanhua2.com is wrong.

RUM_ORIGIN = "http://rumanhua2.com"
_RUM_XOR_KEYS_B64 = [
    "c21raHkyNTg=",  # 0 smkhy258
    "c21rZDk1ZnY=",  # 1 smkd95fv
    "bWQ0OTY5NTI=",  # 2 md496952
    "Y2Rjc2R3cQ==",  # 3 cdcsdwq
    "dmJmc2EyNTY=",  # 4 vbfsa256
    "Y2F3ZjE1MWM=",  # 5 cawf151c
    "Y2Q1NmN2ZGE=",  # 6 cd56cvda
    "OGtpaG50OQ==",  # 7 8kihnt9
    "ZHNvMTV0bG8=",  # 8 dso15tlo
    "NWtvNnBsaHk=",  # 9 5ko6plhy
]
_PACKER_RE = re.compile(
    r"eval\(function\(p,a,c,k,e,d\)\{.*?return p\}"
    r"\('((?:\\'|[^'])*)',(\d+),(\d+),'((?:\\'|[^'])*)'\.split\('\|'\),0,\{\}\)",
    re.S,
)


def _b64(data: str | bytes) -> bytes:
    if isinstance(data, bytes):
        data = data.decode("latin-1")
    data = data.strip()
    data += "=" * ((-len(data)) % 4)
    return base64.b64decode(data)


def _dean_token(n: int, base: int) -> str:
    """Dean Edwards / packer digit: 0-9 a-z then A-Z via chr(rem+29)."""

    def digit(rem: int) -> str:
        if rem > 35:
            return chr(rem + 29)
        return "0123456789abcdefghijklmnopqrstuvwxyz"[rem]

    if n < base:
        return digit(n)
    return _dean_token(n // base, base) + digit(n % base)


def _dean_edwards_unpack(js: str) -> str:
    matches = list(_PACKER_RE.finditer(js))
    if not matches:
        raise RuntimeError("no Dean Edwards packer payload")

    def unpack_one(m: re.Match[str]) -> str:
        p, a, c, kblob = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)
        keys = kblob.split("|")
        table = {
            _dean_token(i, a): (keys[i] if i < len(keys) and keys[i] else _dean_token(i, a))
            for i in range(c)
        }
        return re.sub(r"\b\w+\b", lambda mo: table.get(mo.group(0), mo.group(0)), p)

    unpacked = [unpack_one(m) for m in matches]
    for u in unpacked:
        if "__c0rst96" in u or re.search(r'var\s+\w+\s*=\s*"[A-Za-z0-9+/=]{80,}"', u):
            return u
    return unpacked[0]


def _rum_origin(url: str | None = None) -> str:
    if not url:
        return RUM_ORIGIN
    p = urllib.parse.urlparse(url)
    if p.scheme and p.netloc:
        # rumanhua2.com HTTPS cert does not match; keep http unless caller used https.
        return f"{p.scheme}://{p.netloc}"
    return RUM_ORIGIN


def _rum_comic_id(detail_url: str) -> str:
    path = urllib.parse.urlparse(detail_url).path.strip("/")
    if not path:
        raise ValueError(f"no comic id in {detail_url}")
    return path.split("/")[0]


def rum_search(keyword: str, origin: str = RUM_ORIGIN) -> list[dict[str, Any]]:
    """POST /s with k=keyword. Returns detail dicts including absolute url."""
    origin = origin.rstrip("/")
    raw = _post_form(
        f"{origin}/s",
        {"k": keyword},
        headers={"Origin": origin, "Referer": origin + "/"},
    )
    d = json.loads(raw.decode("utf-8"))
    code = str(d.get("code"))
    if code == "201":
        return []
    if code != "200":
        raise RuntimeError(f"rumanhua search {code}: {d.get('msg')}")
    out: list[dict[str, Any]] = []
    for item in d.get("data") or []:
        cid = item.get("id")
        if not cid:
            continue
        out.append(
            {
                "id": cid,
                "title": item.get("name") or "",
                "url": f"{origin}/{cid}/",
                "cover": item.get("imgurl") or "",
                "latest": item.get("remarks") or "",
            }
        )
    return out


def _rum_order_direction(rows: list[dict[str, Any]], page: str, detail_url: str) -> int:
    """Return -1/1 for confirmed descending/ascending order, or 0 if unknown.

    Some Rum titles use bare numbers (``392 subtitle``), which the common
    chapter-label sorter intentionally does not classify. The source's own
    Start Reading link must point to the appropriate endpoint, and at least
    three numbered chapters must be monotonic. Unknown/mixed ordering is
    left untouched; IDs and URL ordering are never used to infer chronology.
    """
    if len(rows) < 3:
        return 0
    panels = re.findall(r'''<div\b[^>]*class=["'][^"']*\bstat-read-box\b[^"']*["'][^>]*>(.*?)</div>''', page, re.S)
    if len(panels) != 1:
        return 0
    starts = set()
    for anchor in re.finditer(r"<a\b([^>]*)>(.*?)</a>", panels[0], re.S):
        href = re.search(r'''\bhref\s*=\s*(["'])(.*?)\1''', anchor[1], re.S)
        if href and _strip_tags(anchor[2]) == "开始阅读":
            starts.add(urllib.parse.urljoin(detail_url, html.unescape(href[2])))
    if len(starts) != 1:
        return 0
    numbers = []
    for row in rows:
        match = re.match(r"^(?:第\s*)?([0-9]{1,5}(?:\.[0-9]{1,3})?)(?:\s*[话話回章]|\s+)", row.get("name", ""))
        if match:
            numbers.append(Decimal(match[1]))
    if len(numbers) >= 3:
        if starts == {rows[-1]["url"]} and numbers[0] > numbers[-1] and all(a >= b for a, b in zip(numbers, numbers[1:])):
            return -1
        if starts == {rows[0]["url"]} and numbers[0] < numbers[-1] and all(a <= b for a, b in zip(numbers, numbers[1:])):
            return 1
    return 0


def _rum_reading_order(rows: list[dict[str, Any]], page: str, detail_url: str) -> list[dict[str, Any]]:
    return list(reversed(rows)) if _rum_order_direction(rows, page, detail_url) == -1 else rows


def rum_chapters(detail_url: str, *, page: str | None = None) -> list[dict[str, Any]]:
    """HTML first ~20 (newest) plus POST /morechapter {id} for the rest."""
    origin = _rum_origin(detail_url)
    if "://" not in detail_url:
        detail_url = f"{origin}/{detail_url.strip('/')}/"
    if not detail_url.endswith("/"):
        detail_url += "/"
    if page is None:
        page = _page(detail_url, origin + "/")
    comic_id = _rum_comic_id(detail_url)
    box = re.search(r'<div class="chaplist-box">(.*?)</div>', page, re.S)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    if box:
        for m in re.finditer(r'<li><a href="(/[^"/]+/([^"/]+)\.html)">([^<]+)</a></li>', box.group(1)):
            chap_id = m.group(2)
            if chap_id in seen:
                continue
            seen.add(chap_id)
            out.append(
                {
                    "id": chap_id,
                    "name": html.unescape(m.group(3)).strip(),
                    "url": _abs(origin, m.group(1)),
                }
            )
    try:
        raw = _post_form(
            f"{origin}/morechapter",
            {"id": comic_id},
            headers={"Origin": origin, "Referer": detail_url},
        )
        extra = json.loads(raw.decode("utf-8"))
    except Exception:
        extra = None
    if extra and str(extra.get("code")) == "200":
        for item in extra.get("data") or []:
            chap_id = item.get("chapterid")
            if not chap_id or chap_id in seen:
                continue
            seen.add(chap_id)
            out.append(
                {
                    "id": chap_id,
                    "name": item.get("chaptername") or "",
                    "url": f"{origin}/{comic_id}/{chap_id}.html",
                }
            )
    return _rum_reading_order(out, page, detail_url)


def rum_images(chapter_url: str) -> list[str]:
    """Unpack packed __c0rst96 and XOR-decode with keys[readerContainer data-id]."""
    origin = _rum_origin(chapter_url)
    page = _page(chapter_url, origin + "/")
    id_m = re.search(r'class="readerContainer"[^>]*data-id="(\d+)"', page) or re.search(
        r'data-id="(\d+)"[^>]*class="readerContainer"', page
    )
    if not id_m:
        raise RuntimeError("missing readerContainer data-id")
    key_idx = int(id_m.group(1))
    if not 0 <= key_idx < len(_RUM_XOR_KEYS_B64):
        raise RuntimeError(f"readerContainer data-id out of range: {key_idx}")
    unpacked = _dean_edwards_unpack(page)
    blob_m = re.search(r'var\s+\w+\s*=\s*"([^"]+)"', unpacked)
    if not blob_m:
        raise RuntimeError("unpacked packer had no string payload")
    xor_key = _b64(_RUM_XOR_KEYS_B64[key_idx])
    cipher = _b64(blob_m.group(1))
    xored = bytes(cipher[i] ^ xor_key[i % len(xor_key)] for i in range(len(cipher)))
    payload = json.loads(_b64(xored).decode("utf-8"))
    if isinstance(payload, list):
        return [u for u in payload if isinstance(u, str) and u.startswith("http")]
    raise RuntimeError(f"unexpected image payload type {type(payload)}")


# --- 歪歪 comicbox ---
# Search is GET /search?keyword= (no JSON/graphql). Chapters are rendered as
# a.sp-chapter-item on /book/{id}. HTML data-src is a *logical* .jpg; the CDN
# 404s that path. Browser fetches AES-CBC split parts via merge_split_file_monga.js:
#   {jpg} -> {host}/break_2/static/upload/book/{book}/{chap}/{page}.b_{0,1}?v={BMI_CACHE_KEY}
# key=b"aaaaaaaaaaaaaaaa" iv=b"0123456789aaaaaa"; break_2 = decrypt-then-concat.
# Then merge_img.0.0.15.js strip-reorders: N=[44..80][md5(bookId+pageNumber)[-1]%10]

BOX_ORIGIN = "https://www.comicbox.xyz"
BOX_CACHE_KEY = "2026021808"
BOX_AES_KEY = b"aaaaaaaaaaaaaaaa"
BOX_AES_IV = b"0123456789aaaaaa"
_BOX_STRIP_TABLE = [44, 48, 52, 56, 60, 64, 68, 72, 76, 80]
_BOX_PAGE_IMG_RE = re.compile(
    r"https://[^\"']+ccavbox\.com/break_2/static/upload/book/\d+/\d+/\d+\.jpg"
)


def box_search(keyword: str) -> list[dict[str, Any]]:
    url = f"{BOX_ORIGIN}/search?{urllib.parse.urlencode({'keyword': keyword})}"
    page = _page(url, BOX_ORIGIN + "/")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for m in re.finditer(
        r'<a href="(/book/(\d+))" class="sp-search-card" title="([^"]*)">(.*?)</a>',
        page,
        re.S,
    ):
        bid = m.group(2)
        if bid in seen:
            continue
        seen.add(bid)
        inner = m.group(4)
        cover_m = re.search(r'data-src="([^"]+)"', inner)
        desc_m = re.search(r'class="sp-search-card-desc">([^<]*)', inner)
        out.append(
            {
                "id": bid,
                "title": html.unescape(m.group(3)),
                "url": BOX_ORIGIN + m.group(1),
                "cover": html.unescape(cover_m.group(1)) if cover_m else "",
                "desc": html.unescape(desc_m.group(1)).strip() if desc_m else "",
            }
        )
    return out


def box_chapters(book_url: str) -> list[dict[str, Any]]:
    if "://" not in book_url:
        book_url = f"{BOX_ORIGIN}/book/{book_url.strip('/')}"
    page = _page(book_url, BOX_ORIGIN + "/")
    title_m = re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S)
    book_title = _strip_tags(title_m.group(1)) if title_m else ""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for m in re.finditer(
        r'<a href="((?:/free-chapter/|/chapter/)\d+[^"]*)" class="sp-chapter-item" title="([^"]*)"',
        page,
    ):
        href = html.unescape(m.group(1))
        if href in seen:
            continue
        seen.add(href)
        cid_m = re.search(r"/(\d+)", href)
        out.append(
            {
                "id": cid_m.group(1) if cid_m else href,
                "name": html.unescape(m.group(2)).strip(),
                "url": _abs(BOX_ORIGIN, href),
                "book_title": book_title,
            }
        )
    return out


def box_images(chapter_url: str) -> list[str]:
    """Logical .jpg URLs from div.cropped[data-src]. Not fetchable; see box_split_urls."""
    page = _page(chapter_url, BOX_ORIGIN + "/")
    imgs = _BOX_PAGE_IMG_RE.findall(page)
    if not imgs:
        imgs = re.findall(
            r'data-src="(https://[^"]+ccavbox\.com/break_2/static/upload/book/\d+/\d+/\d+\.jpg)"',
            page,
        )
    return list(dict.fromkeys(html.unescape(u) for u in imgs))


def box_cache_key(chapter_html: str | None = None) -> str:
    if chapter_html:
        m = re.search(r"BMI_CACHE_KEY\s*=\s*'([^']+)'", chapter_html)
        if m:
            return m.group(1)
    return BOX_CACHE_KEY


def box_split_urls(
    jpg_url: str,
    cache_key: str = BOX_CACHE_KEY,
    prefix: str = "break_2",
    split_count: int = 2,
) -> list[str]:
    """CDN objects actually fetched by merge_split_file_monga.js."""
    clean = re.sub(r"/break[^/]*/", "/", jpg_url)
    clean = re.sub(r"\.(jpeg|jpg|png|gif|avif|webp)(\?.*)?$", "", clean, flags=re.I)
    with_prefix = re.sub(r"(https?://[^/]+)/", rf"\1/{prefix}/", clean, count=1)
    return [f"{with_prefix}.b_{i}?v={cache_key}" for i in range(split_count)]


def box_strip_count(book_id: str, page_number: str) -> int:
    """Same table as merge_img.0.0.15.js / comicbox-engine-CNYk4vYL.js."""
    digest = hashlib.md5(f"{book_id}{page_number}".encode("utf-8")).hexdigest()
    return _BOX_STRIP_TABLE[ord(digest[-1]) % 10]


def comicbox_chapter_images(chapter_url: str) -> list[str]:
    return box_images(chapter_url)


# --- manhuagui / 漫画柜 (client/manhuagui.py) ---
try:
    from manhuagui import (  # type: ignore[import-not-found]
        GUI_IMG_HOST,
        gui_chapter_images,
        gui_chapters,
        gui_search,
        gui_unpack_imgdata,
    )
except ImportError:
    from .manhuagui import (
        GUI_IMG_HOST,
        gui_chapter_images,
        gui_chapters,
        gui_search,
        gui_unpack_imgdata,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("hipmh-search"); s.add_argument("q")
    s = sub.add_parser("hipmh-chapters"); s.add_argument("mid")
    s = sub.add_parser("copy-search"); s.add_argument("q")
    s = sub.add_parser("komiic-search"); s.add_argument("q")
    s = sub.add_parser("baozimh-search"); s.add_argument("q")
    s = sub.add_parser("baozimh-chapters"); s.add_argument("comic_id")
    s = sub.add_parser("baozimh-images"); s.add_argument("comic_id"); s.add_argument("section", type=int); s.add_argument("chapter", type=int)
    s = sub.add_parser("mhzj-search"); s.add_argument("q")
    s = sub.add_parser("mhzj-chapters"); s.add_argument("slug")
    s = sub.add_parser("mhzj-images"); s.add_argument("chapter")
    s = sub.add_parser("tuku-search"); s.add_argument("q")
    s = sub.add_parser("tuku-chapters"); s.add_argument("manga_id")
    s = sub.add_parser("tuku-images"); s.add_argument("chapter")
    s = sub.add_parser("rum-search"); s.add_argument("q")
    s = sub.add_parser("rum-chapters"); s.add_argument("detail_url")
    s = sub.add_parser("rum-images"); s.add_argument("chapter_url")
    s = sub.add_parser("box-search"); s.add_argument("q")
    s = sub.add_parser("box-chapters"); s.add_argument("book_url")
    s = sub.add_parser("box-images"); s.add_argument("chapter_url")
    s = sub.add_parser("html-prove"); s.add_argument("q", nargs="?", default="一拳超人")
    s = sub.add_parser("gui-search"); s.add_argument("q")
    s = sub.add_parser("gui-chapters"); s.add_argument("comic")
    s = sub.add_parser("gui-images"); s.add_argument("chapter_url"); s.add_argument("--host", default=GUI_IMG_HOST)
    s = sub.add_parser("gui-unpack"); s.add_argument("html_path")
    s = sub.add_parser("gui-prove"); s.add_argument("q", nargs="?", default="一拳超人")
    args = p.parse_args(argv)
    if args.cmd == "hipmh-search":
        print(json.dumps(hipmh_search(args.q)[:5], ensure_ascii=False, indent=2))
    elif args.cmd == "hipmh-chapters":
        print(json.dumps(hipmh_chapters(args.mid), ensure_ascii=False, indent=2)[:4000])
    elif args.cmd == "copy-search":
        print(json.dumps(copy_search(args.q), ensure_ascii=False, indent=2)[:2500])
    elif args.cmd == "komiic-search":
        print(json.dumps(komiic_search(args.q), ensure_ascii=False, indent=2))
    elif args.cmd == "baozimh-search":
        print(json.dumps(baozimh_search(args.q)[:10], ensure_ascii=False, indent=2))
    elif args.cmd == "baozimh-chapters":
        chs = baozimh_chapters(args.comic_id)
        print(json.dumps({"n": len(chs), "first": chs[:3], "last": chs[-2:]}, ensure_ascii=False, indent=2))
    elif args.cmd == "baozimh-images":
        print(json.dumps(baozimh_chapter_images(args.comic_id, args.section, args.chapter)[:5], indent=2))
    elif args.cmd == "mhzj-search":
        print(json.dumps(manhuazhijia_search(args.q), ensure_ascii=False, indent=2))
    elif args.cmd == "mhzj-chapters":
        chs = manhuazhijia_chapters(args.slug)
        print(json.dumps({"n": len(chs), "first": chs[:3], "last": chs[-2:]}, ensure_ascii=False, indent=2))
    elif args.cmd == "mhzj-images":
        print(json.dumps(manhuazhijia_chapter_images(args.chapter)[:5], indent=2))
    elif args.cmd == "tuku-search":
        print(json.dumps(tuku_search(args.q)[:10], ensure_ascii=False, indent=2))
    elif args.cmd == "tuku-chapters":
        chs = tuku_chapters(args.manga_id)
        print(json.dumps({"n": len(chs), "first": chs[:3], "last": chs[-2:]}, ensure_ascii=False, indent=2))
    elif args.cmd == "tuku-images":
        print(json.dumps(tuku_chapter_images(args.chapter)[:5], indent=2))
    elif args.cmd == "rum-search":
        print(json.dumps(rum_search(args.q), ensure_ascii=False, indent=2))
    elif args.cmd == "rum-chapters":
        chs = rum_chapters(args.detail_url)
        print(json.dumps({"n": len(chs), "first": chs[:3], "last": chs[-2:]}, ensure_ascii=False, indent=2))
    elif args.cmd == "rum-images":
        print(json.dumps(rum_images(args.chapter_url), ensure_ascii=False, indent=2))
    elif args.cmd == "box-search":
        print(json.dumps(box_search(args.q), ensure_ascii=False, indent=2))
    elif args.cmd == "box-chapters":
        chs = box_chapters(args.book_url)
        print(json.dumps({"n": len(chs), "chapters": chs}, ensure_ascii=False, indent=2))
    elif args.cmd == "box-images":
        imgs = box_images(args.chapter_url)
        payload = {
            "n": len(imgs),
            "images": imgs,
            "splits0": box_split_urls(imgs[0]) if imgs else [],
        }
        if imgs:
            pm = re.search(r"/book/(\d+)/\d+/(\d+)\.jpg", imgs[0])
            if pm:
                payload["strip_count0"] = box_strip_count(pm.group(1), pm.group(2))
        print(json.dumps(payload, indent=2))
    elif args.cmd == "html-prove":
        _html_prove(args.q)
    elif args.cmd == "gui-search":
        print(json.dumps(gui_search(args.q)[:5], ensure_ascii=False, indent=2))
    elif args.cmd == "gui-chapters":
        ch = gui_chapters(args.comic)
        print(json.dumps({"n": len(ch), "head": ch[:3]}, ensure_ascii=False, indent=2))
    elif args.cmd == "gui-images":
        print(json.dumps(gui_chapter_images(args.chapter_url, host=args.host)[:3], indent=2))
    elif args.cmd == "gui-unpack":
        data = gui_unpack_imgdata(Path(args.html_path).read_text("utf-8"))
        print(
            json.dumps(
                {"path": data.get("path"), "file0": (data.get("files") or [None])[0], "sl": data.get("sl")},
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.cmd == "gui-prove":
        _gui_prove(args.q)
    return 0


def _gui_prove(q: str) -> None:
    saved = Path("/tmp/src-live/gui-ch.body")
    if saved.is_file():
        data = gui_unpack_imgdata(saved.read_text("utf-8"))
        print("UNPACK path:", data.get("path"))
        print("UNPACK files[0]:", (data.get("files") or [None])[0])
        print("UNPACK sl:", json.dumps(data.get("sl"), ensure_ascii=False))
    print(f"=== manhuagui search {q!r} ===")
    hits = gui_search(q)
    for h in hits[:3]:
        print(f"  {h['id']}  {h['title']}  {h['url']}")
    comic = hits[0]["id"] if hits else "7580"
    print(f"=== manhuagui chapters {comic} ===")
    chs = gui_chapters(comic)
    print(f"  n={len(chs)}")
    for c in chs[:3]:
        print(f"  {c['title']}  {c['url']}")
    ch_url = "https://www.manhuagui.com/comic/7580/115210.html"
    imgs = gui_chapter_images(ch_url)
    print(f"=== manhuagui images n={len(imgs)} ===")
    for u in imgs[:2]:
        print(f"  {u}")


def _html_prove(q: str) -> None:
    """Live check: 3 search hits, 3 chapters, 2 image URLs per HTML source."""
    print(f"=== baozimh search {q!r} ===")
    hits = baozimh_search(q)
    for h in hits[:3]:
        print(f"  {h['id']}\t{h['title']}\t{h['url']}")
    comic_id = hits[0]["id"] if hits else "yiquanchaoren-one"
    print(f"=== baozimh chapters {comic_id} ===")
    chs = baozimh_chapters(comic_id)
    print(f"  n={len(chs)}")
    for c in chs[:3]:
        print(f"  {c['section_slot']}/{c['chapter_slot']}\t{c['name']}\t{c['url']}")
    c0 = chs[0] if chs else {"comic_id": comic_id, "section_slot": 0, "chapter_slot": 0}
    imgs = baozimh_chapter_images(c0["comic_id"], c0["section_slot"], c0["chapter_slot"])
    print(f"=== baozimh images n={len(imgs)} ===")
    for u in imgs[:2]:
        print(f"  {u}")

    print(f"=== manhuazhijia search {q!r} ===")
    hits = manhuazhijia_search(q)
    for h in hits[:3]:
        print(f"  {h['id']}\t{h['title']}\t{h['url']}")
    slug = hits[0]["id"] if hits else "yiquanchaoren"
    print(f"=== manhuazhijia chapters {slug} ===")
    chs = manhuazhijia_chapters(slug)
    print(f"  n={len(chs)}")
    for c in chs[:3]:
        print(f"  {c['id']}\t{c['name']}\t{c['url']}")
    imgs = manhuazhijia_chapter_images(chs[0]["url"] if chs else "8682991")
    print(f"=== manhuazhijia images n={len(imgs)} ===")
    for u in imgs[:2]:
        print(f"  {u}")

    print(f"=== tuku search {q!r} ===")
    hits = tuku_search(q)
    for h in hits[:3]:
        print(f"  {h['id']}\t{h['title']}\t{h['url']}")
    mid = hits[0]["id"] if hits else "68967"
    print(f"=== tuku chapters {mid} ===")
    chs = tuku_chapters(mid)
    print(f"  n={len(chs)}")
    for c in chs[:3]:
        print(f"  {c['id']}\t{c['name']}\t{c['url']}")
    pick = next((c for c in chs if "第1话" in c["name"]), chs[0] if chs else None)
    imgs = tuku_chapter_images(pick["url"] if pick else "https://www.tuku.cc/chapter685676/")
    print(f"=== tuku images n={len(imgs)} {(pick or {}).get('name', '')} ===")
    for u in imgs[:2]:
        print(f"  {u}")

    rum_q = "火影" if q == "一拳超人" else q
    print(f"=== rumanhua search {rum_q!r} ===")
    hits = rum_search(rum_q)
    for h in hits[:3]:
        print(f"  {h['id']}\t{h['title']}\t{h['url']}")
    detail = hits[0]["url"] if hits else "http://rumanhua2.com/TRxUTBo/"
    print(f"=== rumanhua chapters {detail} ===")
    chs = rum_chapters(detail)
    print(f"  n={len(chs)}")
    for c in chs[:3]:
        print(f"  {c['id']}\t{c['name']}\t{c['url']}")
    if chs:
        print(f"  last {chs[-1]['id']}\t{chs[-1]['name']}")
    img_url = chs[-1]["url"] if chs else "http://rumanhua2.com/TRxUTBo/zOVgVgY.html"
    imgs = rum_images(img_url)
    print(f"=== rumanhua images n={len(imgs)} {img_url} ===")
    for u in imgs[:2]:
        print(f"  {u}")

    print(f"=== comicbox search {q!r} ===")
    hits = box_search(q)
    for h in hits[:3]:
        print(f"  {h['id']}\t{h['title']}\t{h['url']}")
    book = hits[0]["url"] if hits else "https://www.comicbox.xyz/book/568"
    print(f"=== comicbox chapters {book} ===")
    chs = box_chapters(book)
    print(f"  n={len(chs)}")
    for c in chs[:3]:
        print(f"  {c['id']}\t{c['name']}\t{c['url']}")
    imgs = box_images(chs[0]["url"] if chs else "https://www.comicbox.xyz/free-chapter/2513?t=20260415")
    print(f"=== comicbox images n={len(imgs)} ===")
    for u in imgs[:2]:
        print(f"  {u}")
    if imgs:
        pm = re.search(r"/book/(\d+)/\d+/(\d+)\.jpg", imgs[0])
        if pm:
            n = box_strip_count(pm.group(1), pm.group(2))
            print(f"=== comicbox strip_count book={pm.group(1)} page={pm.group(2)} N={n} ===")


if __name__ == "__main__":
    sys.exit(main())
