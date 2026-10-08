#!/usr/bin/env python3
"""Sardina manga: browser reader with MangaYun-compatible source adapters."""
from __future__ import annotations

import argparse
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import re
from pathlib import Path
import threading
import time
from urllib.parse import parse_qs, quote, urlparse
import urllib.request

from client import providers, comicbox_images, komiic, discovery, recommendations
from client.mangayun_client import MangaYun, UA
from client.native_sources import _ssl_context
from client.source_coverage import compare_sources
from client.source_catalog import catalog as source_catalog
from client.source_health import SourceHealth
from client.request_budget import ImageBudget, ImageCapacityError

ROOT = Path(__file__).resolve().parent
IMAGE_DOMAINS = ("bzcdn.net", "baozimh.com", "bgm.tv", "hamreus.com", "cdndm5.com", "mangabz.com", "tuku.cc", "s3imgs.top", "mangafunb.fun", "komiic.com", "shimolife.com", "ecombdimg.com", "mangacopy.com", "manhuagui.com", "mhgui.com") + providers.EXTRA_IMAGE_DOMAINS + comicbox_images.IMAGE_DOMAINS + discovery.image_domains()


class SardinaHTTPServer(ThreadingHTTPServer):
    # Python 3.12 defaults to five pending connections. A burst of ES module
    # requests can overflow that socket backlog before handlers are started.
    # This queue is independent of the bounded upstream request budgets.
    request_queue_size = 64


def public_origin(value):
    """Accept one explicit HTTPS origin; never infer trust from proxy headers."""
    try:
        parsed = urlparse(value)
        if (re.search(r'[\s\\]', value) or parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.path not in ('', '/')
                or parsed.params or parsed.query or parsed.fragment
                or not re.fullmatch(r'[A-Za-z0-9.-]+(?::[0-9]+)?', parsed.netloc)
                or any(not label or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?', label)
                       for label in parsed.hostname.split('.'))
                or parsed.port == 0):
            raise ValueError
        port = parsed.port
    except ValueError:
        raise argparse.ArgumentTypeError('公网地址必须是完整的 HTTPS 域名，不含路径、账号或查询参数') from None
    return 'https://' + parsed.hostname + (f':{port}' if port not in (None, 443) else '')


def validate_image(url):
    p = urlparse(url)
    if (p.scheme not in ("https", "http") or p.username or p.password or p.port not in (None,80,443)
            or not any(p.hostname == d or (p.hostname or "").endswith("." + d) for d in IMAGE_DOMAINS)):
        raise ValueError("不支持的图片地址")
    return url


def image_request_url(url):
    # Decoded chapter manifests can contain Chinese paths and spaces. urllib
    # requires an ASCII request target; keep existing escapes and signed query
    # delimiters intact rather than parsing/rebuilding the query string.
    return quote(validate_image(url), safe=":/?#[]@!$&'()*+,;=%")


def image_referer(site, url):
    if site not in providers.SOURCES:
        return "https://" + urlparse(url).netloc + "/"
    origin = "https://" + providers.SOURCES[site][1]
    if site in {"dm5", "manben"}:
        # These CDNs require the reading page, not just the site's home page.
        # Signed image URLs carry the chapter ID; the image path's number can
        # refer to a different object and must not be used to guess the page.
        chapter = parse_qs(urlparse(url).query, keep_blank_values=True).get("cid")
        if chapter:
            if len(chapter) != 1 or not re.fullmatch(r"\d{1,12}", chapter[0]):
                raise ValueError("图片章节标识无效")
            return origin + "/m" + chapter[0] + "/"
    return providers.IMAGE_REFERERS.get(site, origin + "/")


class ImageRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, image_request_url(newurl))


class Cache:
    """Hot reads take a short lock; cold requests share only their own key."""
    def __init__(self, limit=128, max_bytes=None):
        self.limit, self.data = limit, OrderedDict()
        self.lock = threading.Lock()
        self.pending = {}
        self.max_bytes, self.bytes = max_bytes, 0

    @staticmethod
    def size(value):
        return len(value[0]) if isinstance(value, tuple) and isinstance(value[0], bytes) else 0

    def get(self, key, ttl, load, *, refresh=False, on_wait=None):
        with self.lock:
            hit = self.data.get(key)
            if not refresh and hit and hit[0] > time.monotonic():
                self.data.move_to_end(key)
                return hit[1]
            future = self.pending.get(key)
            owner = future is None
            if owner:
                if len(self.pending) >= 128:
                    raise RuntimeError("请求较多，请稍后重试")
                self.pending[key] = future = Future()
        if not owner:
            if on_wait:
                on_wait()
            return future.result()
        try:
            value = load()
            with self.lock:
                old = self.data.pop(key, None)
                if old:
                    self.bytes -= self.size(old[1])
                size = self.size(value)
                if self.max_bytes is None or size <= self.max_bytes:
                    self.data[key] = (time.monotonic()+ttl, value)
                    self.bytes += size
                while len(self.data) > self.limit or self.max_bytes is not None and self.bytes > self.max_bytes:
                    _, removed = self.data.popitem(last=False)
                    self.bytes -= self.size(removed[1])
            future.set_result(value)
            return value
        except BaseException as error:
            future.set_exception(error)
            raise
        finally:
            with self.lock:
                if self.pending.get(key) is future:
                    del self.pending[key]


