"""Application-facing adapters. No dependency on MangaYun in native mode."""
from __future__ import annotations

import base64
import re
from urllib.parse import parse_qs, quote, urlencode, urlparse

from . import native_sources as n, dm5_family as dm, manhuagui as gui
from . import apk_wap, apk_dmzj, apk_kuaikan, apk_sunday, apk_vomic_html, apk_official_catalogs
from . import comicbox, komiic, mangacopy_web, hipmh_metadata
from .html_metadata import detail_metadata
from .chapter_order import order_chapters

SOURCES = {
    "hipmh": ("嬉皮漫画", "reader.hipmh.top"),
    "baozimh": ("包子漫画", "www.baozimh.com"),
    "manhuazhijia": ("漫画之家", "www.manhuazhijia.cc"),
    "mangacopy": ("拷贝漫画", "www.mangacopy.com"),
    "manhuagui": ("漫画柜", "www.manhuagui.com"),
    "dm5": ("动漫屋", "www.dm5.com"),
    "mangabz": ("漫画巴士", "www.mangabz.com"),
    "manben": ("漫本", "www.manben.com"),
    "tuku": ("图库漫画", "www.tuku.cc"),
    "rumanhua": ("如漫画", "rumanhua2.com"),
    "komiic": ("Komiic", "komiic.com"),
    "comicbox": ("歪歪漫画", "www.comicbox.xyz"),
}

# Executable adapters are registered explicitly. The extracted APK/rule files
# are development evidence, never runtime code or a source of arbitrary URLs.
APK_ADAPTERS = (apk_wap, apk_dmzj, apk_kuaikan, apk_sunday, apk_vomic_html, apk_official_catalogs)
APK_PROVIDERS = {site: module for module in APK_ADAPTERS for site in module.SOURCES}
for module in APK_ADAPTERS:
    if SOURCES.keys() & module.SOURCES.keys():
        raise RuntimeError("漫画源编号重复")
    SOURCES.update(module.SOURCES)
EXTRA_IMAGE_DOMAINS = tuple(dict.fromkeys(domain for module in APK_ADAPTERS for domain in module.IMAGE_DOMAINS))
IMAGE_REFERERS = {site: ref for module in APK_ADAPTERS for site, ref in getattr(module, "IMAGE_REFERERS", {}).items()}
SOURCE_NOTICES = {site: note for module in APK_ADAPTERS for site, note in getattr(module, "SOURCE_NOTICES", {}).items()}


def validate_url(site: str, url: str) -> str:
    if site not in SOURCES:
        raise ValueError("未接入的漫画源")
    p = urlparse(url)
    host = SOURCES[site][1].removeprefix("www.")
    hosts = {host, "www." + host}
    if site == "hipmh":
        hosts.add("m.hipmh.com")
    if site in APK_PROVIDERS:
        hosts.update(getattr(APK_PROVIDERS[site], "HOST_ALIASES", {}).get(site, ()))
    if (p.scheme not in ("http", "https") or p.hostname not in hosts
            or p.username or p.password or p.port not in (None, 80, 443)):
        raise ValueError("漫画地址与所选源不匹配")
    return url


def sites():
    return [{"siteId": k, "siteName": v[0], **({"notice": SOURCE_NOTICES[k]} if k in SOURCE_NOTICES else {})}
            for k, v in SOURCES.items()]


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _search_title_evidence(row):
    """Keep only source-supplied title evidence, never inferred aliases."""
    values = row.get("alternateTitles")
    aliases = list(dict.fromkeys(_text(value) for value in values if _text(value)))[:32] if isinstance(values, list) else []
    matched = _text(row.get("matchedTitle"))
    result = {"alternateTitles": aliases} if aliases else {}
    if matched and matched in [_text(row.get("title")), *aliases]:
        result["matchedTitle"] = matched
    return result


def search(site: str, keyword: str):
    if site in APK_PROVIDERS:
        rows = APK_PROVIDERS[site].search(site, keyword)
    elif site == "comicbox":
        rows = comicbox.search(keyword)
    elif site == "hipmh":
        rows = n.hipmh_search(keyword)
        for r in rows:
            mid = r["id"].split("-")[0]
            r["url"] = f"https://reader.hipmh.top/manga/{quote(mid)}"
            r["cover"] = "https://cover.s3imgs.top" + r["vertical_image_url"] if r.get("vertical_image_url", "").startswith("/") else r.get("vertical_image_url", "")
            r["author"] = " / ".join(x["name"] for x in r.get("authors", []))
    elif site == "mangacopy":
        rows = n.copy_search(keyword, limit=30).get("results", {}).get("list", [])
        for r in rows:
            r.update(title=r.get("name"), url="https://www.mangacopy.com/comic/" + r["path_word"])
            r["author"] = " / ".join(x["name"] for x in r.get("author", []))
    elif site == "komiic":
        rows = n.komiic_search(keyword)
        for r in rows:
            r.update(url="https://komiic.com/comic/" + str(r["id"]), cover=r.get("imageUrl", ""))
    elif site in dm.SITES:
        rows = dm.search(site, keyword, limit=50)
    else:
        rows = {"baozimh": n.baozimh_search, "manhuazhijia": n.manhuazhijia_search,
                "tuku": n.tuku_search, "rumanhua": n.rum_search, "manhuagui": gui.gui_search}[site](keyword)
    return [dict(title=_text(r.get("title")) or "未命名漫画", detailUrl=r["url"], coverUrl=_text(r.get("cover")),
                 author=_text(r.get("author")), latestChapter=_text(r.get("latest")),
                 description=_text(r.get("description", r.get("desc"))), status=_text(r.get("status")),
                 **_search_title_evidence(r),
                 siteId=site, siteName=SOURCES[site][0]) for r in rows]


