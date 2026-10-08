"""CopyManga's anonymous public website protocol, without executing JavaScript.

Observed scripts: comic_detail_pass202508141558.js and
comic_content_pass202508141534.js, linked by the ordinary /comic/{slug} page.
The page supplies ccz/cct (a public AES key); the first 16 characters of the
payload are its UTF-8 IV and the remainder is hex AES-CBC with PKCS#7 padding.
Directory requests use the page's unchanged #dnt value as the dnts header.

On 2026-09-21 the sampled website returned an empty directory and contentKey.
This adapter preserves that limitation; successful synthetic decoding is not
evidence that anonymous reading currently works. No APP fallback or login is
attempted, and source scripts are never downloaded or executed at runtime.
"""
from __future__ import annotations

from http.cookiejar import CookieJar
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, HTTPCookieProcessor, HTTPSHandler, Request, build_opener

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from .chapter_order import order_chapters
from .html_metadata import image_of, parse_html, text_of
from .native_sources import UA, _ssl_context

ORIGIN = "https://www.mangacopy.com"
IMAGE_DOMAINS = ("mangafunb.fun",)
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_PLAINTEXT_BYTES = 4 * 1024 * 1024
MAX_CHAPTERS = 20_000
MAX_IMAGES = 3_000
_SLUG = r"[A-Za-z0-9_-]{1,160}"
_UUID = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
EMPTY_DIRECTORY = "拷贝官网当前未向匿名访问返回章节目录，请在源站查看或切换漫画源"
EMPTY_IMAGES = "拷贝官网当前未向匿名访问返回本章图片，请在源站查看或切换漫画源"
ACCESS_RESTRICTED = "拷贝官网限制了当前匿名访问，请在源站查看或切换漫画源"


def _url(url, chapter=False):
    if not isinstance(url, str) or len(url) > 2048 or any(ord(c) <= 32 for c in url):
        raise ValueError("拷贝漫画地址无效")
    p = urlparse(url)
    if (p.scheme not in {"https", "http"} or p.hostname not in {"www.mangacopy.com", "mangacopy.com"}
            or p.username or p.password or p.port not in (None, 443 if p.scheme == "https" else 80)
            or p.params or p.query or p.fragment):
        raise ValueError("拷贝漫画地址不在已接入的官网范围")
    pattern = rf"/comic/({_SLUG})" + (rf"/chapter/({_UUID})" if chapter else "") + "/?"
    match = re.fullmatch(pattern, p.path)
    if not match:
        raise ValueError("拷贝漫画作品或章节地址无效")
    return ORIGIN + p.path.rstrip("/"), match[1]


class _Redirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old, new = urlparse(req.full_url), urlparse(newurl)
        if (new.scheme != "https" or new.hostname != "www.mangacopy.com"
                or new.username or new.password or new.port not in (None, 443)
                or new.netloc != old.netloc or new.fragment or new.params):
            raise RuntimeError("拷贝官网请求跳转到了未接入的地址")
        if new.path.startswith("/web/login"):
            raise RuntimeError(ACCESS_RESTRICTED)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _Session:
    def __init__(self):
        # Only cookies set by this normal anonymous visit are retained, and only
        # for this operation. No user session, token or fabricated cookie.
        self.opener = build_opener(_Redirect(), HTTPCookieProcessor(CookieJar()),
                                   HTTPSHandler(context=_ssl_context()))

    def get(self, url, referer, headers=None):
        request = Request(url, headers={"User-Agent": UA, "Referer": referer, **(headers or {})})
        try:
            with self.opener.open(request, timeout=25) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                final = response.geturl()
            if final != url:
                raise RuntimeError("拷贝官网未返回所请求的页面，请在源站查看")
            if len(raw) > MAX_RESPONSE_BYTES:
                raise RuntimeError("拷贝官网数据响应过大")
            return raw.decode("utf-8")
        except HTTPError as exc:
            if exc.code in (401, 403):
                raise RuntimeError(ACCESS_RESTRICTED) from None
            if exc.code == 429:
                raise RuntimeError("拷贝官网当前请求频率受限，请稍后再试") from None
            raise RuntimeError("拷贝官网页面暂不可用，请在源站查看") from None
        except (URLError, TimeoutError, UnicodeError):
            raise RuntimeError("拷贝官网请求失败，请稍后重试") from None