class Application:
    def __init__(self, mode="native"):
        self.mode, self.cache = mode, Cache()
        self.cover_cache = Cache(limit=512)
        # Composition and feed entries have separate caches and in-flight keys.
        self.recommendation_cache = Cache(limit=32)
        self.cover_slots = threading.BoundedSemaphore(4)
        self.book_metadata_cache = Cache(limit=200)
        self.book_metadata_slots = threading.BoundedSemaphore(2)
        self.metadata_slots = threading.BoundedSemaphore(8)
        self.image_slots = ImageBudget(limit=8, foreground_reserved=2)
        self.image_cache = Cache(limit=96, max_bytes=32 * 1024 * 1024)
        self.health = SourceHealth()
        self.upstream = MangaYun(timeout=45)

    def source_request(self, site, capability, load):
        if not self.metadata_slots.acquire(timeout=5):
            raise RuntimeError("漫画源请求较多，请稍后重试")
        try:
            return self.health.observe(site, capability, load)
        finally:
            self.metadata_slots.release()

    def image(self, site, url, *, refresh=False, purpose='cover'):
        if purpose not in {'reader', 'prefetch', 'download', 'cover'}:
            raise ValueError('图片请求类型无效')
        key = (site, url)
        def load():
            ticket = self.image_slots.acquire(key, foreground=purpose == 'reader', timeout=5)
            if ticket is None:
                raise ImageCapacityError("图片请求较多，请稍后重试")
            try:
                data, content_type = self._image(site, url)
                if (not isinstance(data, bytes) or not data or len(data) > 12 * 1024 * 1024
                        or content_type not in {'image/jpeg', 'image/png', 'image/webp', 'image/gif', 'image/avif'}):
                    raise RuntimeError('源站未返回有效图片，请重试或切换漫画源')
                return data, content_type
            finally:
                self.image_slots.release(ticket)
        # Covers and pages can share bytes without sharing health semantics.
        capability = 'coverImage' if purpose == 'cover' else 'image'
        return self.health.observe(site, capability, lambda: self.image_cache.get(
            key, 300, load, refresh=refresh,
            on_wait=(lambda: self.image_slots.promote(key)) if purpose == 'reader' else None))

    def _image(self, site, url):
        host = urlparse(url).hostname or ''
        logical_komiic = host == 'komiic.com' and urlparse(url).path.startswith('/api/image/')
        legacy_komiic = host == 'img.komiic.com' and 'ticket' in parse_qs(urlparse(url).query, keep_blank_values=True)
        if logical_komiic or legacy_komiic:
            return komiic.fetch_image(url)
        if any(host == domain or host.endswith('.' + domain) for domain in comicbox_images.IMAGE_DOMAINS):
            return comicbox_images.fetch_image(url)
        opener = urllib.request.build_opener(ImageRedirect(), urllib.request.HTTPSHandler(context=_ssl_context()))
        with opener.open(urllib.request.Request(image_request_url(url), headers={'User-Agent': UA, 'Referer': image_referer(site, url)}), timeout=20) as response:
            content_type = response.headers.get_content_type()
            if content_type not in ('image/jpeg', 'image/png', 'image/webp', 'image/gif', 'image/avif'):
                raise ValueError('源站没有返回受支持的图片')
            data = response.read(12 * 1024 * 1024 + 1)
            if len(data) > 12 * 1024 * 1024:
                raise ValueError('图片过大')
            return data, content_type

    def sites(self):
        return providers.sites() if self.mode == "native" else self.cache.get("sites", 3600, self.upstream.sites)

    def discovery_sources(self):
        return discovery.sources() if self.mode == "native" else []

    def discovery_feed(self, site, kind, period, page, *, refresh=False):
        return self.cache.get(("/api/discovery", site, kind, period, page), 300,
                              lambda: self.source_request(site, kind, lambda: discovery.fetch(site, kind, period, page)), refresh=refresh)

    def recommendations(self, *, refresh=False, batch=0):
        if self.mode != "native":
            raise ValueError("当前兼容模式暂不支持推荐，请使用原生源模式")
        if type(batch) is not int or not 0 <= batch <= 31:
            raise ValueError("推荐批次参数无效")
        result = self.recommendation_cache.get(batch, 300,
            lambda: recommendations.fetch(batch=batch, fetch_feed=lambda site, kind, period, page:
                self.discovery_feed(site, kind, period, page, refresh=refresh)), refresh=refresh)
        health = self.health.snapshot()
        return {**result, "items": [{**row, "metadataAvailable": row["siteId"] in providers.METADATA_SITES,
                "readingHealth": {mode: health.get(row["siteId"], {}).get(mode) for mode in ("chapter", "image")}}
                for row in result["items"]]}

    def book_metadata(self, body):
        site, url = body.get("siteId"), body.get("detailUrl")
        if self.mode != "native" or site not in providers.METADATA_SITES:
            raise ValueError("此源暂不支持轻量作品资料")
        providers.validate_url(site, url)
        def load():
            if not self.book_metadata_slots.acquire(timeout=1):
                raise RuntimeError("作品资料请求较多，请稍后重试")
            try:
                return self.source_request(site, 'metadata', lambda: providers.metadata(site, url))
            finally:
                self.book_metadata_slots.release()
        return self.book_metadata_cache.get((site, url), 6 * 3600, load)

    def discovery_cover(self, body):
        if self.mode != "native":
            raise ValueError("当前兼容模式尚未接入榜单封面，请使用原生源模式")
        site, url, refresh = discovery.normalize_cover_request(body)
        def load():
            # Keep this independent of list/search workers. Cached hits never
            # consume a slot; at most four source book pages are fetched.
            if not self.cover_slots.acquire(timeout=5):
                raise RuntimeError("封面请求较多，请稍后重试")
            try:
                return self.source_request(site, 'cover', lambda: discovery.book_cover(site, url))
            finally:
                self.cover_slots.release()
        return self.cover_cache.get((site, url), 6 * 3600, load, refresh=refresh)

    def search_site(self, site, keyword):
        started = time.monotonic()
        site_name = providers.SOURCES.get(site, (site, ""))[0]
        try:
            if self.mode == "native":
                rows = self.cache.get(("search",site,keyword), 120, lambda: self.source_request(site, 'search', lambda: providers.search(site,keyword)))
            else:
                site_name = next((source["siteName"] for source in self.sites() if source["siteId"] == site), site_name)
                groups = self.cache.get(("search",keyword),120,lambda:self.upstream.search(keyword))
                group = next((g for g in groups if g["siteId"]==site), {})
                if group.get("error"):
                    raise RuntimeError(group["error"])
                rows = group.get("results", [])
            return {"siteId":site,"siteName":site_name,"results":rows,"elapsedMs":round((time.monotonic()-started)*1000)}
        except Exception as exc:
            return {"siteId":site,"siteName":site_name,"results":[],"error":str(exc)[:240],"elapsedMs":round((time.monotonic()-started)*1000)}

    def post(self, path, body):
        if path == "/api/book-metadata":
            return self.book_metadata(body)
        if path == "/api/discovery/cover":
            return self.discovery_cover(body)
        site = body.get("siteId", "")
        if path == "/api/discovery":
            if self.mode != "native":
                raise ValueError("当前兼容模式尚未接入榜单，请使用原生源模式")
            site, kind, period, page = discovery.normalize_request(body)
            refresh = body.get("refresh", False)
            if type(refresh) is not bool:
                raise ValueError("刷新参数无效")
            return self.discovery_feed(site, kind, period, page, refresh=refresh)
        if path == "/api/search":
            keyword = body.get("keyword", "")
            if not isinstance(keyword,str) or not 1 <= len(keyword.strip()) <= 100:
                raise ValueError("请输入 1–100 字的漫画名")
            available = [source["siteId"] for source in self.sites()]
            if site:
                if site not in available:
                    raise ValueError("未接入的漫画源")
                return [self.search_site(site,keyword.strip())]
            with ThreadPoolExecutor(max_workers=4) as pool:
                return list(pool.map(lambda s:self.search_site(s,keyword.strip()),available))
        if path not in ("/api/details", "/api/chapter-images"):
            raise ValueError("未知接口")
        if site not in {source["siteId"] for source in self.sites()}:
            raise ValueError("当前模式未接入该漫画源")
        url = body.get("detailUrl" if path.endswith("details") else "chapterUrl", "")
        if not (path.endswith("chapter-images") and self.mode == "mangayun" and site == "hipmh"
                and re.fullmatch(r"[A-Za-z0-9_-]{8,250}", url)):
            providers.validate_url(site,url)
        if path.endswith("details"):
            refresh = body.get("refresh", False)
            if type(refresh) is not bool:
                raise ValueError("刷新参数无效")
            return self.cache.get((path,site,url),300,lambda:self.source_request(site, 'details', lambda: providers.details(site,url) if self.mode=="native" else self.upstream.details(site,url)),refresh=refresh)
        refresh = body.get("refresh", False)
        if type(refresh) is not bool:
            raise ValueError("刷新参数无效")
        def load_images():
            images = providers.images(site,url) if self.mode=="native" else self.upstream.chapter_images(site,url)
            if not isinstance(images, list) or not images:
                raise RuntimeError("这个源未返回章节图片，请重试或切换漫画源")
            for image in images:
                try:
                    parsed = urlparse(image) if isinstance(image, str) else None
                    if (parsed is None or parsed.scheme not in {'http', 'https'} or not parsed.hostname
                            or parsed.username or parsed.password or parsed.port not in (None, 80, 443)):
                        raise ValueError('invalid URL')
                    validate_image(image)
                except ValueError:
                    raise RuntimeError("这个源返回的章节图片地址无效，请重试或切换漫画源") from None
            return images
        images = self.cache.get((path,site,url),60,lambda:self.source_request(site, 'chapter', load_images),refresh=refresh)
        return {"images":images}


