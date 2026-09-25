"""Conservative reading order for chapter lists with observed title formats.

Only explicit chapter labels and known section labels carry ordering meaning.
Database IDs, URL paths, page counts and unrelated numbers are never consulted.
Unclassified entries keep their original slots, so an unlabelled introduction
cannot silently become the last page of a book.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal
import re
from typing import Any
import unicodedata


_EXTRA = re.compile(
    r"(?:^|[\s:：·/(（-])(?:番外(?:篇|话|話|回|章)?|外传|外傳|特别篇|特別篇|特典|附录|附錄)"
    r"(?=$|[\s:：·/()（）-]|第|\d)"
)
_PRELUDE = re.compile(r"^(?:序章|序话|序話|序篇|前言|序言)(?:$|[\s:：·(（])")
_NUMBERED = re.compile(r"^(?:第\s*)?(\d+(?:\.\d+)?)\s*([话話回章卷册冊])")
_VOLUME_GROUPS = {"单行本", "單行本", "单行卷", "單行卷", "卷", "卷本"}
_MAIN_GROUPS = {"单话", "單話", "连载", "連載", "正文", "正篇"}


def _text(value: Any) -> str:
    return unicodedata.normalize("NFKC", value).strip() if isinstance(value, str) else ""


def _classification(row: Mapping[str, Any]) -> tuple[int, Decimal | None] | None:
    name = _text(row.get("name") or row.get("title"))
    group = _text(row.get("group"))
    if _EXTRA.search(group) or _EXTRA.match(name):
        return 3, None
    if _PRELUDE.match(name):
        return 0, None
    match = _NUMBERED.match(name)
    if match and match[2] not in "卷册冊":
        kind = 1
        # An explicit section can identify a volume even when its subheading
        # uses a chapter number. Unknown section names imply nothing.
        if group in _VOLUME_GROUPS:
            kind = 2
        return kind, Decimal(match[1])
    # A normal numbered chapter can mention a special in its subtitle. The
    # fallback is for labels such as "3月的獅子 番外篇", without a main number.
    if _EXTRA.search(name):
        return 3, None
    if match:
        return 2, Decimal(match[1])
    if group in _VOLUME_GROUPS:
        return 2, None
    if group in _MAIN_GROUPS:
        return 1, None
    return None


def order_chapters(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return shallow copies ordered as prelude, numbered chapters, volumes, extras.

    ``name`` (or ``title``) and ``group`` are the only inputs to ordering.
    Completely unknown entries stay in their original positions. Within a
    known section, entries without an explicit number also keep their relative
    slots; numbered entries sort stably around them. Extras retain source order.
    Existing ``order`` values are refreshed, but no field is invented or renamed.
    The caller still owns source-specific de-duplication and section selection.
    """
    copied = [dict(row) for row in rows]
    classified = [(row, _classification(row)) for row in copied]
    movable = [index for index, (_, kind) in enumerate(classified) if kind is not None]
    ordered = []
    for section in range(4):
        bucket = [(row, kind[1]) for row, kind in classified if kind is not None and kind[0] == section]
        numeric_slots = [i for i, (_, number) in enumerate(bucket) if number is not None]
        numbered = sorted((bucket[i] for i in numeric_slots), key=lambda item: item[1])
        for index, item in zip(numeric_slots, numbered):
            bucket[index] = item
        ordered.extend(row for row, _ in bucket)
    for index, row in zip(movable, ordered):
        copied[index] = row
    for index, row in enumerate(copied):
        if "order" in row:
            row["order"] = index
    return copied
