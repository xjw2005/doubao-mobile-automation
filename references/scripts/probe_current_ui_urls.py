import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree

import uiautomator2 as u2


URL_RE = re.compile(r"https?://[^\s\"'<>，。；、)）\]]+", re.IGNORECASE)


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def safe_get_info(device):
    try:
        return device.info
    except Exception as exc:
        return {"error": str(exc)}


def safe_app_current(device):
    try:
        return device.app_current()
    except Exception as exc:
        return {"error": str(exc)}


def collect_nodes(xml_text: str):
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
        }
        if any(item.values()):
            nodes.append(item)
    return nodes, None


def extract_urls(nodes):
    found = []
    seen = set()
    for index, node in enumerate(nodes):
        haystacks = {
            "text": node.get("text", ""),
            "resource_id": node.get("resource_id", ""),
            "content_desc": node.get("content_desc", ""),
        }
        for field, value in haystacks.items():
            for match in URL_RE.findall(value or ""):
                key = (match, field, index)
                if key in seen:
                    continue
                seen.add(key)
                found.append({
                    "url": match,
                    "field": field,
                    "nodeIndex": index,
                    "node": node,
                })
    return found


def main():
    parser = argparse.ArgumentParser(description="Probe current Android UI XML for real URLs without clicking.")
    parser.add_argument("--serial", default="emulator-5554")
    parser.add_argument("--output-dir", default="outputs")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = now_stamp()

    device = u2.connect(args.serial)
    info = safe_get_info(device)
    current_app = safe_app_current(device)

    xml_text = device.dump_hierarchy(compressed=False)
    nodes, parse_error = collect_nodes(xml_text)
    urls = extract_urls(nodes)

    xml_path = output_dir / f"ui-{stamp}.xml"
    screenshot_path = output_dir / f"screen-{stamp}.png"
    result_path = output_dir / f"url-probe-{stamp}.json"

    xml_path.write_text(xml_text, encoding="utf-8")
    try:
        device.screenshot(str(screenshot_path))
    except Exception as exc:
        screenshot_path = None
        screenshot_error = str(exc)
    else:
        screenshot_error = None

    visible_nodes = [node for node in nodes if node.get("text") or node.get("content_desc")]
    result = {
        "status": "success" if urls else "no_url_found",
        "serial": args.serial,
        "currentApp": current_app,
        "deviceInfo": info,
        "urlCount": len(urls),
        "urls": urls,
        "artifacts": {
            "xml": str(xml_path),
            "screenshot": str(screenshot_path) if screenshot_path else None,
            "screenshotError": screenshot_error,
            "result": str(result_path),
        },
        "nodeCount": len(nodes),
        "visibleNodePreview": visible_nodes[:80],
        "xmlParseError": parse_error,
    }

    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