def hip_chapters(mid):
    rows, page, seen = [], 1, set()
    while page <= 200:
        data = n.hipmh_chapters(mid, page, 100).get("data", {})
        batch = data.get("items", [])
        fresh = [r for r in batch if r["hid"] not in seen]
        if not fresh:
            if len(rows) < int(data.get("total", len(rows))):
                raise RuntimeError("源站章节分页重复或缺失，请稍后重试")
            break
        rows.extend(fresh)
        seen.update(r["hid"] for r in fresh)
        if len(rows) >= int(data.get("total", len(rows))):
            break
        page += 1
    else:
        raise RuntimeError("章节数量超过分页上限")
    result = []
    for r in rows:
        left, right = r["hid"].split("-", 1)
        decoded = base64.b64decode(left + "=" * (-len(left) % 4)).decode()
        # List hid encodes m:{manga}-c:{chapter}; v2 accepts c:{chapter}.
        cid = decoded.rsplit("c:", 1)[-1]
        hid = base64.b64encode(f"c:{cid}".encode()).decode().rstrip("=") + "-" + right
        result.append({"name": r["title"], "url": "https://reader.hipmh.top/chapter/" + hid})
    return result


def hip_manga_id(url):
    """Resolve the same work from native URLs and original mobile deep links."""
    parsed = urlparse(url)
    fragment = parse_qs(parsed.fragment, keep_blank_values=True)
    query = parse_qs(parsed.query, keep_blank_values=True)
    supplied = fragment.get("mid", query.get("mid", []))
    if supplied:
        if len(supplied) != 1:
            raise ValueError("嬉皮作品标识重复")
        token = supplied[0]
    elif parsed.path.startswith("/works/"):
        token = parsed.path.rstrip("/").rsplit("/", 1)[-1].split("-", 1)[0]
    elif parsed.path.startswith("/manga/"):
        token = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    else:
        raise ValueError("嬉皮作品地址无效")
    if not re.fullmatch(r"[A-Za-z0-9_-]{3,64}", token):
        raise ValueError("嬉皮作品标识无效")
    try:
        decoded = base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True).decode("ascii")
        if not re.fullmatch(r"m:\d+", decoded):
            raise ValueError("不是作品标识")
    except (ValueError, UnicodeError) as exc:
        raise ValueError("嬉皮作品标识无效") from exc
    return token


def copy_chapters_all(slug):
    detail = n.copy_comic(slug).get("results", {})
    rows = []
    groups = detail.get("groups") or {"default": {}}
    if not isinstance(groups, dict):
        raise RuntimeError("拷贝漫画返回了未知的章节分组格式")
    for group in groups:
        offset, seen, expected = 0, set(), None
        while offset < 20000:
            data = n._json(f"{n.COPY_API}/api/v3/comic/{quote(slug)}/group/{quote(group)}/chapters?" + urlencode({"limit":100,"offset":offset,"platform":1}), n.COPY_HEADERS).get("results", {})
            batch, total = data.get("list"), data.get("total")
            if (not isinstance(batch, list) or type(total) is not int or not 0 <= total <= 20000
                    or len(batch) > 100):
                raise RuntimeError("拷贝漫画章节分页信息无效，无法确认完整目录")
            if expected is not None and total != expected:
                raise RuntimeError("拷贝漫画章节分页总数发生变化，请重试")
            expected = total
            for r in batch:
                if not isinstance(r, dict) or not _text(r.get("uuid")) or not _text(r.get("name")):
                    raise RuntimeError("拷贝漫画章节分页包含无效条目，无法确认完整目录")
                if r["uuid"] in seen:
                    raise RuntimeError("拷贝漫画章节分页重复，未取得完整目录")
                seen.add(r["uuid"])
                rows.append({"name": r["name"], "url": f"https://www.mangacopy.com/comic/{slug}/chapter/{r['uuid']}", "group": group})
            if len(seen) > expected or (not batch and len(seen) < expected):
                raise RuntimeError("拷贝漫画章节分页不完整")
            offset += len(batch)
            if len(seen) == expected:
                break
        else:
            raise RuntimeError("章节数量超过分页上限")
    return rows, detail.get("comic", {})


