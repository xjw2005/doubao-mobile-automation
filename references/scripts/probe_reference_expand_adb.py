import argparse
import json
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree


DEFAULT_ADB = "/mnt/c/Users/Administrator/AppData/Local/Android/Sdk/platform-tools/adb.exe"
REFERENCE_TITLE_ID = "com.larus.nova:id/tv_reference_title"
REFERENCE_TITLE_CONTAINER_ID = "com.larus.nova:id/ll_reference_title"
REFERENCE_CONTENT_ID = "com.larus.nova:id/tv_reference_content"


def run_adb(adb, serial, args, check=True):
    command = [adb]
    if serial:
        command.extend(["-s", serial])
    command.extend(args)
    result = subprocess.run(command, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"adb failed: {command}")
    return result


def first_device(adb):
    result = subprocess.run([adb, "devices"], capture_output=True, text=True, check=True)
    for line in result.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            return parts[0]
    return None


def stamp():
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def parse_bounds(bounds):
    match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
    if not match:
        return None
    left, top, right, bottom = map(int, match.groups())
    return {
        "left": left,
        "top": top,
        "right": right,
        "bottom": bottom,
        "centerX": (left + right) // 2,
        "centerY": (top + bottom) // 2,
    }


def dump_xml(adb, serial):
    remote = "/sdcard/window-reference-expand-probe.xml"
    run_adb(adb, serial, ["shell", "uiautomator", "dump", remote])
    return run_adb(adb, serial, ["shell", "cat", remote]).stdout


def collect_nodes(xml_text):
    nodes = []
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return nodes
    for node in root.iter("node"):
        bounds = node.attrib.get("bounds", "")
        nodes.append({
            "text": node.attrib.get("text", ""),
            "resource_id": node.attrib.get("resource-id", ""),
            "content_desc": node.attrib.get("content-desc", ""),
            "class": node.attrib.get("class", ""),
            "bounds": bounds,
            "clickable": node.attrib.get("clickable", ""),
            "enabled": node.attrib.get("enabled", ""),
            "parsedBounds": parse_bounds(bounds),
        })
    return nodes


def save_state(adb, serial, output_dir, label):
    ts = stamp()
    xml = dump_xml(adb, serial)
    nodes = collect_nodes(xml)
    xml_path = output_dir / f"{label}-{ts}.xml"
    png_path = output_dir / f"{label}-{ts}.png"
    xml_path.write_text(xml, encoding="utf-8")
    screenshot = subprocess.run([adb, "-s", serial, "exec-out", "screencap", "-p"], capture_output=True)
    if screenshot.returncode == 0:
        png_path.write_bytes(screenshot.stdout)
    return {"xml": str(xml_path), "screenshot": str(png_path), "nodes": nodes}


def find_nodes(nodes, *, resource_id=None, text_contains=None):
    found = []
    for node in nodes:
        if resource_id and node.get("resource_id") != resource_id:
            continue
        text = node.get("text", "") + node.get("content_desc", "")
        if text_contains and text_contains not in text:
            continue
        if node.get("parsedBounds"):
            found.append(node)
    return found


def visible_reference_items(nodes):
    items = []
    for node in nodes:
        bounds = node.get("parsedBounds")
        if node.get("resource_id") == REFERENCE_CONTENT_ID and node.get("text") and bounds:
            items.append({
                "title": node["text"],
                "bounds": node["bounds"],
                "centerX": bounds["centerX"],
                "centerY": bounds["centerY"],
            })
    return items


def node_summary(node):
    if not node:
        return None
    bounds = node.get("parsedBounds") or {}
    return {
        "text": node.get("text", ""),
        "resourceId": node.get("resource_id", ""),
        "contentDesc": node.get("content_desc", ""),
        "class": node.get("class", ""),
        "clickable": node.get("clickable", ""),
        "bounds": node.get("bounds", ""),
        "centerX": bounds.get("centerX"),
        "centerY": bounds.get("centerY"),
    }


def reference_title_nodes(nodes):
    direct = find_nodes(nodes, resource_id=REFERENCE_TITLE_ID)
    containers = find_nodes(nodes, resource_id=REFERENCE_TITLE_CONTAINER_ID)
    fallback_text = [
        node for node in nodes
        if node.get("parsedBounds") and "参考" in node.get("text", "") and "资料" in node.get("text", "")
    ]
    return {"direct": direct, "containers": containers, "fallbackText": fallback_text}


def pick_tap_target(groups):
    if groups["containers"]:
        return groups["containers"][0], "reference-title-container"
    if groups["direct"]:
        return groups["direct"][0], "reference-title-text"
    if groups["fallbackText"]:
        return groups["fallbackText"][0], "fallback-reference-text"
    return None, "not-found"


def tap(adb, serial, x, y):
    run_adb(adb, serial, ["shell", "input", "tap", str(x), str(y)])


def main():
    parser = argparse.ArgumentParser(description="Probe whether Doubao reference title can be clicked and expanded using adb UI XML.")
    parser.add_argument("--adb", default=DEFAULT_ADB)
    parser.add_argument("--serial", default=None)
    parser.add_argument("--output-dir", default="outputs/reference-expand-probe")
    parser.add_argument("--wait", type=float, default=1.2)
    parser.add_argument("--dry-run", action="store_true", help="Only locate the reference title; do not tap it.")
    args = parser.parse_args()

    serial = args.serial or first_device(args.adb)
    if not serial:
        raise SystemExit("No connected adb device found.")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    before = save_state(args.adb, serial, output_dir, "before-reference-expand")
    groups = reference_title_nodes(before["nodes"])
    target, method = pick_tap_target(groups)
    before_items = visible_reference_items(before["nodes"])

    result = {
        "serial": serial,
        "dryRun": args.dry_run,
        "status": "not_found",
        "method": method,
        "clicked": False,
        "referenceTitleCandidates": {
            "direct": [node_summary(node) for node in groups["direct"]],
            "containers": [node_summary(node) for node in groups["containers"]],
            "fallbackText": [node_summary(node) for node in groups["fallbackText"]],
        },
        "beforeVisibleReferenceCount": len(before_items),
        "beforeVisibleReferences": before_items,
        "artifacts": {"beforeXml": before["xml"], "beforeScreenshot": before["screenshot"]},
    }

    if not target:
        result_path = output_dir / f"reference-expand-result-{stamp()}.json"
        result["artifacts"]["result"] = str(result_path)
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    result["tapTarget"] = node_summary(target)
    if args.dry_run:
        result["status"] = "located"
    else:
        bounds = target["parsedBounds"]
        tap(args.adb, serial, bounds["centerX"], bounds["centerY"])
        result["clicked"] = True
        time.sleep(args.wait)
        after = save_state(args.adb, serial, output_dir, "after-reference-expand")
        after_items = visible_reference_items(after["nodes"])
        after_groups = reference_title_nodes(after["nodes"])
        result.update({
            "status": "expanded" if after_items else "clicked_no_visible_reference_items",
            "afterVisibleReferenceCount": len(after_items),
            "afterVisibleReferences": after_items,
            "afterReferenceTitleCandidates": {
                "direct": [node_summary(node) for node in after_groups["direct"]],
                "containers": [node_summary(node) for node in after_groups["containers"]],
                "fallbackText": [node_summary(node) for node in after_groups["fallbackText"]],
            },
        })
        result["artifacts"].update({"afterXml": after["xml"], "afterScreenshot": after["screenshot"]})

    result_path = output_dir / f"reference-expand-result-{stamp()}.json"
    result["artifacts"]["result"] = str(result_path)
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
