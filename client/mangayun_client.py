#!/usr/bin/env python3
"""Replay MangaYun (https://mangayun.com/) JSON APIs.

Signature (from /assets/index-bbC94IHS.js):

    SHA256(f"{path}|{raw_body}|{ts_ms}|{nonce}|{SALT}")

Server rejects timestamps older than ~120s. GET catalog endpoints currently
accept unsigned requests; POST /api/search|/api/details|/api/chapter-images
require a valid signature.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

ORIGIN = "https://mangayun.com"
SALT = "ym-salt-883a0f7e-29f1-4b72-9ad2"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _nonce() -> str:
    # Frontend: Math.random().toString(36).substring(2, 10) twice.
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    return "".join(alphabet[secrets.randbelow(36)] for _ in range(16))


def sign_headers(path: str, body: str = "") -> dict[str, str]:
    ts = str(int(time.time() * 1000))
    nonce = _nonce()
    raw = f"{path}|{body}|{ts}|{nonce}|{SALT}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return {
        "x-ym-ts": ts,
        "x-ym-nonce": nonce,
        "x-ym-sign": digest,
    }


def dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


class MangaYun:
    def __init__(self, origin: str = ORIGIN, timeout: float = 40.0) -> None:
        self.origin = origin.rstrip("/")
        self.timeout = timeout

    def request(
        self,
        path: str,
        method: str = "GET",
        obj: Any | None = None,
        extra_headers: dict[str, str] | None = None,
        signed: bool = True,
    ) -> Any:
        body = dumps(obj) if obj is not None else ""
        headers = {
            "User-Agent": UA,
            "Accept": "application/json,text/plain,*/*",
            "Origin": self.origin,
            "Referer": f"{self.origin}/",
            "Content-Type": "application/json",
        }
        if signed:
            headers.update(sign_headers(path, body))
        if extra_headers:
            headers.update(extra_headers)
        data = body.encode("utf-8") if method != "GET" else None
        req = urllib.request.Request(
            self.origin + path, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            raise RuntimeError(f"{method} {path} -> HTTP {exc.code}: {raw[:400]!r}") from exc
        if not raw:
            return None
        return json.loads(raw.decode("utf-8"))

    def sites(self) -> list[dict[str, str]]:
        return self.request("/api/sites", signed=False)["data"]

    def home_sections(self) -> dict[str, Any]:
        return self.request("/api/home-sections", signed=False)["data"]

    def search(self, keyword: str) -> list[dict[str, Any]]:
        return self.request("/api/search", "POST", {"keyword": keyword})["data"]

    def details(self, site_id: str, detail_url: str) -> dict[str, Any]:
        return self.request(
            "/api/details", "POST", {"siteId": site_id, "detailUrl": detail_url}
        )["data"]

    def chapter_images(self, site_id: str, chapter_url: str) -> list[str]:
        data = self.request(
            "/api/chapter-images",
            "POST",
            {"siteId": site_id, "chapterUrl": chapter_url},
        )["data"]
        return data.get("images") or []

    def image_proxy_url(self, url: str, site_id: str | None = None) -> str:
        q = {"url": url}
        if site_id:
            q["siteId"] = site_id
        return f"{self.origin}/api/image?{urllib.parse.urlencode(q)}"


def comicbox_strip_count(book_id: str, page_number: str) -> int:
    """Vertical strip count used by comicbox-engine-CNYk4vYL.js."""
    digest = hashlib.md5(f"{book_id}{page_number}".encode("utf-8")).hexdigest()
    idx = ord(digest[-1]) % 10
    table = [44, 48, 52, 56, 60, 64, 68, 72, 76, 80]
    return table[idx]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MangaYun signed API client")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("sites")
    sub.add_parser("home")

    p_search = sub.add_parser("search")
    p_search.add_argument("keyword")

    p_details = sub.add_parser("details")
    p_details.add_argument("site_id")
    p_details.add_argument("detail_url")

    p_images = sub.add_parser("images")
    p_images.add_argument("site_id")
    p_images.add_argument("chapter_url")

    p_sign = sub.add_parser("sign")
    p_sign.add_argument("path")
    p_sign.add_argument("--body", default="")

    args = parser.parse_args(argv)
    client = MangaYun()

    if args.cmd == "sign":
        print(json.dumps(sign_headers(args.path, args.body), indent=2, ensure_ascii=False))
        return 0
    if args.cmd == "sites":
        print(json.dumps(client.sites(), indent=2, ensure_ascii=False))
        return 0
    if args.cmd == "home":
        print(json.dumps(client.home_sections(), indent=2, ensure_ascii=False))
        return 0
    if args.cmd == "search":
        print(json.dumps(client.search(args.keyword), indent=2, ensure_ascii=False))
        return 0
    if args.cmd == "details":
        print(json.dumps(client.details(args.site_id, args.detail_url), indent=2, ensure_ascii=False))
        return 0
    if args.cmd == "images":
        print(json.dumps(client.chapter_images(args.site_id, args.chapter_url), indent=2, ensure_ascii=False))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
