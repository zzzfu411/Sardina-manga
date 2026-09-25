"""Explicit live acceptance through a running local server; saves no images.

Run only when live source verification is intended. Each selected source reads
one ordinary work, its directory, one chapter's metadata and exactly one image.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from PIL import Image

SAMPLES = {
    "sundaywebry": ("名探偵コナン", "https://www.sunday-webry.com/episode/3269754496548998088"),
    "manhua1234": ("一拳超人", "https://m.wmh1234.com/comic/11955.html"),
    "cocoecar": ("一拳超人", "https://www.cocoecar.com/comic/12686"),
    "guazimanhua": ("一拳超人", "https://www.guazimanhua.com/comic.php?id=28524"),
    "manhua6": ("一拳超人", "https://www.hzxidou.com/comic/71150"),
    "terrahistoricus": ("莱茵生命", "https://comic.hypergryph.com/terra-historicus/comic/1421"),
    "namicomi": ("Part-Time Adventurer", "https://namicomi.com/en/title/Lx6v77NL/part-time-adventurer"),
}


def verify(base, site):
    query, expected = SAMPLES[site]
    result = {"siteId": site, "query": query, "expectedDetailUrl": expected}
    start = time.monotonic()

    def api(path, body):
        request = Request(base + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
        with urlopen(request, timeout=55) as response:
            return json.load(response)["data"]

    try:
        groups = api("/api/search", {"siteId": site, "keyword": query})
        if len(groups) != 1 or groups[0].get("error"):
            raise RuntimeError(str(groups))
        rows = groups[0]["results"]
        book = next((row for row in rows if row["detailUrl"].rstrip("/") == expected.rstrip("/")), None)
        if book is None:
            raise RuntimeError("指定普通作品未出现在真实搜索结果中")
        result.update(searchCount=len(rows), title=book["title"], detailUrl=book["detailUrl"])
        detail = api("/api/details", {"siteId": site, "detailUrl": book["detailUrl"]})
        chapters = detail["chapters"]
        if not chapters:
            raise RuntimeError("未返回完整可读目录")
        first = chapters[0]
        result.update(chapterCount=len(chapters), chapterName=first["name"], chapterUrl=first["url"])
        pages = api("/api/chapter-images", {"siteId": site, "chapterUrl": first["url"]})["images"]
        if not pages:
            raise RuntimeError("首章图片元数据为空")
        with urlopen(base + "/api/image?" + urlencode({"siteId": site, "url": pages[0]}), timeout=35) as response:
            content_type = response.headers.get_content_type()
            raw = response.read(12 * 1024 * 1024 + 1)
        with Image.open(BytesIO(raw)) as image:
            image.load()
            result.update(imageCount=len(pages), firstImage={"format": image.format, "size": list(image.size),
                          "bytes": len(raw), "contentType": content_type, "fullyDecoded": True})
        result["passed"] = True
    except Exception as exc:
        result.update(passed=False, error=str(exc)[:500])
    result["elapsedSeconds"] = round(time.monotonic() - start, 2)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--sites", nargs="+", choices=list(SAMPLES), default=list(SAMPLES))
    parser.add_argument("--output", type=Path, default=Path("output/apk-round2-integration/integration-validation.json"))
    args = parser.parse_args()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda site: verify(f"http://127.0.0.1:{args.port}", site), args.sites))
    prior = json.loads(args.output.read_text()) if args.output.exists() else {}
    combined = {row["siteId"]: row for row in prior.get("results", [])}
    combined.update({row["siteId"]: row for row in results})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"checkedAt": datetime.now(timezone.utc).isoformat(),
        "via": "local application HTTP API and image proxy", "imagesSaved": False,
        "results": list(combined.values())}, ensure_ascii=False, indent=2) + "\n")
    raise SystemExit(0 if all(row["passed"] for row in results) else 1)


if __name__ == "__main__":
    main()