class Handler(BaseHTTPRequestHandler):
    server_version = "Sardina/0.4"

    def log_request(self, code="-", size="-"):
        # Image URLs may contain short-lived source tickets or signatures.
        self.log_message('"%s %s %s" %s %s', self.command, urlparse(self.path).path, self.request_version, str(code), str(size))

    def send(self, status, data, content_type="application/json; charset=utf-8", cache="no-store"):
        if not isinstance(data,bytes):
            data = json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length",str(len(data)))
        self.send_header("Cache-Control",cache)
        self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("Referrer-Policy","no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' blob: data: https: http:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError,ConnectionResetError):
            pass

    def request_origin(self):
        hosts = self.headers.get_all('Host', [])
        if len(hosts) != 1 or re.search(r'[\s/@?#\\]', hosts[0]):
            return None
        try:
            parsed = urlparse('http://' + hosts[0])
            port = parsed.port
            if not parsed.hostname or port == 0 or parsed.path or parsed.params:
                return None
        except ValueError:
            return None
        configured = getattr(self.server, 'public_origin', None)
        if configured:
            public = urlparse(configured)
            if parsed.hostname == public.hostname and (port or 443) == (public.port or 443):
                return configured
        if parsed.hostname in ('localhost', '127.0.0.1', '::1'):
            return 'http://' + hosts[0]
        return None

    def valid_host(self):
        return self.request_origin() is not None

    def do_GET(self):
        if not self.valid_host():
            self.send(403,{"error":"访问域名未获允许"}); return
        p = urlparse(self.path)
        try:
            if p.path == "/api/config":
                self.send(200,{"data":{"mode":self.server.app.mode,"sync":False,"disabledSources":[],"sourceCoverage":compare_sources(self.server.app.sites())}})
            elif p.path == "/api/sites":
                self.send(200,{"data":self.server.app.sites()})
            elif p.path == "/api/source-catalog":
                data = source_catalog(self.server.app.sites(), mode=self.server.app.mode, discovery_sources=self.server.app.discovery_sources())
                health = self.server.app.health.snapshot()
                for site in data['activeSources']:
                    site['health'] = health.get(site['siteId'], {})
                self.send(200, {'data': data})
            elif p.path == '/api/source-health':
                self.send(200, {'data': {'sources': self.server.app.health.snapshot(), 'scope': 'observed-requests'}})
            elif p.path == "/api/discovery/sources":
                self.send(200,{"data":self.server.app.discovery_sources()})
            elif p.path == "/api/recommendations":
                params = parse_qs(p.query, keep_blank_values=True, max_num_fields=2)
                batch_values = params.get("batch", ["0"])
                if (set(params) - {"refresh", "batch"} or params.get("refresh", ["0"]) not in (["0"], ["1"])
                        or len(batch_values) != 1 or not re.fullmatch(r"(?:0|[1-9]\d?)", batch_values[0]) or int(batch_values[0]) > 31):
                    raise ValueError("推荐刷新参数无效")
                self.send(200,{"data":self.server.app.recommendations(refresh=params.get("refresh") == ["1"], batch=int(batch_values[0]))})
            elif p.path == "/api/home-sections":
                self.send(200,{"data":json.loads((ROOT/"web/home.json").read_text())})
            elif p.path == "/api/image":
                url = validate_image(parse_qs(p.query).get("url",[""])[0])
                params = parse_qs(p.query)
                site = params.get('siteId', [''])[0]
                purposes = params.get('purpose', ['cover'])
                if len(purposes) != 1:
                    raise ValueError('图片请求类型无效')
                data, content_type = self.server.app.image(site, url, refresh=bool(params.get('retry') or params.get('coverRetry')), purpose=purposes[0])
                self.send(200, data, content_type, 'private, max-age=300')
            elif p.path.startswith("/api/"):
                self.send(404,{"error":"接口不存在"})
            else:
                files = {"/":"index.html","/app.js":"app.js","/style.css":"style.css","/logo.svg":"logo.svg","/favicon.ico":"brand/sardina-097-c.png", "/brand":"brand-preview.html"}
                for name in ("sardina.css", "brand/sardina-070.png", "brand/sardina-097.png", "brand/sardina-097-a.png", "brand/sardina-097-b.png", "brand/sardina-097-c.png"):
                    files["/" + name] = name
                for name in ("search-model.js", "search-view.js", "search.css", "reader.js", "reader-model.js", "reader-transport.js", "reader.css", "source-catalog.js", "source-catalog.css", "source-preferences.js", "library-model.js", "library.css", "library-updates.js", "library-store.js", "library-auto-updates.js", "book-identity.js", "discovery.js", "discovery-model.js", "discovery-covers.js", "discovery.css", "recommendations.js", "recommendations-model.js", "recommendations-feedback.js", "recommendations.css", "cover-wall.js", "cover-wall.css", "home.css"):
                    files["/" + name] = name
                files["/recommendations-ranking.js"] = "recommendations-ranking.js"
                files["/route-history.js"] = "route-history.js"
                files["/page-window.js"] = "page-window.js"
                files["/cover-pause.js"] = "cover-pause.js"
                files["/recommendations-metadata.js"] = "recommendations-metadata.js"
                files["/search-results-model.js"] = "search-results-model.js"
                files["/search-characters.js"] = "search-characters.js"
                for name in ("image-loader.js", "download-store.js", "download-model.js", "downloads.js", "downloads.css"):
                    files["/" + name] = name
                file = files.get(p.path)
                if not file and (p.path in ("/discover", "/discover/popular", "/discover/latest") or p.path.startswith("/s/") or p.path.startswith("/m/") or p.path.startswith("/read/")):
                    file = "index.html"
                if not file:
                    self.send(404,{"error":"页面不存在"}); return
                self.send(200,(ROOT/"web"/file).read_bytes(),mimetypes.guess_type(file)[0] or "text/plain")
        except ValueError as exc:
            self.send(400,{"error":str(exc)})
        except Exception as exc:
            self.send(502,{"error":str(exc)[:240]})

    def do_POST(self):
        expected_origin = self.request_origin()
        origins = self.headers.get_all('Origin', [])
        if not expected_origin or (origins and origins != [expected_origin]):
            self.send(403,{"error":"请求来源不匹配"}); return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 16384:
                raise ValueError("请求长度无效")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body,dict):
                raise ValueError("请求需要 JSON 对象")
            self.send(200,{"data":self.server.app.post(urlparse(self.path).path,body)})
        except (ValueError,TypeError,KeyError) as exc:
            self.send(400,{"error":str(exc)[:240]})
        except Exception as exc:
            self.send(502,{"error":str(exc)[:240]})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port",type=int,default=8765)
    parser.add_argument("--provider",choices=("native","mangayun"),default="native")
    parser.add_argument('--public-origin', type=public_origin, help='HTTPS 反向代理的公网地址，例如 https://manga.example.com')
    parser.add_argument('--state-dir', type=Path, default=ROOT / 'output/runtime', help='漫画源健康状态保存目录')
    args = parser.parse_args()
    import signal
    def stop_service(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop_service)
    server = SardinaHTTPServer(("127.0.0.1",args.port),Handler)
    server.public_origin = args.public_origin
    server.app = Application(args.provider)
    server.app.health = SourceHealth(args.state_dir / 'source-health.json')
    print(f"Sardina manga http://127.0.0.1:{args.port} / provider={args.provider}",flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.app.health.flush(force=True)
        server.server_close()


if __name__ == "__main__":
    main()
