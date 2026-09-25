"""Public discovery metadata for the existing API/official-site readers.

The observed website lists remain authoritative: no remote JavaScript is run,
no chapter image/ticket endpoint is called, and new-title lists are not updates.
Small homepage sections are explicitly single-page snapshots, not full catalogs.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
import json
import re
import time
from urllib.parse import urljoin, urlsplit

from . import apk_official_catalogs, apk_sunday, hipmh_metadata, mangacopy_web
from .discovery_common import DiscoveryError, image_url, read_json, read_text, result, text
from .html_metadata import Element, image_of, parse_html, text_of

IMAGE_DOMAINS = ("cover.s3imgs.top", "public.komiic.com", "mangafunb.fun",
                 "cdn-img.www.sunday-webry.com", "cdn-scissors.gigaviewer.com",
                 "web.hycdn.cn", "res01.hycdn.cn", "uploads.namicomi.com")
_NAMES = {"hipmh": "嬉皮漫画", "komiic": "Komiic", "mangacopy": "拷贝漫画",
          "sundaywebry": "Sunday Webry（日文）", "terrahistoricus": "泰拉记事社", "namicomi": "NamiComi 漫画"}
_ORIGINS = {"hipmh": "https://m.hipmh.com", "komiic": "https://komiic.com",
            "mangacopy": "https://www.mangacopy.com", "sundaywebry": "https://www.sunday-webry.com",
            "terrahistoricus": "https://comic.hypergryph.com", "namicomi": "https://namicomi.com"}
_COVERS = {"hipmh": ("cover.s3imgs.top",), "komiic": ("public.komiic.com",),
           "mangacopy": ("mangafunb.fun",), "sundaywebry": apk_sunday.IMAGE_DOMAINS,
           "terrahistoricus": ("web.hycdn.cn", "res01.hycdn.cn"), "namicomi": ("uploads.namicomi.com",)}
_COPY_PERIODS = {f"{gender}-{period}": f"{label} · {period_label}" for gender, label in
                 (("male", "男频"), ("female", "女频")) for period, period_label in
                 (("day", "日榜"), ("week", "周榜"), ("month", "月榜"), ("total", "总榜"))}
_EMPTY = Element("empty")


def _mode(kind, label, periods=(), maximum=None):
    value = {"kind": kind, "label": label, "periods": [{"id": key, "label": name} for key, name in periods]}
    if maximum is not None:
        value["maxPage"] = maximum
    return value


def sources():
    modes = {
        "hipmh": [_mode("popular", "人气榜"), _mode("latest", "首页近期更新", maximum=1)],
        "komiic": [_mode("popular", "热门漫画", (("month", "本月热门"), ("total", "总热门"))),
                   _mode("latest", "最近更新")],
        "mangacopy": [_mode("popular", "排行榜", _COPY_PERIODS.items(), 1), _mode("latest", "最近更新")],
        "sundaywebry": [_mode("popular", "官网人气榜", maximum=1), _mode("latest", "今日与昨日更新", maximum=1)],
        "terrahistoricus": [_mode("latest", "官网近期更新", maximum=1)],
        "namicomi": [_mode("popular", "热门作品", maximum=1), _mode("latest", "官网近期更新", maximum=1)],
    }
    return [{"siteId": site, "siteName": _NAMES[site], "modes": values, "coverLookup": False}
            for site, values in modes.items()]


def _selection(site, kind, period, page):
    source = next((source for source in sources() if source["siteId"] == site), None)
    mode = next((mode for mode in (source or {}).get("modes", []) if mode["kind"] == kind), None)
    if not mode:
        raise ValueError("此来源没有已接入的发现类型")
    if period not in ([value["id"] for value in mode["periods"]] or [""]):
        raise ValueError("此来源不支持所选榜单周期")
    if type(page) is not int or not 1 <= page <= mode.get("maxPage", 1000):
        raise ValueError("此来源不支持所选页码")


def _tree(source):
    root = parse_html(source)
    if re.search(r"Just a moment|Attention Required|安全验证|人机验证", text_of(root.first("title")), re.I):
        raise DiscoveryError("来源要求访问验证，请稍后重试")
    return root


def _required(value, label):
    if value is None:
        raise DiscoveryError(label + "结构发生变化")
    return value


def _book(site, raw):
    if not isinstance(raw, str) or not raw or len(raw) > 4096 or any(ord(c) <= 32 for c in raw) or "\\" in raw:
        raise DiscoveryError("来源缺少有效作品地址")
    url = urljoin(_ORIGINS[site], raw)
    p = urlsplit(url)
    if (p.scheme != "https" or p.hostname != urlsplit(_ORIGINS[site]).hostname or p.username or p.password
            or p.port is not None or p.query or p.fragment):
        raise DiscoveryError("来源返回了不匹配的作品地址")
    try:
        if site == "hipmh":
            # The public /works URL contains the same m:ID token used by the
            # existing reader adapter, followed by its human-readable slug.
            match = re.fullmatch(r"/works/([A-Za-z0-9]+)-[A-Za-z0-9_-]+", p.path)
            if not match:
                raise ValueError
            hipmh_metadata._work_id(match[1])
            return "https://reader.hipmh.top/manga/" + match[1]
        if site == "mangacopy":
            return mangacopy_web._url(url)[0]
        if site == "sundaywebry":
            return apk_sunday._episode_url(apk_sunday._url_id(site, url))
        if site in {"namicomi", "terrahistoricus"}:
            apk_official_catalogs._source_ids(site, url)
        elif not re.fullmatch(r"/comic/[1-9][0-9]{0,19}", p.path):
            raise ValueError
    except ValueError as exc:
        raise DiscoveryError("来源作品地址格式发生变化") from exc
    return url


def _item(site, title, href, cover, **values):
    title = text(title, 300)
    if not title:
        raise DiscoveryError("来源作品名称缺失")
    return {"title": title, "detailUrl": _book(site, href),
            "coverUrl": image_url(cover, _ORIGINS[site], _COVERS[site]), **values}


def _items(values):
    if not values:
        raise DiscoveryError("来源未返回可识别的漫画列表")
    if len({value["detailUrl"] for value in values}) != len(values):
        raise DiscoveryError("来源返回了重复作品，列表不完整")
    return values


def _html(site, url, deadline):
    return read_text(url, hosts=(urlsplit(_ORIGINS[site]).hostname,), deadline=deadline)


def _hip(source, kind, page):
    root = _tree(source)
    if kind == "latest":
        scope = _required(root.first(cls="recent-updates-section"), "嬉皮近期更新")
        if text_of(scope.first("h2")) != "近期更新":
            raise DiscoveryError("嬉皮更新栏目已变化")
        rows = list(scope.all("a"))
    else:
        if text_of(root.first("h1")) != "人氣榜":
            raise DiscoveryError("嬉皮榜单栏目已变化")
        active = root.first(cls="pagination-link-active")
        if text_of(active) != str(page):
            raise DiscoveryError("嬉皮未返回所选榜单页")
        rows = list(root.all("a", cls="manga-card-link"))
    items = []
    for row in rows:
        values = {}
        if kind == "popular":
            rank = text_of(row.first(cls="rank-badge"))
            if not rank.isdecimal():
                raise DiscoveryError("嬉皮榜单缺少名次")
            values["rank"] = int(rank)
        else:
            values["updatedAtText"] = text_of(row.first(cls="recent-updates-time-badge"))
        items.append(_item("hipmh", row.attrs.get("aria-label"), row.attrs.get("href"),
                           image_of(row, _ORIGINS["hipmh"]), **values))
    more = False
    if kind == "popular":
        following = _required(root.first("a", cls="pagination-next"), "嬉皮榜单分页")
        if following.attrs.get("aria-disabled") == "false":
            if following.attrs.get("href") != f"/popularity?page={page + 1}":
                raise DiscoveryError("嬉皮榜单分页没有前进")
            more = True
    return _items(items), more


_KOMIIC_FIELDS = "id title status imageUrl authors { name } dateUpdated monthViews views lastBookUpdate lastChapterUpdate"


def _komiic(kind, period, page, deadline):
    field = "hotComics" if kind == "popular" else "recentUpdate"
    order = ("MONTH_VIEWS" if period == "month" else "VIEWS") if kind == "popular" else "DATE_UPDATED"
    body = {"query": f"query {field}($pagination: Pagination!) {{ {field}(pagination: $pagination) {{ {_KOMIIC_FIELDS} }} }}",
            "variables": {"pagination": {"limit": 20, "offset": (page - 1) * 20, "orderBy": order, "asc": True}}}
    payload = read_json(_ORIGINS["komiic"] + "/api/query", hosts=("komiic.com",), deadline=deadline,
                        headers={"Origin": _ORIGINS["komiic"], "Referer": _ORIGINS["komiic"] + "/",
                                 "Content-Type": "application/json"}, data=json.dumps(body).encode())
    if not isinstance(payload, dict) or payload.get("errors") or not isinstance(payload.get("data"), dict):
        raise DiscoveryError("Komiic 未返回完整榜单数据")
    rows = payload["data"].get(field)
    if not isinstance(rows, list) or len(rows) > 20:
        raise DiscoveryError("Komiic 发现数据格式发生变化")
    values = []
    for row in rows:
        if not isinstance(row, dict) or not re.fullmatch(r"[1-9][0-9]{0,19}", str(row.get("id", ""))):
            raise DiscoveryError("Komiic 作品编号无效")
        chapter, book = text(row.get("lastChapterUpdate")), text(row.get("lastBookUpdate"))
        latest = " / ".join(value for value in (f"第 {chapter} 话" if chapter else "", f"第 {book} 卷" if book else "") if value)
        authors = row.get("authors")
        values.append(_item("komiic", row.get("title"), "/comic/" + str(row["id"]), row.get("imageUrl"),
                            author=" / ".join(text(author.get("name")) for author in authors if isinstance(author, dict))
                            if isinstance(authors, list) else "", latestChapter=latest,
                            updatedAtText=text(row.get("dateUpdated")),
                            status={"ONGOING": "连载中", "END": "已完结"}.get(text(row.get("status")), "")))
    # Exactly matches the public client's observed limit/offset end condition.
    return (_items(values) if values else []), len(rows) == 20


def _copy(source, kind, period, page):
    root = _tree(source)
    items = []
    if kind == "popular":
        scope = _required(root.first(cls="ranking-box"), "拷贝榜单")
        selected = (root.first(cls="rankingTime") or _EMPTY).first("a", cls="active")
        gender, table = period.split("-")
        if (selected or _EMPTY).attrs.get("href") != f"/rank?type={gender}&table={table}":
            raise DiscoveryError("拷贝未返回所选榜单")
        for row in scope.all(cls="ranking-all-box"):
            title = _required(row.first(cls="threeLines"), "拷贝榜单作品标题")
            link = _required(row.first("a"), "拷贝榜单作品地址")
            rank = text_of(row.first(cls="ranking-all-icon"))
            if not rank.isdecimal():
                raise DiscoveryError("拷贝榜单缺少名次")
            authors = [text_of(author) for author in row.all("a") if author.attrs.get("href", "").startswith("/author/")]
            items.append(_item("mangacopy", text_of(title), link.attrs.get("href"), image_of(row, _ORIGINS["mangacopy"]),
                               author=" / ".join(authors), rank=int(rank)))
        return _items(items), False
    scope = _required(root.first(cls="exemptComic-box"), "拷贝更新列表")
    pagination = _required(root.first("ul", cls="page-all"), "拷贝更新分页")
    if (pagination.attrs.get("url") != "/comics?ordering=-datetime_updated"
            or pagination.attrs.get("limit") != "50"
            or text_of(pagination.first("li", cls="active")) != str(page)):
        raise DiscoveryError("拷贝未返回所选更新排序和页码")
    literal = scope.attrs.get("list", "")
    if not literal or len(literal) > 400000:
        raise DiscoveryError("拷贝更新数据过大或缺失")
    try:
        # The observed server template writes a Python literal into an HTML
        # attribute. Only bounded literal data is parsed; code is never run.
        rows = ast.literal_eval(literal)
    except (ValueError, SyntaxError, RecursionError, MemoryError):
        raise DiscoveryError("拷贝更新数据格式发生变化") from None
    total = scope.attrs.get("total", "")
    if not isinstance(rows, list) or len(rows) > 50 or not total.isdecimal():
        raise DiscoveryError("拷贝更新分页数据缺失")
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("path_word"), str):
            raise DiscoveryError("拷贝更新作品信息缺失")
        authors = row.get("author")
        items.append(_item("mangacopy", row.get("name"), "/comic/" + row["path_word"], row.get("cover"),
                           author=" / ".join(text(author.get("name")) for author in authors if isinstance(author, dict))
                           if isinstance(authors, list) else "",
                           status={0: "连载中", 1: "已完结", 2: "短篇"}.get(row.get("status"), "")
                           if type(row.get("status")) is int else ""))
    more = (page - 1) * 50 + len(rows) < int(total)
    if more:
        expected = f"/comics?ordering=-datetime_updated&offset={page * 50}&limit=50"
        if not any(a.attrs.get("href") == expected for a in root.all("a")):
            raise DiscoveryError("拷贝更新分页没有前进")
    return (_items(items) if items else []), more


def _sunday(source, kind):
    root = _tree(source)
    scope = _required(root.first("section", cls="top-ranking" if kind == "popular" else "top-today"), "Sunday Webry 发现栏目")
    if text_of(scope.first("h2")) != ("RANKING" if kind == "popular" else "TODAY"):
        raise DiscoveryError("Sunday Webry 发现栏目已变化")
    rows = list(scope.all("li")) if kind == "popular" else list(scope.all("li", cls="test-updated-episode"))
    values = []
    for row in rows:
        link = _required(row.first("a"), "Sunday Webry 作品地址")
        values.append(_item("sundaywebry", text_of(row.first("h4")), link.attrs.get("href"),
                            image_of(row, _ORIGINS["sundaywebry"]), author=text_of(row.first(cls="author")),
                            latestChapter=text_of(row.first(cls="episode-title"))))
    return _items(values)


def _nami_scope(root, heading):
    headings = [node for node in root.all() if node.tag in {"h1", "h2"} and text_of(node) == heading]
    if len(headings) != 1:
        raise DiscoveryError("NamiComi 发现栏目已变化")
    scope = headings[0]
    for _ in range(5):
        if any("/title/" in link.attrs.get("href", "") for link in scope.all("a")):
            return scope
        scope = _required(scope.parent, "NamiComi 发现栏目")
    raise DiscoveryError("NamiComi 发现栏目缺少作品")


def _nami(source, kind):
    root = _tree(source)
    scope = _nami_scope(root, "Hot Titles" if kind == "popular" else "Latest Updates")
    values = []
    if kind == "popular":
        for row in scope.all("a"):
            if "/title/" not in row.attrs.get("href", "") or row.first("img") is None:
                continue
            values.append(_item("namicomi", text_of(row.first("p")), row.attrs.get("href"),
                                image_of(row, _ORIGINS["namicomi"])))
    else:
        for row in scope.all("li"):
            links = [link for link in row.all("a") if "/title/" in link.attrs.get("href", "")]
            if not links:
                continue
            # Gated chapters render the same metadata wrapper as a div. The
            # list may show their title; reading permission stays with reader.
            chapter = _required(row.first(cls="chapter-card__wrap"), "NamiComi 最新章节")
            stamp = next((span.attrs["title"] for span in chapter.all("span") if "title" in span.attrs), "")
            values.append(_item("namicomi", text_of(row.first("h3")), links[0].attrs.get("href"),
                                image_of(links[0], _ORIGINS["namicomi"]),
                                latestChapter=text_of(chapter.first("h3")), updatedAtText=stamp))
    return _items(values)


def _terra(payload):
    if (not isinstance(payload, dict) or payload.get("code") != 0 or
            not isinstance(payload.get("data"), list) or len(payload["data"]) > 100):
        raise DiscoveryError("泰拉记事社近期更新格式发生变化")
    values, seen = [], set()
    previous = None
    for row in payload["data"]:
        if (not isinstance(row, dict) or not re.fullmatch(r"[0-9]{1,20}", str(row.get("comicCid", "")))
                or type(row.get("updateTime")) is not int or not 0 < row["updateTime"] < 253402300800):
            raise DiscoveryError("泰拉记事社更新作品信息缺失")
        stamp = row["updateTime"]
        if previous is not None and stamp > previous:
            raise DiscoveryError("泰拉记事社更新顺序发生变化")
        previous = stamp
        cid = str(row["comicCid"])
        if cid in seen:
            continue
        seen.add(cid)
        values.append(_item("terrahistoricus", row.get("title"), f"/terra-historicus/comic/{cid}", row.get("coverUrl"),
                            latestChapter=text(row.get("episodeShortTitle")), description=text(row.get("subtitle")),
                            updatedAtText=datetime.fromtimestamp(stamp, timezone.utc).date().isoformat()))
    return _items(values)


def fetch(site, kind, period, page):
    _selection(site, kind, period, page)
    deadline, origin = time.monotonic() + 25, _ORIGINS[site]
    note, more = "", False
    if site == "hipmh":
        url = origin + ("/popularity" + (f"?page={page}" if page > 1 else "") if kind == "popular" else "/")
        values, more = _hip(_html(site, url, deadline), kind, page)
        label = "人气榜" if kind == "popular" else "首页近期更新"
        if kind == "latest":
            note = "仅展示官网首页的近期更新批次，保留原站时间；不代表全站更新目录。"
    elif site == "komiic":
        url = origin + ("/hot" if kind == "popular" else "/updates")
        values, more = _komiic(kind, period, page, deadline)
        label = ("本月热门" if period == "month" else "总热门") if kind == "popular" else "最近更新"
        note = "按源站顺序每页显示20部；热门顺序来自源站浏览量排序。"
    elif site == "mangacopy":
        if kind == "popular":
            gender, table = period.split("-")
            url, label = origin + f"/rank?type={gender}&table={table}", _COPY_PERIODS[period]
        else:
            url, label = origin + f"/comics?ordering=-datetime_updated&offset={(page - 1) * 50}&limit=50", "最近更新"
        values, more = _copy(_html(site, url, deadline), kind, period, page)
        note = "列表和封面可用；拷贝官网可能不向匿名访问返回完整章节目录。"
    elif site == "sundaywebry":
        url = origin + "/"
        values = _sunday(_html(site, url, deadline), kind)
        label = "官网人气榜" if kind == "popular" else "今日与昨日更新"
        note = "仅展示官网首页当前栏目；日文作品，部分章节需在源站授权。"
    elif site == "namicomi":
        url = origin + ("/en" if kind == "popular" else "/en/updates/latest")
        values = _nami(_html(site, url, deadline), kind)
        label = "热门作品" if kind == "popular" else "官网近期更新"
        note = "仅展示官网当前公开批次；热门作品保留 Hot Titles 顺序，未指定统计周期。" if kind == "popular" else "仅展示官网更新首批，每部作品保留最新一章；包含多种语言。"
    else:
        url = origin + "/terra-historicus"
        values = _terra(read_json(origin + "/api/recentUpdate?topicKey=terra-historicus",
                                 hosts=("comic.hypergryph.com",), deadline=deadline))
        label = "官网近期更新"
        note = "官网首页更新批次；同一作品多次更新合并，展示最新章节及原站更新缩略图。"
    if more and page == 1000:
        raise DiscoveryError("来源列表超过当前可浏览页数上限，请前往源站继续查看")
    return result(site, _NAMES[site], kind, period, page, values, has_more=more, source_url=url, label=label, note=note)
