"""Source identity coverage, separate from live availability or result counts."""

# Observed from https://mangayun.com/api/sites on this date. Updating this
# reference requires checking the live list; adding unrelated sources never
# compensates for a missing reference source.
REFERENCE_DATE = "2026-09-21"
REFERENCE_SOURCES = (
    "baozimh", "mangacopy", "dm5", "mangabz", "manben", "manhuagui",
    "comicbox", "manhuazhijia", "tuku", "rumanhua", "hipmh", "komiic",
)


def compare_sources(sites, reference=REFERENCE_SOURCES, reference_date=REFERENCE_DATE):
    available = {row["siteId"] for row in sites}
    expected = set(reference)
    return {
        "scope": "registered-source-ids",
        "liveAvailabilityChecked": False,
        "referenceDate": reference_date,
        "referenceCount": len(expected),
        "coveredCount": len(expected & available),
        "missing": sorted(expected - available),
        "additional": sorted(available - expected),
        "totalCount": len(available),
    }


def main():
    import argparse
    from datetime import date
    import json
    from . import providers
    from .mangayun_client import MangaYun

    parser = argparse.ArgumentParser(description="按源 ID 核对原版覆盖；不把新增源或搜索条数当成覆盖率。")
    parser.add_argument("--live", action="store_true", help="读取原版当前 /api/sites；不下载图片")
    args = parser.parse_args()
    reference, observed = REFERENCE_SOURCES, REFERENCE_DATE
    if args.live:
        reference = [source["siteId"] for source in MangaYun().sites()]
        if not reference:
            raise RuntimeError("原版源列表为空，无法判断覆盖")
        observed = date.today().isoformat()
    result = compare_sources(providers.sites(), reference, observed)
    result["referenceMode"] = "live" if args.live else "snapshot"
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(bool(result["missing"]))


if __name__ == "__main__":
    raise SystemExit(main())
