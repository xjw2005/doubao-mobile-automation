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
INPUT_ID = "com.larus.nova:id/input_text"
SHARE_BUTTON_ID = "com.larus.nova:id/btn_share"


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
    remote = "/sdcard/window-source-share-copy.xml"
    run_adb(adb, serial, ["shell", "uiautomator", "dump", remote])
    return run_adb(adb, serial, ["shell", "cat", remote]).stdout


def collect_nodes(xml_text):
    nodes = []
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return nodes
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
        nodes.append(item)
    return nodes


def extract_urls_from_text(text):
    return URL_RE.findall(text or "")


def extract_urls_from_nodes(nodes):
    urls = []
    seen = set()
    for node in nodes:
        for field in ("text", "content_desc", "resource_id"):
            for url in extract_urls_from_text(node.get(field, "")):
                if url not in seen:
                    seen.add(url)
                    urls.append(url)
    return urls


def find_nodes(nodes, *, resource_id=None, text_contains=None):
    found = []
    for node in nodes:
        if resource_id and node.get("resource_id") != resource_id:
            continue
        if text_contains and text_contains not in node.get("text", ""):
            continue
        if node.get("parsedBounds"):
            found.append(node)
    return found


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


def tap(adb, serial, x, y):
    run_adb(adb, serial, ["shell", "input", "tap", str(x), str(y)])


def keyevent(adb, serial, code):
    run_adb(adb, serial, ["shell", "input", "keyevent", str(code)])


def clear_focused_text(adb, serial):
    keyevent(adb, serial, 123)
    for _ in range(260):
        keyevent(adb, serial, 67)
    keyevent(adb, serial, 122)
    for _ in range(260):
        keyevent(adb, serial, 112)


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
    return {
        "xml": str(xml_path),
        "screenshot": str(png_path),
        "nodes": nodes,
        "urls": extract_urls_from_nodes(nodes),
    }


def clear_input_if_visible(adb, serial, nodes):
    input_nodes = find_nodes(nodes, resource_id=INPUT_ID)
    if not input_nodes:
        return False
    bounds = input_nodes[-1]["parsedBounds"]
    tap(adb, serial, bounds["centerX"], bounds["centerY"])
    time.sleep(0.3)
    clear_focused_text(adb, serial)
    return True


def paste_clipboard_into_input_and_read(adb, serial, output_dir, label):
    state = save_state(adb, serial, output_dir, f"{label}-before-paste")
    input_nodes = find_nodes(state["nodes"], resource_id=INPUT_ID)
    if not input_nodes:
        return {"urls": [], "error": "input_not_found", "state": state}
    bounds = input_nodes[-1]["parsedBounds"]
    tap(adb, serial, bounds["centerX"], bounds["centerY"])
    time.sleep(0.3)
    keyevent(adb, serial, 279)
    time.sleep(0.8)
    pasted = save_state(adb, serial, output_dir, f"{label}-after-paste")
    urls = []
    for node in find_nodes(pasted["nodes"], resource_id=INPUT_ID):
        urls.extend(extract_urls_from_text(node.get("text", "")))
    clear_input_if_visible(adb, serial, pasted["nodes"])
    return {"urls": list(dict.fromkeys(urls)), "state": pasted}


def main():
    parser = argparse.ArgumentParser(description="Click Doubao source items, copy source share links, and extract real URLs by paste-read.")
    parser.add_argument("--adb", default=DEFAULT_ADB)
    parser.add_argument("--serial", default=None)
    parser.add_argument("--output-dir", default="outputs/source-share-copy")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--wait-source", type=float, default=4.0)
    parser.add_argument("--wait-share", type=float, default=1.5)
    args = parser.parse_args()

    serial = args.serial or first_device(args.adb)
    if not serial:
        raise SystemExit("No connected adb device found.")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    started = stamp()
    initial = save_state(args.adb, serial, output_dir, "initial-list")
    clear_input_if_visible(args.adb, serial, initial["nodes"])
    initial = save_state(args.adb, serial, output_dir, "initial-list-after-clear")
    items = visible_source_items(initial["nodes"])
    result = {
        "serial": serial,
        "startedAt": started,
        "visibleSourceCount": len(items),
        "attemptedCount": 0,
        "successUrlCount": 0,
        "items": [],
    }

    for index, item in enumerate(items[: args.limit], start=1):
        item_result = {
            "index": index,
            "title": item["title"],
            "sourceTap": {"x": item["centerX"], "y": item["centerY"], "bounds": item["bounds"]},
            "urls": [],
            "errors": [],
        }
        tap(args.adb, serial, item["centerX"], item["centerY"])
        time.sleep(args.wait_source)
        source_page = save_state(args.adb, serial, output_dir, f"item-{index}-source-page")
        share_nodes = find_nodes(source_page["nodes"], resource_id=SHARE_BUTTON_ID)
        if not share_nodes:
            item_result["errors"].append("share_button_not_found")
            keyevent(args.adb, serial, 4)
            time.sleep(1.0)
            result["items"].append(item_result)
            continue
        share_bounds = share_nodes[-1]["parsedBounds"]
        tap(args.adb, serial, share_bounds["centerX"], share_bounds["centerY"])
        time.sleep(args.wait_share)
        share_sheet = save_state(args.adb, serial, output_dir, f"item-{index}-share-sheet")
        copy_nodes = find_nodes(share_sheet["nodes"], text_contains="复制链接")
        if not copy_nodes:
            item_result["errors"].append("copy_link_not_found")
            keyevent(args.adb, serial, 4)
            time.sleep(0.5)
            keyevent(args.adb, serial, 4)
            time.sleep(1.0)
            result["items"].append(item_result)
            continue
        copy_bounds = copy_nodes[0]["parsedBounds"]
        tap(args.adb, serial, copy_bounds["centerX"], copy_bounds["centerY"])
        time.sleep(1.0)
        keyevent(args.adb, serial, 4)
        time.sleep(1.0)
        paste = paste_clipboard_into_input_and_read(args.adb, serial, output_dir, f"item-{index}")
        item_result["urls"] = paste.get("urls", [])
        if paste.get("error"):
            item_result["errors"].append(paste["error"])
        item_result["artifacts"] = {
            "sourcePageXml": source_page["xml"],
            "shareSheetXml": share_sheet["xml"],
            "pasteStateXml": paste.get("state", {}).get("xml"),
        }
        result["attemptedCount"] += 1
        result["successUrlCount"] += len(item_result["urls"])
        result["items"].append(item_result)

    result["finishedAt"] = stamp()
    result["status"] = "success" if result["successUrlCount"] else "no_url_found"
    result_path = output_dir / f"source-share-copy-{started}.json"
    result["resultPath"] = str(result_path)
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