def _literal(root, name):
    # Only a standalone, unescaped quoted literal from an inline script is
    # accepted. Concatenation, calls and template strings are never evaluated.
    pattern = re.compile(r"\b(?:var|let|const)\s+" + re.escape(name)
                         + r"\s*=\s*(['\"])([^'\"\\\r\n]*)\1\s*;")
    values = []
    for node in root.all("script"):
        if not node.attrs.get("src"):
            script = "".join(child for child in node.children if isinstance(child, str))
            values.extend(match[2] for match in pattern.finditer(script))
    if len(values) != 1:
        raise RuntimeError("拷贝官网页面缺少有效阅读参数，请在源站查看")
    return values[0]


def _decrypt_json(payload, key):
    if not isinstance(payload, str) or not isinstance(key, str):
        raise RuntimeError("拷贝官网数据封装格式已变化")
    if len(payload) > 16 + 2 * (MAX_PLAINTEXT_BYTES + 16):
        raise RuntimeError("拷贝官网解码数据过大")
    try:
        key_bytes, iv, data = key.encode("utf-8"), payload[:16].encode("ascii"), payload[16:]
        if (len(key_bytes) not in {16, 24, 32} or len(iv) != 16 or not data
                or len(data) % 32 or not re.fullmatch(r"[0-9a-fA-F]+", data)):
            raise ValueError
        decoder = Cipher(algorithms.AES(key_bytes), modes.CBC(iv)).decryptor()
        padded = decoder.update(bytes.fromhex(data)) + decoder.finalize()
        unpadder = PKCS7(128).unpadder()
        plain = unpadder.update(padded) + unpadder.finalize()
        if len(plain) > MAX_PLAINTEXT_BYTES:
            raise RuntimeError("拷贝官网解码数据过大")
        return json.loads(plain.decode("utf-8").strip())
    except (ValueError, UnicodeError):
        raise RuntimeError("拷贝官网数据解码失败，未返回完整可读内容") from None


def _directory(data, slug):
    if (not isinstance(data, dict) or not isinstance(data.get("build"), dict)
            or data["build"].get("path_word") != slug or not isinstance(data.get("groups"), dict)):
        raise RuntimeError("拷贝官网未返回所选作品的完整目录")
    result, seen, total = [], set(), 0
    for key, group in data["groups"].items():
        if not isinstance(group, dict) or not isinstance(group.get("chapters"), list):
            raise RuntimeError("拷贝官网章节分组格式已变化")
        rows, count = group["chapters"], group.get("count")
        if type(count) is not int or count != len(rows):
            raise RuntimeError("拷贝官网章节目录不完整")
        total += count
        if total > MAX_CHAPTERS:
            raise RuntimeError("拷贝官网章节数量超过上限")
        group_name = group.get("name") if isinstance(group.get("name"), str) else str(key)
        entries = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("name"), str) or not row["name"].strip():
                raise RuntimeError("拷贝官网章节目录格式已变化")
            chapter_id = row.get("id")
            if not isinstance(chapter_id, str) or not re.fullmatch(_UUID, chapter_id):
                raise RuntimeError("拷贝官网章节编号无效")
            if chapter_id in seen:
                continue
            seen.add(chapter_id)
            url = f"{ORIGIN}/comic/{slug}/chapter/{chapter_id}"
            kind = {1: "話", 2: "卷", 3: "番外篇"}.get(row.get("type"), "") if type(row.get("type")) is int else ""
            label = " · ".join(filter(None, (group_name.strip(), kind)))
            entries.append({"id": url, "url": url, "name": row["name"].strip(), "group": label})
        # Retain source groups; normalize explicit chapter/volume labels within
        # each group, without inventing chapter IDs or paging missing data.
        result.extend(order_chapters(entries))
    return [{**row, "order": index} for index, row in enumerate(result)]


