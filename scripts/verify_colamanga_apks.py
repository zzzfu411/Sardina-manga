"""Cross-check the COLAMANGA audit with Androguard, without executing APK code.

Run in an isolated Python environment containing androguard==4.1.4.
This complements the separate ZIP/DEX/ELF and Android apksig checks.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import subprocess
import zipfile

from loguru import logger

logger.disable("androguard")

from androguard.core.apk import APK
from androguard.core.dex import DEX
from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
PRIOR = ROOT / "output/colamanga-recheck-20260921"
OUT = ROOT / "output/apk-revalidation-20260921"
ANDROID = "{http://schemas.android.com/apk/res/android}"


def save(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def normalize_value(value, reference):
    if not isinstance(reference, int):
        return value
    if value == "true":
        return 0xFFFFFFFF
    if value == "false":
        return 0
    if value.startswith("@"):
        return int(value[1:], 16)
    return int(value, 0) if value.lower().startswith("0x") else int(value)


def check_manifest(apk, prior_manifest):
    xml = apk.get_android_manifest_xml()
    checks = []
    for old in prior_manifest:
        if old["tag"] == "manifest":
            candidates = [xml]
        elif old["tag"] == "activity":
            name = old["attributes"]["name"]
            candidates = [node for node in xml.findall(".//activity") if node.get(ANDROID + "name") == name]
        else:
            candidates = xml.findall(".//" + old["tag"])
        for key, expected in old["attributes"].items():
            value = None
            if len(candidates) == 1:
                node = candidates[0]
                value = node.get(ANDROID + key, node.get(key))
            try:
                actual = normalize_value(value, expected) if value is not None else None
            except (ValueError, AttributeError):
                actual = value
            checks.append({"tag": old["tag"], "attribute": key,
                           "expected": expected, "actual": actual, "match": actual == expected})
    return checks


def method_check(dex, row):
    actual = dex.get_cm_method(row["id"])
    return {"id": row["id"], "expectedClass": row["class"], "expectedName": row["name"],
            "actual": actual, "match": actual[0] == row["class"] and actual[1] == row["name"]}


def instructions_for_methods(dex, predicate):
    rows = []
    for cls in dex.get_classes():
        for method in cls.get_methods():
            if not predicate(cls.get_name(), method.get_name()):
                continue
            offset, code = 0, []
            for instruction in method.get_instructions():
                code.append({"byteOffsetInMethod": offset, "opcode": instruction.get_name(),
                             "operands": instruction.get_output()})
                offset += instruction.get_length()
            rows.append({"class": cls.get_name(), "method": method.get_name(),
                         "descriptor": method.get_descriptor(), "instructions": code})
    return rows


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    inventory = json.loads((PRIOR / "apk-research-local-inventory.json").read_text())
    old_static = json.loads((PRIOR / "apk-research-official-static.json").read_text())
    download = json.loads((PRIOR / "apk-research-official-download.json").read_text())["download"]
    input_rows = [{"kind": "official-download", "path": download["path"], "sha256": download["sha256"]}]
    input_rows.extend({"kind": "extension", **row} for row in inventory["matchingApkFiles"])
    old_builds = {row["sha256"]: row for row in inventory["builds"]}
    old_components = {row["path"]: row for row in old_static["components"]}
    rows, parsed_hashes = [], {}
    for record in input_rows:
        path = ROOT / record["path"]
        sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        row = {"path": record["path"], "kind": record["kind"], "sha256": sha256,
               "priorHashMatches": sha256 == record["sha256"]}
        if sha256 in parsed_hashes:
            row["byteIdenticalTo"] = parsed_hashes[sha256]
            rows.append(row)
            continue
        parsed_hashes[sha256] = record["path"]
        apk = APK(str(path))
        row["identity"] = {"validManifest": apk.is_valid_APK(), "package": apk.get_package(),
                           "versionName": apk.get_androidversion_name(), "versionCode": apk.get_androidversion_code(),
                           "label": apk.get_app_name(), "mainActivity": apk.get_main_activity(),
                           "minSdk": apk.get_min_sdk_version(), "targetSdk": apk.get_target_sdk_version()}
        label = "official" if record["kind"] == "official-download" else "extension-" + row["identity"]["versionName"]
        (OUT / f"parser-{label}-manifest.xml").write_bytes(etree.tostring(apk.get_android_manifest_xml(), pretty_print=True, encoding="utf-8"))
        if record["kind"] == "official-download":
            row["priorManifestAttributes"] = check_manifest(apk, old_static["manifest"])
        else:
            row["extensionMetadata"] = [dict(node.attrib) for node in apk.get_android_manifest_xml().findall(".//meta-data")]
        with zipfile.ZipFile(path) as archive:
            row["independentZipMemberListMatches"] = sorted(archive.namelist()) == sorted(apk.get_files())
            row["memberCount"] = len(archive.namelist())
            row["independentExtraction"] = []
            selected_members = ["AndroidManifest.xml", "classes.dex"]
            if record["kind"] == "official-download":
                selected_members += ["resources.arsc", "lib/arm64-v8a/libapp.so"]
            for member in selected_members:
                data = archive.read(member)
                system_data = subprocess.run(["/usr/bin/unzip", "-p", str(path), member],
                                             check=True, capture_output=True).stdout
                row["independentExtraction"].append({"member": member, "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(), "systemUnzipBytesMatch": data == system_data})
            row["dex"] = []
            for member in archive.namelist():
                if not re.fullmatch(r"classes(?:\d+)?\.dex", member):
                    continue
                dex = DEX(archive.read(member))
                strings = list(dex.get_strings())
                dex_row = {"member": member, "stringCount": len(strings),
                           "classCount": len(dex.get_classes()), "methodIdCount": len(dex.get_methods())}
                if record["kind"] == "official-download":
                    dex_row["priorStringCount"] = old_components[member]["strings"]
                    dex_row["stringCountMatches"] = len(strings) == dex_row["priorStringCount"]
                    dex_row["methodChecks"] = [method_check(dex, item) for item in old_static["appNativeMethodNames"] if item["dex"] == member]
                    selected = instructions_for_methods(dex, lambda cls, method:
                        cls.startswith(("Lcom/mymangaviewer/mymangaviewer/MainActivity", "Lcom/mymangaviewer/security/SecurityHandle")))
                    if selected:
                        save(f"parser-{label}-{member}-native-bridge.json", selected)
                else:
                    old = old_builds[sha256]
                    dex_row["priorStringCount"] = old["dexStringCount"]
                    dex_row["stringCountMatches"] = len(strings) == old["dexStringCount"]
                    dex_row["stringIndexChecks"] = [
                        {"index": item["dexStringIndex"], "expectedPrefix": item["value"],
                         "actualLength": len(strings[item["dexStringIndex"]]),
                         "match": strings[item["dexStringIndex"]].startswith(item["value"])}
                        for item in old["protocolStringEvidence"]]
                    dex_row["methodChecks"] = [method_check(dex, item) for item in old["methods"]]
                    relevant_methods = {"searchMangaRequest", "chapterListRequest", "chapterListParse", "chapterListSelector", "pageListRequest", "pageListParse", "getChapterImages", "getImageUrls", "decryptJs", "invoke", "run"}
                    selected = instructions_for_methods(dex, lambda cls, method:
                        ("/multisrc/colamanga/" in cls or "/extension/zh/onemanhua/" in cls)
                        and (method in relevant_methods or method.startswith("pageListParse$")
                             or cls.endswith("/Onemanhua;") and method == "<init>"))
                    save(f"parser-{label}-web-protocol-instructions.json", selected)
                row["dex"].append(dex_row)
        rows.append(row)
        print(json.dumps({"kind": row["kind"], "identity": row["identity"], "dex": len(row["dex"]), "members": row["memberCount"]}, ensure_ascii=False), flush=True)
    failures = []
    def walk(value, path=""):
        if isinstance(value, dict):
            for key, item in value.items():
                location = path + "/" + key
                if key in {"priorHashMatches", "validManifest", "independentZipMemberListMatches", "systemUnzipBytesMatch", "stringCountMatches", "match"} and item is not True:
                    failures.append(location)
                walk(item, location)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, path + "/" + str(index))
    walk(rows)
    result = {"tool": "Androguard", "version": importlib.metadata.version("androguard"),
              "systemExtractor": "/usr/bin/unzip -p", "executedApk": False, "inputFileCount": len(rows),
              "uniqueApkCount": len(parsed_hashes), "entries": rows, "mismatches": failures,
              "scope": "Independent Manifest/DEX parsing and extraction consistency; not a full Dart AOT decompilation or working App protocol."}
    save("parser-crosscheck.json", result)
    print(json.dumps({"uniqueApks": len(parsed_hashes), "mismatches": failures}, ensure_ascii=False))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
