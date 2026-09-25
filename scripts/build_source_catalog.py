"""Build the public inventory from audited metadata, never executable rules.

Run after updating output/apk-round2-integration/*-audit/catalog.json.
This developer-only command does no network I/O and never registers sources.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "output/apk-round2-integration"


def build():
    audit = json.loads((AUDIT / "vomic-audit.json").read_text())
    rows = audit["entries"]
    expected = {f"vomic:{i}" for i in range(95)} | {f"octopus:{i}" for i in range(4)}
    if {row["entryId"] for row in rows} != expected or len(rows) != 99:
        raise ValueError("新规则清单必须准确包含 95 + 4 条记录")
    entries = []
    for row in rows:
        entries.append({"entryId": row["entryId"], "name": row["name"], "origin": row["origin"],
                        "siteId": row.get("siteId") or "", "status": row["status"], "reason": row["reason"],
                        "collection": "你搜 v185" if row["entryId"].startswith("vomic:") else "章鱼图源",
                        "checkedAt": audit["date"]})
    extra_file = AUDIT / "protocol-catalog.json"
    if extra_file.exists():
        entries.extend(json.loads(extra_file.read_text())["entries"])
    ids = set()
    for row in entries:
        if row["entryId"] in ids or row["status"] not in {"integrated", "duplicate", "blocked", "pending"}:
            raise ValueError("源条目编号或状态无效")
        ids.add(row["entryId"])
        if any(not isinstance(value, str) for value in row.values()):
            raise ValueError("运行时目录只接受公开文本字段")
        if not row["origin"] and row["status"] == "pending":
            continue  # Some APKs expose no verifiable origin; retain that gap.
        if urlparse(row["origin"]).scheme not in {"http", "https"}:
            raise ValueError("源条目缺少公开 HTTP 入口")
    result = {"updatedAt": audit["date"], "ruleEntryCount": 99, "entries": entries}
    target = ROOT / "client/source_catalog.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(f"已整理 {len(entries)} 条公开资料，其中规则 99 条；未自动启用任何适配器。")


if __name__ == "__main__":
    build()
