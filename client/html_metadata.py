"""Small HTML tree for source adapters, with no browser or extra dependency.

Selectors below describe observed source markup. Missing fields stay empty; a
site-wide author meta tag or a recommendation must not become book metadata.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
import re
from urllib.parse import urljoin, urlparse


@dataclass
class Element:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list = field(default_factory=list)
    parent: Element | None = field(default=None, repr=False)

    def all(self, tag=None, cls=None, ident=None):
        if ((tag is None or self.tag == tag)
                and (cls is None or cls in self.attrs.get("class", "").split())
                and (ident is None or self.attrs.get("id") == ident)):
            yield self
        for child in self.children:
            if isinstance(child, Element):
                yield from child.all(tag, cls, ident)

    def first(self, tag=None, cls=None, ident=None):
        return next(self.all(tag, cls, ident), None)

    def text(self):
        if self.tag in {"script", "style"}:
            return ""
        return re.sub(r"\s+", " ", " ".join(
            child.text() if isinstance(child, Element) else child
            for child in self.children
        )).strip()


class _Tree(HTMLParser):
    void = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Element("document")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Element(tag, dict((key, value or "") for key, value in attrs), parent=self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in self.void:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.void:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def parse_html(source):
    parser = _Tree()
    parser.feed(source)
    parser.close()
    return parser.root


def text_of(node):
    return node.text() if node else ""


def image_of(node, base):
    if node is None:
        return ""
    for image in node.all():
        raw = ""
        if image.tag in {"img", "amp-img"}:
            raw = next((image.attrs[key] for key in ("data-src", "data-original", "src") if image.attrs.get(key)), "")
        elif "background-image" in image.attrs.get("style", ""):
            match = re.search(r"url\(\s*['\"]?([^)'\"]+)", image.attrs["style"], re.I)
            raw = match[1] if match else ""
        if not raw or re.search(r"(?:default_cover|placeholder|/nopic\.|/load\.)", raw, re.I):
            continue
        url = urljoin(base, raw.strip())
        if urlparse(url).scheme in {"http", "https"}:
            return url
    return ""


def label_value(root, label):
    """Read the smallest labelled element, not the entire detail container."""
    pattern = re.compile(r"^(?:" + label + r")\s*[：:]?\s*(.*)$")
    values = []
    for node in root.all():
        if node.tag not in {"p", "span", "div", "li"}:
            continue
        value = node.text()
        if len(value) > 500:
            continue
        match = pattern.match(value)
        if match and match[1]:
            values.append(match[1].strip())
    return min(values, key=len) if values else ""


def known_status(text):
    match = re.search(r"暂停连载|暫停連載|已完结|已完結|连载中|連載中|连载|連載|完结|完結|暂停|暫停|停更", text)
    return match[0] if match else ""


def detail_metadata(site, page, url):
    root = parse_html(page)
    metas = {node.attrs.get("property", node.attrs.get("name", "")).lower().strip(): node.attrs.get("content", "").strip()
             for node in root.all("meta")}
    title = author = description = cover = status = ""
    empty = Element("empty")
    scope = empty
    if site == "baozimh":
        scope = root.first(cls="comics-detail__info") or empty
        title = text_of(root.first(cls="comics-detail__title"))
        author = text_of(root.first(cls="comics-detail__author"))
        description = text_of(root.first(cls="comics-detail__desc"))
        cover = image_of(root.first(cls="comics-detail__poster"), url)
    elif site == "manhuazhijia":
        scope = root.first(cls="info-wrapper") or empty
        title = text_of(scope.first("h1"))
        description = text_of(scope.first(cls="summary"))
        cover = image_of(root.first(cls="cover-wrapper"), url)
    elif site == "manhuagui":
        scope = root.first(cls="book-detail") or empty
        title = text_of(scope.first("h1"))
        description = text_of(root.first(ident="intro-all") or root.first(ident="intro-cut"))
        cover = image_of(root.first(cls="book-cover"), url)
    elif site == "tuku":
        scope = root.first(cls="manga-info-wrap") or empty
        title = text_of(scope.first("h1"))
        description = text_of(scope.first(cls="multi-ellipsis"))
        cover = image_of(root.first(cls="manga-cover"), url)
    elif site in {"rumanhua", "dumanwu"}:
        scope = root.first(cls="comic-info") or empty
        title = text_of(scope.first("h1")) or text_of(root.first("h1")) or text_of(root.first(cls="banner-title"))
        description = text_of(root.first(cls="cartoon-introduction") or root.first(cls="introduction"))
        cover = image_of(scope.first(cls="book-cover") or root.first(cls="banner-pic"), url)
    elif site == "mangabz":
        title = text_of(root.first(cls="detail-info-title"))
        description = text_of(root.first(cls="detail-info-content"))
        cover = image_of(root.first(cls="detail-info-cover"), url)
        scope = root.first(cls="detail-info-tip") or empty
    elif site in {"dm5", "manben"}:
        scope = root.first(cls="banner_detail_form" if site == "dm5" else "comicInfo") or empty
        title_node = scope.first(cls="title")
        # These title paragraphs also contain rating stars and scores.
        title = re.sub(r"\s+", " ", " ".join(child for child in title_node.children if isinstance(child, str))).strip() if title_node else ""
        description = text_of(scope.first(cls="content"))
        cover = image_of(scope.first(cls="cover"), url)
    # Only book-specific metadata fallbacks, never generic name=Author.
    title = title or metas.get("og:novel:book_name", "") or metas.get("og:title", "")
    author = author or label_value(scope, r"(?:漫画|漫畫)?作\s*者") or metas.get("og:novel:author", "") or metas.get("og:author", "")
    status = known_status(label_value(scope, r"(?:漫画|漫畫)?(?:状\s*态|狀\s*態)")) or known_status(metas.get("og:novel:status", ""))
    cover = cover or metas.get("og:image", "")
    if cover:
        cover = urljoin(url, cover)
    description = re.sub(r"^(?:简介|簡介)\s*[：:]\s*", "", description)
    raw_tags = label_value(scope, r"(?:题材|題材|类别|類別|类型|類型)") or metas.get("og:novel:category", "")
    tags = [tag.strip() for tag in re.split(r"[/,，、\s]+", raw_tags) if 0 < len(tag.strip()) <= 20][:12]
    return {"title": title, "author": author, "description": description, "coverUrl": cover, "status": status, **({"tags": tags} if tags else {})}
