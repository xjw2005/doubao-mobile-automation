import argparse
import json
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree


DEFAULT_ADB = "/mnt/c/Users/Administrator/AppData/Local/Android/Sdk/platform-tools/adb.exe"
URL_RE = re.compile(r"https?://[^\s\"'<>，。；、)）\]]+", re.IGNORECASE)
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
    remote = "/sdcard/window-source-url-probe.xml"
    run_adb(adb, serial, ["shell", "uiautomator", "dump", remote])
    return run_adb(adb, serial, ["shell", "cat", remote]).stdout


def collect_nodes(xml_text):
    nodes = []
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        return nodes, str(exc)
    for node in root.iter("node"):
        item = {
            "text": node.attrib.get("text", ""),
            "resource_id": node.attrib.get("resource-id", ""),
            "content_desc": node.attrib.get("content-desc", ""),
            "class": node.attrib.get("class", ""),
            "bounds": node.attrib.get("bounds", ""),
            "clickable": node.attrib.get("clickable", ""),
            "enabled": node.attrib.get("enabled", ""),
            "parsedBounds": parse_bounds(node.attrib.get("bounds", "")),
        }
        if any(value for key, value in item.items() if key != "parsedBounds"):
            nodes.append(item)
    return nodes, None


def extract_urls(nodes):
    urls = []
    seen = set()
    for index, node in enumerate(nodes):
        for field in ("text", "resource_id", "content_desc"):
            for url in URL_RE.findall(node.get(field, "") or ""):
                if url in seen:
                    continue
                seen.add(url)
                urls.append({"url": url, "field": field, "nodeIndex": index, "node": node})
    return urls


def visible_source_items(nodes):
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


def save_state(adb, serial, output_dir, label):
    ts = stamp()
    xml = dump_xml(adb, serial)
    nodes, parse_error = collect_nodes(xml)
    urls = extract_urls(nodes)
    xml_path = output_dir / f"{label}-{ts}.xml"
    png_path = output_dir / f"{label}-{ts}.png"
    xml_path.write_text(xml, encoding="utf-8")
    screenshot = subprocess.run([adb, "-s", serial, "exec-out", "screencap", "-p"], capture_output=True)
    if screenshot.returncode == 0:
        png_path.write_bytes(screenshot.stdout)
    return {
        "xml": str(xml_path),
        "screenshot": str(png_path),
        "nodeCount": len(nodes),
        "urls": urls,
        "xmlParseError": parse_error,
        "visibleTextPreview": [
            {"text": node.get("text", ""), "resource_id": node.get("resource_id", ""), "bounds": node.get("bounds", "")}
            for node in nodes if node.get("text")
        ][:80],
    }


def main():
    parser = argparse.ArgumentParser(description="Click visible Doubao mobile source items and probe opened pages for URLs.")
    parser.add_argument("--adb", default=DEFAULT_ADB)
    parser.add_argument("--serial", default=None)
    parser.add_argument("--output-dir", default="outputs/source-click-probe")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--wait-seconds", type=float, default=4.0)
    args = parser.parse_args()

    serial = args.serial or first_device(args.adb)
    if not serial:
        raise SystemExit("No connected adb device found.")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    started = stamp()

    initial_xml = dump_xml(args.adb, serial)
    initial_nodes, parse_error = collect_nodes(initial_xml)
    items = visible_source_items(initial_nodes)
    result = {
        "serial": serial,
        "startedAt": started,
        "status": "running",
        "initialParseError": parse_error,
        "visibleSourceCount": len(items),
        "attemptedCount": 0,
        "successUrlCount": 0,
        "items": [],
    }

    for index, item in enumerate(items[: args.limit], start=1):
        before = save_state(args.adb, serial, output_dir, f"item-{index}-before")
        run_adb(args.adb, serial, ["shell", "input", "tap", str(item["centerX"]), str(item["centerY"])])
        time.sleep(args.wait_seconds)
        after = save_state(args.adb, serial, output_dir, f"item-{index}-after")
        run_adb(args.adb, serial, ["shell", "input", "keyevent", "4"])
        time.sleep(1.5)
        returned = save_state(args.adb, serial, output_dir, f"item-{index}-returned")

        urls = after["urls"]
        result["items"].append({
            "index": index,
            "title": item["title"],
            "tap": {"x": item["centerX"], "y": item["centerY"], "bounds": item["bounds"]},
            "urlCount": len(urls),
            "urls": urls,
            "artifacts": {
                "before": before,
                "after": after,
                "returned": returned,
            },
        })
        result["attemptedCount"] += 1
        result["successUrlCount"] += len(urls)

    result["finishedAt"] = stamp()
    result["status"] = "success" if result["successUrlCount"] else "no_url_found"
    result_path = output_dir / f"source-click-probe-{started}.json"
    result["resultPath"] = str(result_path)
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
