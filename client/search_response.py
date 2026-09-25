"""Reject transport/challenge documents masquerading as empty HTML searches.

Valid book rows remain source-parser evidence. Zero rows need a positive empty
result statement or the source's explicit zero count; a missing CSS selector is
never sufficient evidence that a work does not exist.
"""
from __future__ import annotations

import re

try:
    from .html_metadata import parse_html, text_of
except ImportError:  # Existing standalone adapter CLIs.
    from html_metadata import parse_html, text_of


class SearchResponseError(RuntimeError):
    pass


_COUNT = re.compile(r"(?:搜索|搜尋)(?:结果|結果)\s*[（(]\s*(\d+)\s*[)）]|共找到\s*(\d+)\s*[条條]|共有\s*(\d+)\s*[个個](?:结果|結果)")
_EMPTY = re.compile(
    r"^(?:🔍\s*)?(?:(?:很抱歉|抱歉|对不起|對不起)[，,:：]?\s*)?"
    r"(?:没有找到|沒有找到|未找到|未搜索到|没有搜索到|沒有搜尋到|查无|查無)"
    r"(?:任何|符合条件的|符合條件的|相关的?|相關的?)?\s*(?:漫画|漫畫|作品|结果|結果)"
    r"(?:[。.!！…\s]|$)"
)


def validate_search_response(site, source, rows):
    root = parse_html(source)
    title = text_of(root.first("title"))
    # Source sites often include a normal login link. Only explicit interstitial
    # titles/markers, not that navigation text, identify a blocked search.
    if (re.search(r"^(?:just a moment|attention required|verify (?:you are|your)|安全验证|安全驗證|人机验证|人機驗證|访问验证|訪問驗證)", title, re.I)
            or root.first(ident="cf-challenge-running") or root.first(ident="challenge-form")
            or re.search(r"^\s*(?:请登录后|請登入後|请先登录|請先登入).*(?:搜索|搜尋)", root.text())):
        raise SearchResponseError("源站要求访问验证或存在访问限制，未取得搜索结果，请稍后重试")
    if rows:
        return

    if site == "baozimh":
        heading = root.first(cls="keyword-hinter")
    elif site == "manhuazhijia":
        heading = root.first(cls="search-info")
    elif site == "tuku":
        heading = root.first(cls="search-tip-text")
    elif site == "dm5":
        heading = next((node for node in root.all("h1") if "搜索" in node.text()), None)
    elif site == "mangabz":
        heading = root.first(cls="result-title")
    elif site == "manben":
        scope = root.first(cls="mainSearch")
        heading = scope.first(cls="topBar") if scope else None
    else:  # A GUI empty statement still needs an explicit search-page context.
        heading = root.first("title") if re.search(r"搜索|搜尋", title) else None

    counts = [int(next(value for value in match.groups() if value is not None)) for match in _COUNT.finditer(text_of(heading))]
    if counts:
        if all(value == 0 for value in counts):
            return
        raise SearchResponseError("源站声明有搜索结果，但列表结构无法解析，请稍后重试")
    if heading is not None:
        # Inspect short visible messages, not descriptions, scripts or the full
        # document text. A keyword echo is not a no-results statement.
        for node in root.all():
            if node.tag not in {"div", "p", "li", "main", "section"}:
                continue
            value = node.text()
            if len(value) <= 240 and _EMPTY.search(value):
                return
    raise SearchResponseError("源站搜索页面结构异常，无法确认结果是否为空，请稍后重试")