def _field(scope, label):
    # Labels are source-owned list rows with a colon. Do not treat an author's
    # name beginning with "作者" as another, shorter metadata label.
    for node in scope.all("li"):
        match = re.fullmatch(r"(?:" + label + r")\s*[：:]\s*(.*)", node.text())
        if match:
            return match[1].strip()
    return ""


def _metadata(root, url):
    scope = root.first(cls="comicParticulars-title")
    title = text_of(scope.first("h6")) if scope else ""
    if not title:
        raise RuntimeError("拷贝官网未返回作品详情，请在源站查看或切换漫画源")
    result = {"title": title, "author": _field(scope, "作者"),
              "description": text_of(root.first(cls="intro")), "status": _field(scope, "狀態|状态"),
              "coverUrl": image_of(scope.first(cls="comicParticulars-left-img"), url)}
    tags = [tag for tag in re.split(r"[/,，、\s]+", _field(scope, "題材|题材|類型|类型")) if tag]
    if tags:
        result['tags'] = tags[:20]
    return result


def metadata(url):
    url, _ = _url(url)
    return _metadata(parse_html(_Session().get(url, ORIGIN + '/')), url)


def details(url):
    url, slug = _url(url)
    session = _Session()
    root = parse_html(session.get(url, ORIGIN + "/"))
    result = _metadata(root, url)
    dnt = root.first(ident="dnt")
    dnts = dnt.attrs.get("value", "") if dnt else ""
    if not re.fullmatch(r"[\x21-\x7e]{1,128}", dnts):
        raise RuntimeError("拷贝官网缺少公开目录参数，请在源站查看")
    key = _literal(root, "ccz")
    response = session.get(f"{ORIGIN}/comicdetail/{slug}/chapters", url,
                           {"dnts": dnts, "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
    try:
        payload = json.loads(response)
    except ValueError:
        raise RuntimeError("拷贝官网未返回有效章节目录，请在源站查看") from None
    if not isinstance(payload, dict) or payload.get("code") not in (200, "200"):
        raise RuntimeError("拷贝官网未允许读取章节目录，请在源站查看或切换漫画源")
    chapters = _directory(_decrypt_json(payload.get("results"), key), slug)
    result.update(chapters=chapters, sourceUrl=url)
    if not chapters:
        result["unavailableReason"] = EMPTY_DIRECTORY
    return result


def _image_url(value):
    if not isinstance(value, str) or len(value) > 8192 or any(ord(c) <= 32 for c in value):
        raise RuntimeError("拷贝官网图片地址无效")
    try:
        p = urlparse(value)
        port = p.port
    except ValueError:
        raise RuntimeError("拷贝官网图片地址无效") from None
    host = p.hostname or ""
    if (p.scheme not in {"https", "http"} or p.username or p.password or p.fragment or p.params
            or port not in (None, 443 if p.scheme == "https" else 80)
            or not any(host == domain or host.endswith("." + domain) for domain in IMAGE_DOMAINS)):
        raise RuntimeError("拷贝官网返回了未接入的图片地址")
    return value


def images(url):
    url, slug = _url(url, chapter=True)
    root = parse_html(_Session().get(url, f"{ORIGIN}/comic/{slug}"))
    payload = _literal(root, "contentKey")
    if not payload:
        raise RuntimeError(EMPTY_IMAGES)
    rows = _decrypt_json(payload, _literal(root, "cct"))
    if not isinstance(rows, list) or not rows:
        raise RuntimeError(EMPTY_IMAGES)
    if len(rows) > MAX_IMAGES:
        raise RuntimeError("拷贝官网章节图片数量超过上限")
    if any(not isinstance(row, dict) for row in rows):
        raise RuntimeError("拷贝官网图片目录格式已变化")
    # Source order (including any repeated page URL) is authoritative.
    return [_image_url(row.get("url")) for row in rows]