def details(site, url):
    validate_url(site, url)
    if site == "comicbox":
        return comicbox.details(url)
    if site == "komiic":
        return komiic.details(urlparse(url).path.rstrip("/").rsplit("/", 1)[-1])
    if site in APK_PROVIDERS:
        return APK_PROVIDERS[site].details(site, url)
    path = urlparse(url).path.strip("/").split("/")
    meta = {}
    if site == "hipmh":
        mid = hip_manga_id(url)
        rows = hip_chapters(mid)
        try:
            meta = hipmh_metadata.metadata(mid)
        except hipmh_metadata.MetadataError:
            # Metadata failure must not discard a usable chapter directory or
            # replace a known search/shelf title with unrelated source content.
            meta["unavailableReason"] = "嬉皮作品资料暂时无法获取，目录仍可阅读；换源前请确认书名"
    elif site == "mangacopy":
        try:
            rows, comic = copy_chapters_all(path[-1])
        except n.SourceBusinessError as exc:
            if exc.code != "210":
                raise
            # A public website is a distinct, ordinary source interface. Do
            # not change identity/permissions or conceal an unavailable result.
            return mangacopy_web.details(url)
        meta = {"title": comic.get("name", ""), "description": comic.get("brief", ""),
                "coverUrl": comic.get("cover", ""),
                "author": " / ".join(a["name"] for a in comic.get("author", []) if a.get("name")),
                "status": comic.get("status", {}).get("display", "") if isinstance(comic.get("status"), dict) else "",
                "catalogCompleteness": "complete"}
    elif site in dm.SITES:
        body, _, status = dm.Session().get(url, headers={"Referer": dm.SITES[site]["origin"] + "/"})
        if status >= 400:
            raise RuntimeError(f"源站返回 HTTP {status}")
        page = body.decode("utf-8", "replace")
        meta = detail_metadata(site, page, url)
        rows, notice = dm.parse_directory_html(site, page)
        if notice:
            meta["unavailableReason"] = notice
    else:
        # Reuse the same detail response for metadata and chapters. Search
        # remains one request per source and does not grow N+1 detail calls.
        origin = urlparse(url).scheme + "://" + urlparse(url).netloc + "/"
        page = gui._get(url) if site == "manhuagui" else n._page(url, origin)
        meta = detail_metadata(site, page, url)
        rows = {"baozimh": n.baozimh_chapters, "manhuazhijia": n.manhuazhijia_chapters,
                "tuku": n.tuku_chapters, "rumanhua": n.rum_chapters, "manhuagui": gui.gui_chapters}[site](url, page=page)
        if site == "manhuagui":
            rows.sort(key=lambda r: int(r["id"]))
        if site in {"baozimh", "manhuazhijia", "rumanhua"} and not (
                site == "rumanhua" and n._rum_order_direction(rows, page, url) == 1):
            # A source-confirmed Rum sequence already includes unnumbered
            # preludes and interleaved extras. Reclassifying only some of its
            # bare-number titles would move later chapters into earlier slots.
            rows = order_chapters(rows)
    chapters = [dict(id=r["url"], name=r.get("name") or r.get("title") or f"第{i+1}话", url=r["url"], order=i, group=r.get("group", "")) for i,r in enumerate(rows)]
    return {**dict.fromkeys(("title", "author", "description", "coverUrl", "status"), ""),
            **{field: _text(value) for field, value in meta.items()}, "chapters": chapters, "sourceUrl": url}


def images(site, url):
    validate_url(site, url)
    if site == "comicbox":
        return comicbox.images(url)
    if site in APK_PROVIDERS:
        return APK_PROVIDERS[site].images(site, url)
    path = urlparse(url).path.strip("/").split("/")
    if site == "hipmh":
        return n.hipmh_chapter_images(path[-1])
    if site == "mangacopy":
        try:
            return n.copy_chapter_images(path[1], path[-1])
        except n.SourceBusinessError as exc:
            if exc.code != "210":
                raise
            return mangacopy_web.images(url)
    if site == "komiic":
        return komiic.images(path[-1])
    if site in dm.SITES:
        return dm.chapter_images(url)[0]
    if site == "baozimh":
        q = parse_qs(urlparse(url).query)
        return n.baozimh_chapter_images(q["comic_id"][0], int(q["section_slot"][0]), int(q["chapter_slot"][0]))
    return {"manhuazhijia":n.manhuazhijia_chapter_images, "tuku":n.tuku_chapter_images,
            "rumanhua":n.rum_images, "manhuagui":gui.gui_chapter_images}[site](url)
