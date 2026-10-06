"""Public discovery HTML verified 2026-09-22; no scripts or chapters fetched.

Each mode has an observed URL and its own list scope. Homepage excerpts and
the first ten linked Mangabz pages are explicitly labelled as limited ranges.
Rumanhua's published HTTP origin is retained; TLS verification is never disabled.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

from . import comicbox
from .discovery_common import DiscoveryError, image_url, read_text, result, text
from .html_metadata import Element, image_of, known_status, parse_html, text_of

_SITES = {
    "dm5": ("动漫屋", "https://www.dm5.com"),
    "mangabz": ("漫画巴士", "https://www.mangabz.com"),
    "baozimh": ("包子漫画", "https://www.baozimh.com"),
    "manhuazhijia": ("漫画之家", "https://www.manhuazhijia.cc"),
    "tuku": ("图库漫画", "https://www.tuku.cc"),
    "rumanhua": ("如漫画", "http://rumanhua2.com"),
    "dumanwu": ("读漫屋", "http://dumanwu1.com"),
    "comicbox": ("歪歪漫画", "https://www.comicbox.xyz"),
}
_COVERS = {
    "dm5": ("cdndm5.com",), "mangabz": ("mangabz.com",), "baozimh": ("baozimh.com",),
    "manhuazhijia": ("bgm.tv", "baozimh.com"), "tuku": ("tuku.cc",),
    "rumanhua": ("ecombdimg.com",), "dumanwu": ("ecombdimg.com",), "comicbox": comicbox.IMAGE_HOSTS,
}
IMAGE_DOMAINS = tuple(dict.fromkeys(host for hosts in _COVERS.values() for host in hosts))
_BOOKS = {
    "dm5": r"/manhua-(?!(?:new|rank|list)(?:/|$))[A-Za-z0-9_-]+/?",
    "mangabz": r"/[1-9][0-9]{0,11}bz/",
    "baozimh": r"/comic/[A-Za-z0-9_-]+",
    "manhuazhijia": r"/comic/[A-Za-z0-9_-]+",
    "tuku": r"/manga-[1-9][0-9]{0,11}/",
    "rumanhua": r"/[A-Za-z0-9]{7,20}/",
    "dumanwu": r"/[A-Za-z0-9]{7,20}/",
    "comicbox": r"/book/[1-9][0-9]{0,11}",
}
_PERIODS = {"week": "周", "month": "月", "total": "总"}
_EMPTY = Element("empty")


def sources():
    labels = {
        "dm5": ("人气榜", "今日更新"), "mangabz": ("人气排序", "最近更新"),
        "baozimh": ("首页热门漫画", "首页最近更新"),
        "manhuazhijia": ("人气排行榜", "最近更新"),
        "tuku": ("人气榜", "首页最近更新"), "rumanhua": ("人气榜", "最近更新"),
        "dumanwu": ("人气榜", "最近更新"),
        "comicbox": ("人气排行", "首页最近更新"),
    }
    return [{"siteId": site, "siteName": name, "coverLookup": False, "modes": [
        {"kind": kind, "label": labels[site][index], "maxPage": 10 if site == "mangabz" else 1,
         "periods": [{"id": key, "label": value + "榜"} for key, value in _PERIODS.items()]
                    if site == "dm5" and kind == "popular" else []}
        for index, kind in enumerate(("popular", "latest"))
    ]} for site, (name, _) in _SITES.items()]


def _selection(site, kind, period, page):
    if not isinstance(site, str) or site not in _SITES:
        raise ValueError("未启用的 HTML 发现来源")
    if not isinstance(kind, str) or kind not in {"popular", "latest"}:
        raise ValueError("不支持的发现类型")
    if not isinstance(period, str) or (period not in _PERIODS if site == "dm5" and kind == "popular" else period != ""):
        raise ValueError("该来源不支持此榜单周期")
    if type(page) is not int or not 1 <= page <= (10 if site == "mangabz" else 1):
        raise ValueError("页码超出该来源已验证的列表范围")


def _url(site, kind, page):
    paths = {
        "dm5": ("/manhua-rank/?t=4", "/manhua-new/"),
        "baozimh": ("/", "/"), "manhuazhijia": ("/top", "/update"),
        "tuku": ("/rank/", "/"), "rumanhua": ("/rank/2", "/rank/5"),
        "dumanwu": ("/rank/2", "/rank/5"),
        "comicbox": ("/rank", "/"),
    }
    if site == "mangabz":
        path = "/manga-list" + ("-0-0-2" if kind == "latest" else "") + (f"-p{page}" if page > 1 else "") + "/"
    else:
        path = paths[site][kind == "latest"]
    return _SITES[site][1] + path


def _tree(source):
    root = parse_html(source)
    if re.search(r"Just a moment|Attention Required|安全验证|人机验证|访问验证", text_of(root.first("title")), re.I) or "cf-chl-" in source:
        raise DiscoveryError("来源要求访问验证，暂时无法读取列表")
    return root


def _one(nodes, label):
    nodes = list(nodes)
    if len(nodes) != 1:
        raise DiscoveryError(f"来源的{label}结构发生变化")
    return nodes[0]


def _direct(node, tag=None, cls=None):
    return (child for child in node.children if isinstance(child, Element)
            and (tag is None or child.tag == tag)
            and (cls is None or cls in child.attrs.get("class", "").split()))


def _book(site, value):
    if (not isinstance(value, str) or not value or len(value) > 1000 or any(ord(c) <= 32 for c in value)
            or "\\" in value or re.search(r"(?:^|/)\.{1,2}(?:/|$)", value)):
        raise DiscoveryError("来源列表缺少有效作品地址")
    origin = _SITES[site][1]
    try:
        url = urljoin(origin + "/", value)
        p = urlsplit(url)
        if (p.scheme not in {"https", "http"} or p.netloc != urlsplit(origin).netloc
                or p.query or p.fragment or not re.fullmatch(_BOOKS[site], p.path)):
            raise ValueError
    except ValueError:
        raise DiscoveryError("来源作品地址不属于所选源的作品页") from None
    path = p.path.rstrip("/") + "/" if site == "dm5" else p.path
    return origin + path


def _row(site, title, href, cover_node=None, **extra):
    title = text(title, 500)
    if not title:
        raise DiscoveryError("来源列表条目缺少作品标题")
    cover = image_url(image_of(cover_node, _SITES[site][1]), _SITES[site][1], _COVERS[site])
    return {"siteId": site, "siteName": _SITES[site][0], "title": title, "detailUrl": _book(site, href),
            "coverUrl": cover, "author": "", "latestChapter": "", "description": "", "status": "", **extra}


def _rank(value, expected):
    if not re.fullmatch(r"[0-9]{1,4}", value) or int(value) != expected:
        raise DiscoveryError("来源榜单名次缺失或顺序发生变化")
    return int(value)


def _dm5(root, kind, period):
    scope = _one(root.all("section", cls="js_top_container" if kind == "popular" else "js_update_mh_list"), "主列表")
    heading = text_of(scope.first("h1"))
    if heading != ("人气榜" if kind == "popular" else "最近更新"):
        raise DiscoveryError("来源榜单类型发生变化")
    if kind == "popular":
        tabs = [text_of(n) for n in (scope.first(cls="top-type") or _EMPTY).all("a")]
        groups = list(scope.all("ul", cls="top-cat"))
        if tabs != list(_PERIODS.values()) or len(groups) != len(tabs):
            raise DiscoveryError("来源榜单周期与列表无法对应")
        group = groups[tabs.index(_PERIODS[period])]
    else:
        if text_of(scope.first(ident="daykeylabel")) != "今天":
            raise DiscoveryError("来源更新日期范围发生变化")
        group = _one(scope.all("ul", cls="mh-list"), "今日更新列表")
    rows = []
    for li in _direct(group, "li"):
        card = _one(_direct(li, cls="mh-item"), "作品卡片")
        info = _one(_direct(card, cls="mh-item-detali"), "作品信息")
        link = (info.first("h2", cls="title") or _EMPTY).first("a") or _EMPTY
        chapter = info.first(cls="chapter") or _EMPTY
        extra = {"latestChapter": text_of(chapter.first("a")), "status": known_status(text_of(chapter))}
        if kind == "popular":
            extra.update(rank=_rank(text_of(card.first(cls="num")), len(rows) + 1),
                         author=" / ".join(text_of(a) for a in (info.first(cls="zl") or _EMPTY).all("a")),
                         description=text_of(info.first(cls="desc")))
        else:
            extra["updatedAtText"] = text_of(info.first(cls="zl"))
        rows.append(_row("dm5", text_of(link), link.attrs.get("href"), card.first(cls="mh-cover"), **extra))
    return rows


def _mangabz(root, kind, page):
    sort_links = [node for node in root.all("a", cls="active") if text_of(node) in {"人氣", "更新時間"}]
    if len(sort_links) != 1 or text_of(sort_links[0]) != ("人氣" if kind == "popular" else "更新時間"):
        raise DiscoveryError("来源列表排序与请求不一致")
    scope = _one(root.all("ul", cls="mh-list"), "主列表")
    rows = []
    for li in _direct(scope, "li"):
        card = _one(_direct(li, cls="mh-item"), "作品卡片")
        info = card.first(cls="mh-item-detali") or _EMPTY
        link = (info.first("h2", cls="title") or _EMPTY).first("a") or _EMPTY
        rows.append(_row("mangabz", text_of(link), link.attrs.get("href"), card.first(cls="mh-cover"),
                         latestChapter=text_of((info.first(cls="chapter") or _EMPTY).first("a")),
                         status=known_status(text_of(info.first(cls="chapter")))))
    links = [node for node in root.all("a") if "data-index" in node.attrs]
    current = [node for node in links if "active" in node.attrs.get("class", "").split()]
    if (len(current) != 1 or current[0].attrs["data-index"] != str(page)
            or urljoin(_SITES["mangabz"][1], current[0].attrs.get("href", "")) != _url("mangabz", kind, page)):
        raise DiscoveryError("来源返回了其他页的列表")
    arrows = [node for node in links if text_of(node) == ">"]
    if len(arrows) > 1 or any(node.attrs.get("data-index") != str(page + 1) for node in arrows):
        raise DiscoveryError("来源分页标记发生变化")
    # A numbered next-page link is equally explicit evidence. Losing only
    # the decorative arrow must not incorrectly mark the list as finished.
    following = [node for node in links if node.attrs.get("data-index") == str(page + 1)]
    if any(urljoin(_SITES["mangabz"][1], node.attrs.get("href", "")) != _url("mangabz", kind, page + 1)
           for node in following):
        raise DiscoveryError("来源下一页地址与列表不匹配")
    return rows, bool(following) and page < 10


def _baozimh(root, kind):
    title = "熱門漫畫" if kind == "popular" else "最近更新"
    heading = _one((node for node in root.all(cls="catalog-title") if text_of(node) == title), "首页列表标题")
    scope = heading.parent.parent
    if "index-recommend-items" not in scope.attrs.get("class", "").split():
        raise DiscoveryError("来源首页列表范围发生变化")
    group = _one(_direct(scope, cls="pure-g"), "首页作品列表")
    rows = []
    for card in _direct(group, cls="comics-card"):
        link = card.first("a", cls="comics-card__poster") or _EMPTY
        extra = {"latestChapter": re.sub(r"^更新至\s*", "", text_of(card.first("small", cls="tags")))}
        if kind == "popular":
            extra["rank"] = _rank(text_of(card.first(cls="comics-card__badge")), len(rows) + 1)
        rows.append(_row("baozimh", text_of(card.first("h3")), link.attrs.get("href"), link, **extra))
    return rows


def _manhuazhijia(root, kind):
    expected = "漫画排行榜" if kind == "popular" else "最近更新"
    if expected not in text_of(root.first("title")):
        raise DiscoveryError("来源列表类型发生变化")
    rows = []
    if kind == "popular":
        scope = _one((node for node in root.all("div", cls="container") if list(_direct(node, "a", "rank-item"))), "排行榜主体")
        for card in _direct(scope, "a", "rank-item"):
            info = card.first(cls="rank-info") or _EMPTY
            paragraphs = list(_direct(info, "p"))
            if len(paragraphs) != 2:
                raise DiscoveryError("来源作品信息发生变化")
            rows.append(_row("manhuazhijia", text_of(info.first("h3")), card.attrs.get("href"), card.first(cls="rank-cover"),
                             rank=_rank(text_of(card.first(cls="rank-num")), len(rows) + 1),
                             author=text_of(paragraphs[0]), latestChapter=re.sub(r"^更新至\s*", "", text_of(paragraphs[1]))))
    else:
        scope = _one(root.all(cls="update-grid"), "更新列表主体")
        for card in _direct(scope, "a", "update-card"):
            rows.append(_row("manhuazhijia", text_of(card.first(cls="update-title")), card.attrs.get("href"), card.first(cls="update-cover"),
                             latestChapter=re.sub(r"^更新至\s*", "", text_of(card.first(cls="update-chap")))))
    return rows


def _tuku(root, kind):
    rows = []
    if kind == "popular":
        if "人气榜" not in text_of(root.first("title")):
            raise DiscoveryError("来源榜单类型发生变化")
        heading = _one((node for node in root.all("h3", cls="title") if node.attrs.get("data-name") == "人气榜"), "榜单标题")
        scope = heading.parent.parent
        cards = list(scope.all(cls="top-card"))
    else:
        scope = _one(root.all(cls="home-update-wrap"), "首页更新列表")
        if text_of(scope.first("h3")) != "最近更新":
            raise DiscoveryError("来源首页列表范围发生变化")
        cards = list(scope.all(cls="swiper-card-item"))
    for card in cards:
        link = card.first("a", cls="card-item-title") or _EMPTY
        chapter = card.first(cls="new-tip") or _EMPTY
        if kind == "popular":
            chapter_link = chapter.first("a")
            extra = {"author": " / ".join(text_of(a) for a in card.all("a") if a.attrs.get("href", "").startswith("/search?")),
                     "description": text_of(card.first(cls="multi-ellipsis"))}
            # The source marks only positions 01–09. Later cards have no
            # printed rank; retain their order without inventing numbers.
            printed_rank = card.first("p", cls="rank")
            if printed_rank is not None or len(rows) < 9:
                extra["rank"] = _rank(text_of(printed_rank), len(rows) + 1)
        else:
            chapter_link = (card.first(cls="card-item-text-wrap") or _EMPTY).first("a")
            extra = {"updatedAtText": text_of(card.first(cls="card-item-chapter"))}
        rows.append(_row("tuku", text_of(link), link.attrs.get("href"), card.first("img"),
                         latestChapter=text_of(chapter_link), **extra))
    return rows


def _rumanhua(root, kind, site):
    expected = "人气榜" if kind == "popular" else "最近更新"
    if expected not in text_of(root.first("title")):
        raise DiscoveryError("来源列表类型发生变化")
    scope = _one(root.all(cls="rank-list"), "主列表")
    rows = []
    for card in _direct(scope, "li"):
        link = (card.first(cls="simple-info") or _EMPTY).first("a") or _EMPTY
        paragraphs = list(_direct(link, "p"))
        if len(paragraphs) != 2:
            raise DiscoveryError("来源作品信息发生变化")
        extra = {"author": text_of(paragraphs[0]), "latestChapter": re.sub(r"^最新[：:]\s*", "", text_of(paragraphs[1])),
                 "description": text_of(card.first(cls="cartoon-introduction"))}
        if kind == "popular":
            extra["rank"] = _rank(text_of(card.first(cls="rank-default")), len(rows) + 1)
        rows.append(_row(site, text_of(link.first("h2")), link.attrs.get("href"), card.first(cls="poster-box"), **extra))
    return rows


def _comicbox(root, kind):
    version = comicbox.cache_key(root)
    if kind == "popular":
        scope = _one(root.all("main", cls="sp-rank-page"), "榜单主体")
        if text_of(scope.first("h1")) != "人氣排行":
            raise DiscoveryError("来源榜单类型发生变化")
        group = _one(scope.all(cls="sp-rank-grid"), "榜单作品列表")
        card_class, title_class, cover_class = "sp-rank-card", "sp-rank-card-title", "sp-rank-card-cover"
    else:
        heading = _one((node for node in root.all("h2", cls="sp-section-title") if text_of(node) == "最近更新"), "首页更新标题")
        scope = heading.parent.parent
        if "sp-section" not in scope.attrs.get("class", "").split():
            raise DiscoveryError("来源首页列表范围发生变化")
        group = _one(_direct(scope, cls="sp-grid"), "首页作品列表")
        card_class, title_class, cover_class = "sp-card", "sp-card-title", "sp-card-cover"
    rows = []
    for card in group.all("a", cls=card_class):
        if comicbox._is_ad(card, group):
            continue
        href = card.attrs.get("href")
        url = _book("comicbox", href)
        cover = comicbox._cover(card.first(cls=cover_class), version, url.rsplit("/", 1)[-1])
        extra = {"coverUrl": image_url(cover, _SITES["comicbox"][1], _COVERS["comicbox"])}
        if kind == "popular":
            extra["rank"] = _rank(text_of(card.first(cls="sp-rank-card-number")), len(rows) + 1)
        rows.append(_row("comicbox", text_of(card.first(cls=title_class)), href, **extra))
    return rows


def fetch(site, kind, period, page):
    _selection(site, kind, period, page)
    url = _url(site, kind, page)
    root = _tree(read_text(url, hosts=(urlsplit(_SITES[site][1]).hostname,)))
    more = False
    if site == "dm5":
        items = _dm5(root, kind, period)
    elif site == "mangabz":
        items, more = _mangabz(root, kind, page)
    elif site in {"rumanhua", "dumanwu"}:
        items = _rumanhua(root, kind, site)
    else:
        items = {"baozimh": _baozimh, "manhuazhijia": _manhuazhijia,
                 "tuku": _tuku, "comicbox": _comicbox}[site](root, kind)
    if not items:
        raise DiscoveryError("来源未返回可识别的列表，不能确认当前内容为空")
    mode = next(m for s in sources() if s["siteId"] == site for m in s["modes"] if m["kind"] == kind)
    label = mode["label"] + ("（" + _PERIODS[period] + "榜）" if period else "")
    if site == "mangabz":
        note = "按源站顺序开放首页明确链接的前10页；此范围之外的作品请在源站查看。"
    elif site == "dm5" and kind == "latest":
        note = f"显示源站今日更新区的{len(items)}部作品；其他日期请在源站查看。"
    elif site == "baozimh" or (site in {"tuku", "comicbox"} and kind == "latest"):
        note = f"显示源站首页该区域当前提供的{len(items)}部作品，保留原站顺序。"
    else:
        note = f"显示该公开榜单或更新页面当前提供的{len(items)}部作品；原页未提供分页链接。"
    return result(site, _SITES[site][0], kind, period, page, items,
                  has_more=more, source_url=url, label=label, note=note)
