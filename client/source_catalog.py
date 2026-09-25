"""Curated extraction inventory, deliberately separate from executable sources.

Only public metadata is shipped. Imported JavaScript/headers/rules and APKs are
never loaded by this module, and an inventory entry cannot enable an adapter.
"""
from __future__ import annotations

import json
from pathlib import Path

CATALOG_PATH = Path(__file__).with_name("source_catalog.json")
STATUSES = {"integrated", "duplicate", "blocked", "pending"}


def catalog(sites, *, mode="native", discovery_sources=None):
    snapshot = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    active = {site["siteId"]: site for site in sites}
    discovery_modes = ({source["siteId"]: [item["kind"] for item in source["modes"]]
                        for source in discovery_sources} if discovery_sources is not None and mode == "native" else None)
    entries = []
    seen = set()
    for item in snapshot["entries"]:
        entry_id = item["entryId"]
        status = item["status"]
        if entry_id in seen or status not in STATUSES:
            raise RuntimeError("源目录资料存在重复编号或未知状态")
        seen.add(entry_id)
        row = {field: item.get(field, "") for field in
               ("entryId", "name", "origin", "status", "reason", "collection", "siteId", "checkedAt")}
        row["searchEnabled"] = bool(row["siteId"] and row["siteId"] in active)
        if status == "integrated" and not row["searchEnabled"]:
            row["status"] = "pending"
            row["reason"] = "当前模式未启用此源；可在原生源模式使用" if mode != "native" else "适配器当前未启用"
        if row["searchEnabled"]:
            row["mappedName"] = active[row["siteId"]]["siteName"]
        entries.append(row)
    return {"updatedAt": snapshot["updatedAt"], "ruleEntryCount": snapshot["ruleEntryCount"],
            "entryCount": len(entries), "activeSourceCount": len(active), "mode": mode,
            "counts": {status: sum(row["status"] == status for row in entries) for status in STATUSES},
            "activeSources": [{**site, **({"discoveryModes": discovery_modes.get(site["siteId"], [])}
                                         if discovery_modes is not None else {})} for site in active.values()], "entries": entries,
            "scope": "extracted-source-inventory", "liveAvailabilityChecked": False}
