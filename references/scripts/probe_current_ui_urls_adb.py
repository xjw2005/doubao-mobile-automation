import argparse
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree


DEFAULT_ADB = "/mnt/c/Users/Administrator/AppData/Local/Android/Sdk/platform-tools/adb.exe"
URL_RE = re.compile(r"https?://[^\s\"'<>，。；、)）\]]+", re.IGNORECASE)


def run_adb(adb, serial, args, check=True):
    command = [adb]
    if serial:
        command.extend(["-s", serial])
    command.extend(args)
    result = subprocess.run(command, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"adb failed: {command}")
    return result


def stamp():
    return datetime.now().strftime("%Y%m%d-%H%M%S")


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
        }
        if any(item.values()):
            nodes.append(item)
    return nodes, None


def extract_urls(nodes):
    urls = []
    seen = set()
    for index, node in enumerate(nodes):
        for field in ("text", "resource_id", "content_desc"):
            value = node.get(field, "")
            for url in URL_RE.findall(value or ""):
                key = (url, field, index)
                if key in seen:
                    continue
                seen.add(key)
                urls.append({
                    "url": url,
                    "field": field,
                    "nodeIndex": index,
                    "node": node,
                })
    return urls


def first_device(adb):
    result = subprocess.run([adb, "devices"], capture_output=True, text=True, check=True)
    for line in result.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            return parts[0]
    return None


def main():
    parser = argparse.ArgumentParser(description="Probe current Android UI XML for real URLs using only adb.")
    parser.add_argument("--adb", default=DEFAULT_ADB)
    parser.add_argument("--serial", default=None)
    parser.add_argument("--output-dir", default="outputs")
    args = parser.parse_args()

    adb = args.adb
    serial = args.serial or first_device(adb)
    if not serial:
        raise SystemExit("No connected adb device found.")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = stamp()

    xml_remote = "/sdcard/window-url-probe.xml"
    run_adb(adb, serial, ["shell", "uiautomator", "dump", xml_remote])
    xml_text = run_adb(adb, serial, ["shell", "cat", xml_remote]).stdout
    current_app = run_adb(adb, serial, ["shell", "dumpsys", "window", "|", "grep", "mCurrentFocus"], check=False).stdout.strip()

    xml_path = output_dir / f"ui-{ts}.xml"
    png_path = output_dir / f"screen-{ts}.png"
    result_path = output_dir / f"url-probe-{ts}.json"

    xml_path.write_text(xml_text, encoding="utf-8")
    with png_path.open("wb") as file:
        screenshot = subprocess.run([adb, "-s", serial, "exec-out", "screencap", "-p"], capture_output=True)
        if screenshot.returncode == 0:
            file.write(screenshot.stdout)

    nodes, parse_error = collect_nodes(xml_text)
    urls = extract_urls(nodes)
    visible_nodes = [node for node in nodes if node.get("text") or node.get("content_desc")]

    result = {
        "status": "success" if urls else "no_url_found",
        "serial": serial,
        "currentAppRaw": current_app,
        "urlCount": len(urls),
        "urls": urls,
        "nodeCount": len(nodes),
        "visibleNodePreview": visible_nodes[:120],
        "xmlParseError": parse_error,
        "artifacts": {
            "xml": str(xml_path),
            "screenshot": str(png_path),
            "result": str(result_path),
        },
    }
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
