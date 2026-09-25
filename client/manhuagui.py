#!/usr/bin/env python3
"""manhuagui.com native client: search, chapter list, image URLs.

Chapter pages pack SMH.imgData with Dean Edwards + LZString. The 4th packer
argument is LZString base64; String.prototype.splic (crypt_*.js) is
decompressFromBase64(this).split('|') — not a hex-mangled .split.

Images: https://{us|eu*}.hamreus.com{path}{file}?e=&m=  (e/m only from sl).
Adult chapter lists: <input id="__VIEWSTATE"> LZString HTML -> #erroraudit_show.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from lzstring import LZString

try:
    from .search_response import validate_search_response
except ImportError:
    from search_response import validate_search_response

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
GUI_BASE = "https://www.manhuagui.com"
GUI_IMG_HOST = "us"  # picserv: i/eu/eu1/eu2/us/us1/us2/us3
_DIGITS_36 = "0123456789abcdefghijklmnopqrstuvwxyz"

_PACKER_ARGS = re.compile(
    r"\}\s*\(\s*'(?P<p>(?:\\'|[^'])*)'\s*,\s*(?P<a>\d+)\s*,\s*(?P<c>\d+)\s*,\s*'(?P<k>[^']*)'\s*\[",
    re.S,
)
_IMGDATA = re.compile(r"SMH\.imgData\((\{.*?\})\)\s*\.\s*preInit\s*\(\s*\)", re.S)
_SEARCH_ITEM = re.compile(
    r'<li class="cf">\s*<div class="book-cover">\s*'
    r'<a class="bcover" href="(?P<href>/comic/(?P<id>\d+)/)" title="(?P<title>[^"]*)">\s*'
    r'<img src="(?P<cover>[^"]*)"[^>]*>'
    r'(?:.*?<span class="tt">(?P<latest>[^<]*)</span>)?',
    re.S,
)
_CHAPTER = re.compile(
    r'<a href="(?P<href>/comic/(?P<bid>\d+)/(?P<cid>\d+)\.html)" '
    r'title="(?P<title>[^"]*)"[^>]*class="status\d+"[^>]*>'
    r'<span>[^<]*<i>(?P<pages>\d+)p</i></span></a>'
)
_H4 = re.compile(r"<h4><span>([^<]*)</span></h4>")
_VIEWSTATE_TAG = re.compile(
    r"<input[^>]*(?:id|name)=['\"]__VIEWSTATE['\"][^>]*>",
    re.I,
)


def _get(url: str, referer: str = GUI_BASE + "/") -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Referer": referer,
            "Accept": "text/html,application/json,*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=25) as resp:
        raw = resp.read()
        enc = resp.headers.get_content_charset() or "utf-8"
    return raw.decode(enc, "replace")


def _abs(href: str) -> str:
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return GUI_BASE + href
    return href


def _base_n(num: int, radix: int) -> str:
    def rec(c: int) -> str:
        prefix = rec(c // radix) if c >= radix else ""
        d = c % radix
        return prefix + (chr(d + 29) if d > 35 else _DIGITS_36[d])

    return rec(num)


def dean_edwards_unpack(p: str, a: int, c: int, k: list[str]) -> str:
    table = {_base_n(i, a): (k[i] if i < len(k) and k[i] else _base_n(i, a)) for i in range(c)}
    return re.sub(r"\b\w+\b", lambda m: table.get(m.group(0), m.group(0)), p)


def unpack_gui_packer(page: str) -> str:
    m = _PACKER_ARGS.search(page)
    if not m:
        raise RuntimeError("manhuagui packer payload not found")
    p, a, c, k_b64 = m.group("p"), int(m.group("a")), int(m.group("c")), m.group("k")
    if "\\" in p:
        p = p.encode("utf-8").decode("unicode_escape")
    k_src = k_b64
    if "|" not in k_b64:
        dec = LZString.decompressFromBase64(k_b64)
        if not dec:
            raise RuntimeError("LZString.decompressFromBase64 returned empty")
        k_src = dec
    return dean_edwards_unpack(p, a, c, k_src.split("|"))


def gui_unpack_imgdata(page: str) -> dict[str, Any]:
    js = unpack_gui_packer(page)
    m = _IMGDATA.search(js)
    if not m:
        raise RuntimeError(f"SMH.imgData not in unpacked JS: {js[:240]!r}")
    return json.loads(m.group(1))


def _expand_viewstate(page: str) -> str:
    tag_m = _VIEWSTATE_TAG.search(page)
    if not tag_m:
        return page
    val_m = re.search(r'value="([^"]*)"', tag_m.group(0)) or re.search(
        r"value='([^']*)'", tag_m.group(0)
    )
    if not val_m or not val_m.group(1):
        return page
    extra = LZString.decompressFromBase64(val_m.group(1))
    return page + extra if extra else page


def gui_search(q: str) -> list[dict[str, Any]]:
    page = _get(f"{GUI_BASE}/s/{urllib.parse.quote(q)}.html")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for m in _SEARCH_ITEM.finditer(page):
        cid = m.group("id")
        if cid in seen:
            continue
        seen.add(cid)
        cover = m.group("cover") or ""
        out.append(
            {
                "id": cid,
                "title": html.unescape(m.group("title")),
                "url": _abs(m.group("href")),
                "cover": _abs(cover) if cover else "",
                "latest": html.unescape((m.group("latest") or "").strip()),
            }
        )
    validate_search_response("manhuagui", page, out)
    return out


def gui_suggest(q: str) -> Any:
    text = _get(f"{GUI_BASE}/tools/word.ashx?key={urllib.parse.quote(q)}")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def gui_chapters(comic: str | int, *, page: str | None = None) -> list[dict[str, Any]]:
    s = str(comic)
    url = s if s.startswith("http") else f"{GUI_BASE}/comic/{s}/"
    if not url.endswith("/"):
        url += "/"
    page = _expand_viewstate(_get(url) if page is None else page)
    parts = _H4.split(page)
    out: list[dict[str, Any]] = []
    if len(parts) >= 3:
        for i in range(1, len(parts), 2):
            group = parts[i]
            body = parts[i + 1] if i + 1 < len(parts) else ""
            out.extend(_parse_chapter_links(body, group))
    if not out:
        out = _parse_chapter_links(page, "")
    return out


def _parse_chapter_links(page: str, group: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for m in _CHAPTER.finditer(page):
        rows.append(
            {
                "id": m.group("cid"),
                "bid": m.group("bid"),
                "title": html.unescape(m.group("title")),
                "url": _abs(m.group("href")),
                "pages": int(m.group("pages")),
                "group": group,
            }
        )
    return rows


def gui_imgdata_urls(data: dict[str, Any], host: str = GUI_IMG_HOST) -> list[str]:
    path = data["path"]
    sl = data.get("sl") or {}
    query = urllib.parse.urlencode(sl)
    base = f"https://{host}.hamreus.com{path}"
    urls: list[str] = []
    for name in data.get("files") or []:
        u = f"{base}{name}"
        if query:
            u = f"{u}?{query}"
        urls.append(u)
    return urls


def gui_chapter_images(chapter_url: str, host: str = GUI_IMG_HOST) -> list[str]:
    return gui_imgdata_urls(gui_unpack_imgdata(_get(chapter_url, referer=chapter_url)), host=host)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="manhuagui native client")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search"); s.add_argument("q")
    s = sub.add_parser("chapters"); s.add_argument("comic")
    s = sub.add_parser("images"); s.add_argument("chapter_url"); s.add_argument("--host", default=GUI_IMG_HOST)
    s = sub.add_parser("unpack"); s.add_argument("html_path")
    s = sub.add_parser("prove"); s.add_argument("q", nargs="?", default="一拳超人")
    args = p.parse_args(argv)
    if args.cmd == "search":
        print(json.dumps(gui_search(args.q)[:5], ensure_ascii=False, indent=2))
    elif args.cmd == "chapters":
        ch = gui_chapters(args.comic)
        print(json.dumps({"n": len(ch), "head": ch[:3]}, ensure_ascii=False, indent=2))
    elif args.cmd == "images":
        print(json.dumps(gui_chapter_images(args.chapter_url, host=args.host)[:3], indent=2))
    elif args.cmd == "unpack":
        data = gui_unpack_imgdata(Path(args.html_path).read_text("utf-8"))
        print(
            json.dumps(
                {"path": data.get("path"), "file0": (data.get("files") or [None])[0], "sl": data.get("sl")},
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.cmd == "prove":
        saved = Path("/tmp/src-live/gui-ch.body")
        if saved.is_file():
            data = gui_unpack_imgdata(saved.read_text("utf-8"))
            print("UNPACK path:", data.get("path"))
            print("UNPACK files[0]:", (data.get("files") or [None])[0])
            print("UNPACK sl:", json.dumps(data.get("sl"), ensure_ascii=False))
        hits = gui_search(args.q)
        print("SEARCH", len(hits))
        for h in hits[:3]:
            print(" ", h["title"], h["url"])
        chs = gui_chapters(hits[0]["id"] if hits else "7580")
        print("CHAPTERS", len(chs))
        for c in chs[:3]:
            print(" ", c["title"], c["url"])
        imgs = gui_chapter_images("https://www.manhuagui.com/comic/7580/115210.html")
        print("IMAGES", len(imgs))
        for u in imgs[:2]:
            print(" ", u)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
