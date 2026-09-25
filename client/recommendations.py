"""Bounded, attributable discovery candidates for local recommendation rules.

A batch reads at most six public metadata lists, with three workers. Source
adapters validate their URLs and payloads; no details, chapters or images are
fetched here. The injected reader lets the application share its discovery
cache. Ranking preferences and conservative cross-source identity live locally
in the browser, so this layer never deletes works just because titles match.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timezone
import threading
import time
from urllib.parse import urlsplit

from . import discovery, discovery_api, discovery_apk, discovery_html, providers
from .discovery_common import image_url

MAX_BATCH = 31
_MAX_FEEDS = 6
_MAX_WORKERS = 3
_SOURCE_SLOTS = threading.BoundedSemaphore(_MAX_WORKERS)
_MAX_ITEMS = 200
_MAX_PER_FEED = 40
# Avoid launching a fresh 25-second source request near the end of a batch.
_DISPATCH_SECONDS = 7
_TOTAL_SECONDS = 33
_PREFERRED = ("hipmh", "komiic", "manben", "baozimh", "zaimanhua", "mangacopy")
_PRIMARY_KINDS = {"hipmh": "popular", "komiic": "popular", "manben": "popular",
                  "baozimh": "latest", "zaimanhua": "popular", "mangacopy": "latest"}
_FIELDS = ("title", "detailUrl", "coverUrl", "author", "description", "status", "latestChapter",
           "updatedAtText", "edition", "language")
_COVERS = {"manben": ("cdndm5.com",), "manhuagui": ("mhgui.com", "hamreus.com"),
           **discovery_html._COVERS, **discovery_api._COVERS, **discovery_apk._CDN}
# These adapters publish lists on a different verified gateway/web host than
# their mobile book URLs. This is provenance only, never a fetch destination.
_LIST_HOSTS = {"zaimanhua": ("manhua.zaimanhua.com",),
               "manhuatai": ("m.kanman.com",), "shenmanhua": ("m.kanman.com",)}


class RecommendationsError(RuntimeError):
    pass


def _plan():
    """First rotate across registered sources, then across their other mode.

    These are list snapshots, not a crawl. Periods come from source capability
    declarations and every request stays on page one. Returning nextBatch makes
    exhaustion explicit and bounds the complete rotation as well as each call.
    """
    registered = discovery.sources()
    order = {site: index for index, site in enumerate(_PREFERRED)}
    registered.sort(key=lambda item: order.get(item["siteId"], len(order)))
    first, later = [], []
    for index, source in enumerate(registered):
        modes = source["modes"]
        preferred = _PRIMARY_KINDS.get(source["siteId"], "popular" if index % 2 == 0 else "latest")
        primary = next((mode for mode in modes if mode["kind"] == preferred), modes[0])
        for mode in [primary, *(mode for mode in modes if mode is not primary)]:
            periods = mode.get("periods", [])
            period = periods[0]["id"] if periods else ""
            config = (source["siteId"], source["siteName"], mode["kind"], period, 1, mode["label"])
            (first if mode is primary else later).append(config)
    feeds = (first + later)[:(MAX_BATCH + 1) * _MAX_FEEDS]
    return [feeds[start:start + _MAX_FEEDS] for start in range(0, len(feeds), _MAX_FEEDS)]


def _cover_url(site, value):
    return bool(image_url(value, "", _COVERS.get(site, ())))


def _public_url(value):
    if (not isinstance(value, str) or not value or len(value) > 4096
            or any(ord(c) <= 32 for c in value) or "\\" in value):
        return False
    try:
        url = urlsplit(value)
        return (url.scheme in {"http", "https"} and url.port in (None, 80, 443)
                and not url.username and not url.password and not url.fragment and bool(url.hostname))
    except ValueError:
        return False


def _detail_url(site, value):
    if not _public_url(value):
        return False
    try:
        providers.validate_url(site, value)
        # Built-in lists predate the common validators. Keep their stricter
        # book paths so a ranking/list URL cannot become a recommendation.
        if site in {"manben", "manhuagui"}:
            discovery._cover_endpoint(value)
        elif site in discovery_html._SITES:
            discovery_html._book(site, value)
        elif urlsplit(value).path in {"", "/"}:
            return False
        return True
    except (TypeError, ValueError, RuntimeError):
        return False


def _source_url(site, value):
    if not _public_url(value):
        return False
    if urlsplit(value).hostname in _LIST_HOSTS.get(site, ()):
        return True
    try:
        providers.validate_url(site, value)
        return True
    except (TypeError, ValueError):
        return False


def _batch(value, config):
    site, name, kind, period, page, label = config
    if (not isinstance(value, dict) or value.get("siteId") != site or value.get("kind") != kind
            or value.get("period") != period or type(value.get("page")) is not int or value["page"] != page
            or not isinstance(value.get("items"), list) or len(value["items"]) > 5000
            or not _source_url(site, value.get("sourceUrl"))):
        raise RecommendationsError("来源列表身份或结构发生变化")
    try:
        stamp = datetime.fromisoformat(value["fetchedAt"].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError
    except (KeyError, AttributeError, TypeError, ValueError):
        raise RecommendationsError("来源列表缺少有效出处或获取时间") from None
    items = []
    for row in value["items"]:
        if (not isinstance(row, dict) or row.get("siteId") != site or not isinstance(row.get("title"), str)
                or not row["title"].strip() or not _cover_url(site, row.get("coverUrl"))
                or not _detail_url(site, row.get("detailUrl"))):
            continue
        item = {field: row[field][:4000] for field in _FIELDS if isinstance(row.get(field), str)}
        for field in ("tags", "genres"):
            if isinstance(row.get(field), list):
                item[field] = [tag[:80] for tag in row[field][:20] if isinstance(tag, str) and tag.strip()]
        item.update(siteId=site, siteName=name, title=row["title"].strip()[:500], recommendationKind=kind,
                    recommendationLabel=label, recommendationSourceUrl=value["sourceUrl"])
        if kind == "popular" and type(row.get("rank")) is int and row["rank"] > 0:
            item["rank"] = row["rank"]
        items.append(item)
        if len(items) == _MAX_PER_FEED:
            break
    if not items:
        raise RecommendationsError("来源暂无带有效封面地址的作品")
    origin = {"siteId": site, "siteName": name, "kind": kind, "period": period, "page": page,
              "sourceUrl": value["sourceUrl"], "fetchedAt": value["fetchedAt"], "label": label}
    if value.get("paginationNote"):
        origin["note"] = str(value["paginationNote"])[:500]
    return items, origin


def fetch(*, batch=0, fetch_feed=None):
    plan = _plan()
    if type(batch) is not int or not 0 <= batch < len(plan):
        raise ValueError("推荐批次超出已接入的范围")
    reader = fetch_feed or discovery.fetch
    configs, started = plan[batch], time.monotonic()
    batches, origins, warnings = [], [], []

    def read(config):
        remaining = _DISPATCH_SECONDS - (time.monotonic() - started)
        if remaining <= 0 or not _SOURCE_SLOTS.acquire(timeout=remaining):
            raise RecommendationsError("本批请求已到时限，请稍后刷新")
        try:
            if time.monotonic() - started > _DISPATCH_SECONDS:
                raise RecommendationsError("本批请求已到时限，请稍后刷新")
            site, _, kind, period, page, _ = config
            return _batch(reader(site, kind, period, page), config)
        finally:
            _SOURCE_SLOTS.release()

    pool = ThreadPoolExecutor(max_workers=_MAX_WORKERS, thread_name_prefix="recommendations")
    futures = [pool.submit(read, config) for config in configs]
    try:
        complete, pending = wait(futures, timeout=_TOTAL_SECONDS)
        for future in pending:
            future.cancel()
        # Preserve source order, independent of response timing.
        for config, future in zip(configs, futures):
            try:
                if future not in complete:
                    raise RecommendationsError("本批请求超时，请稍后重试")
                rows, origin = future.result()
                batches.append(rows); origins.append(origin)
            except Exception as error:
                warnings.append(f"{config[1]}推荐暂不可用：{str(error)[:160]}")
    finally:
        # Source transports have their own deadlines; an injected/slow reader
        # must not hold up the HTTP response after the aggregation deadline.
        pool.shutdown(wait=False, cancel_futures=True)

    items, books = [], set()
    for position in range(max((len(rows) for rows in batches), default=0)):
        for rows in batches:
            if position >= len(rows):
                continue
            book = rows[position]
            key = (book["siteId"], book["detailUrl"].rstrip("/"))
            if key in books:
                continue
            books.add(key); items.append(book)
            if len(items) == _MAX_ITEMS:
                break
        if len(items) == _MAX_ITEMS:
            break
    next_batch = batch + 1 if batch + 1 < len(plan) else None
    # An empty failed batch still exposes the next rotation, allowing recovery
    # without repeatedly requesting the same broken sources or inventing books.
    return {"items": items, "fetchedAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "origins": origins, "warnings": warnings, "batch": batch, "nextBatch": next_batch,
            "hasMore": next_batch is not None, "candidateCount": len(items)}
