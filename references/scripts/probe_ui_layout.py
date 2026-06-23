"""Probe current Android UI XML layout for element location analysis.

Usage (Windows PowerShell):
    python scripts/probe_ui_layout.py
    python scripts/probe_ui_layout.py --adb "C:\\Users\\Administrator\\AppData\\Local\\Android\\Sdk\\platform-tools\\adb.exe"
    python scripts/probe_ui_layout.py --serial <device-serial> --output-dir outputs/ui-probe

The script:
  1. Dumps the current UI hierarchy XML via `uiautomator dump`.
  2. Saves the raw XML, a screenshot, a structured JSON of all nodes, and a
     readable indented tree that highlights clickable / reference-related nodes.
  3. Prints a compact summary (clickable candidates, reference-related nodes,
     nodes containing key text) to stdout so you can quickly decide what to tap.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree


# Windows default (matches the WSL path used elsewhere in the repo).
DEFAULT_ADB = r"C:\Users\Administrator\AppData\Local\Android\Sdk\platform-tools\adb.exe"

# Resource ids / text keywords relevant to the "expand references" feature.
REFERENCE_RELATED_IDS = {
    "com.larus.nova:id/tv_reference_title",
    "com.larus.nova:id/ll_reference_title",
    "com.larus.nova:id/tv_reference_content",
    "com.larus.nova:id/ll_reference_content",
    "com.larus.nova:id/recycler_reference",
    "com.larus.nova:id/cl_reference",
}
REFERENCE_TEXT_KEYWORDS = ("参考", "资料", "来源", "引用", "展开", "查看更多")

BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def resolve_adb(adb_arg: str) -> str:
    """Return a usable adb executable path."""
    if adb_arg and Path(adb_arg).exists():
        return adb_arg
    # Fallback: rely on PATH.
    result = subprocess.run(["where", "adb"], capture_output=True, text=True)
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            if line.strip() and Path(line.strip()).exists():
                return line.strip()
    if Path(DEFAULT_ADB).exists():
        return DEFAULT_ADB
    return adb_arg or "adb"


def first_device(adb: str) -> str | None:
    result = subprocess.run([adb, "devices"], capture_output=True, text=True)
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            return parts[0]
    return None


def run_adb(adb: str, serial: str | None, args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    command = [adb]
    if serial:
        command.extend(["-s", serial])
    command.extend(args)
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"adb failed: {command}")
    return result


def parse_bounds(bounds: str | None) -> dict | None:
    match = BOUNDS_RE.fullmatch(bounds or "")
    if not match:
        return None
    left, top, right, bottom = map(int, match.groups())
    return {
        "left": left,
        "top": top,
        "right": right,
        "bottom": bottom,
        "width": right - left,
        "height": bottom - top,
        "centerX": (left + right) // 2,
        "centerY": (top + bottom) // 2,
    }


def dump_ui_xml(adb: str, serial: str | None) -> str:
    remote = "/sdcard/ui-layout-probe.xml"
    # Retry a couple of times: uiautomator dump occasionally fails on busy UIs.
    last_error: str | None = None
    for _ in range(3):
        try:
            run_adb(adb, serial, ["shell", "uiautomator", "dump", remote])
            xml = run_adb(adb, serial, ["shell", "cat", remote]).stdout
            if xml and "<hierarchy" in xml:
                return xml
            last_error = "uiautomator dump did not produce valid hierarchy xml"
        except Exception as exc:
            last_error = str(exc)
        # Try reading stale file as a last resort.
        cat = run_adb(adb, serial, ["shell", "cat", remote], check=False)
        if "<hierarchy" in (cat.stdout or ""):
            return cat.stdout
    raise RuntimeError(f"uiautomator dump failed: {last_error}")


def take_screenshot(adb: str, serial: str | None, path: Path) -> bool:
    result = subprocess.run(
        [adb, *([ "-s", serial] if serial else []), "exec-out", "screencap", "-p"],
        capture_output=True,
    )
    if result.returncode != 0 or not result.stdout:
        return False
    path.write_bytes(result.stdout)
    return True


def collect_nodes_with_hierarchy(xml_text: str) -> list[dict]:
    """Parse the hierarchy XML and attach depth + ancestor path to each node."""
    nodes: list[dict] = []
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        raise RuntimeError(f"XML parse error: {exc}") from exc

    def walk(element, depth: int, path: list[str]):
        if element.tag != "node":
            for child in element:
                walk(child, depth, path)
            return
        bounds = element.attrib.get("bounds", "")
        resource_id = element.attrib.get("resource-id", "")
        node = {
            "index": len(nodes),
            "depth": depth,
            "parentPath": "/".join(path) if path else "/",
            "text": element.attrib.get("text", ""),
            "resource_id": resource_id,
            "content_desc": element.attrib.get("content-desc", ""),
            "class": element.attrib.get("class", ""),
            "package": element.attrib.get("package", ""),
            "bounds": bounds,
            "clickable": element.attrib.get("clickable", "false"),
            "enabled": element.attrib.get("enabled", "true"),
            "focusable": element.attrib.get("focusable", "false"),
            "scrollable": element.attrib.get("scrollable", "false"),
            "long_clickable": element.attrib.get("long-clickable", "false"),
            "password": element.attrib.get("password", "false"),
            "selected": element.attrib.get("selected", "false"),
            "checked": element.attrib.get("checked", "false"),
            "parsedBounds": parse_bounds(bounds),
        }
        nodes.append(node)
        # Build a short label for the ancestor path.
        label = resource_id.split(":id/")[-1] if resource_id else (node["text"][:12] or node["class"].split(".")[-1])
        path.append(label)
        for child in element:
            walk(child, depth + 1, path)
        path.pop()

    walk(root, 0, [])
    return nodes


def is_reference_related(node: dict) -> bool:
    rid = node.get("resource_id", "")
    if rid in REFERENCE_RELATED_IDS:
        return True
    combined = f"{node.get('text', '')} {node.get('content_desc', '')}"
    return any(keyword in combined for keyword in REFERENCE_TEXT_KEYWORDS)


def short_class(cls: str) -> str:
    return cls.rsplit(".", 1)[-1] if cls else ""


def build_readable_tree(nodes: list[dict]) -> str:
    """Produce an indented text tree emphasizing clickable / reference nodes."""
    lines: list[str] = []
    for node in nodes:
        depth = node["depth"]
        indent = "  " * depth
        rid = node["resource_id"].split(":id/")[-1] if node["resource_id"] else ""
        text = (node["text"] or "").replace("\n", " ")
        if len(text) > 40:
            text = text[:37] + "..."
        desc = node["content_desc"]
        if len(desc) > 20:
            desc = desc[:17] + "..."
        cls = short_class(node["class"])
        bounds = node["parsedBounds"] or {}
        size = f"{bounds.get('width', 0)}x{bounds.get('height', 0)}" if bounds else "?"
        flags = []
        if node["clickable"] == "true":
            flags.append("CLICK")
        if node["scrollable"] == "true":
            flags.append("SCROLL")
        if node["long_clickable"] == "true":
            flags.append("LONG")
        ref_marker = " [REF]" if is_reference_related(node) else ""
        flag_str = f" <{'|'.join(flags)}>" if flags else ""
        parts = [f"[{node['index']:>3}]"]
        if rid:
            parts.append(f"id={rid}")
        if text:
            parts.append(f"text={text!r}")
        if desc:
            parts.append(f"desc={desc!r}")
        parts.append(f"({cls} {size})")
        line = f"{indent}{' '.join(parts)}{flag_str}{ref_marker}"
        lines.append(line)
    return "\n".join(lines)


def build_candidates(nodes: list[dict]) -> dict:
    """Group nodes that are likely tap targets for the reference-expand fix."""
    clickable = [n for n in nodes if n["clickable"] == "true" and n["parsedBounds"]]
    reference_related = [n for n in nodes if is_reference_related(n) and n["parsedBounds"]]
    # Reference-related but NOT clickable (the likely root cause: clicking the
    # text node instead of its clickable ancestor/container).
    reference_not_clickable = [n for n in reference_related if n["clickable"] != "true"]

    def slim(n: dict) -> dict:
        b = n["parsedBounds"] or {}
        return {
            "index": n["index"],
            "depth": n["depth"],
            "resourceId": n["resource_id"],
            "text": n["text"],
            "contentDesc": n["content_desc"],
            "class": n["class"],
            "clickable": n["clickable"],
            "bounds": n["bounds"],
            "centerX": b.get("centerX"),
            "centerY": b.get("centerY"),
            "width": b.get("width"),
            "height": b.get("height"),
            "parentPath": n["parentPath"],
        }

    return {
        "clickableCount": len(clickable),
        "referenceRelatedCount": len(reference_related),
        "referenceNotClickableCount": len(reference_not_clickable),
        "clickable": [slim(n) for n in clickable],
        "referenceRelated": [slim(n) for n in reference_related],
        "referenceNotClickable": [slim(n) for n in reference_not_clickable],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe current Android UI XML layout for element location analysis.")
    parser.add_argument("--adb", default=DEFAULT_ADB, help="Path to adb executable.")
    parser.add_argument("--serial", default=None, help="Device serial. Auto-detected if omitted.")
    parser.add_argument("--output-dir", default="outputs/ui-layout-probe", help="Directory to save artifacts.")
    args = parser.parse_args()

    adb = resolve_adb(args.adb)
    serial = args.serial or first_device(adb)
    if not serial:
        sys.exit("No connected adb device found.")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = stamp()

    print(f"[probe] adb={adb} serial={serial}")
    print("[probe] dumping UI hierarchy ...")
    xml_text = dump_ui_xml(adb, serial)

    nodes = collect_nodes_with_hierarchy(xml_text)
    candidates = build_candidates(nodes)
    tree_text = build_readable_tree(nodes)

    # Save artifacts.
    xml_path = output_dir / f"ui-{ts}.xml"
    png_path = output_dir / f"screen-{ts}.png"
    tree_path = output_dir / f"tree-{ts}.txt"
    nodes_path = output_dir / f"nodes-{ts}.json"
    summary_path = output_dir / f"summary-{ts}.json"

    xml_path.write_text(xml_text, encoding="utf-8")
    tree_path.write_text(tree_text, encoding="utf-8")
    nodes_path.write_text(json.dumps(nodes, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[probe] saving screenshot ...")
    screenshot_ok = take_screenshot(adb, serial, png_path)

    current_focus = run_adb(adb, serial, ["shell", "dumpsys", "window"], check=False).stdout
    focus_lines = [line.strip() for line in current_focus.splitlines() if "mCurrentFocus" in line or "mFocusedApp" in line]

    summary = {
        "status": "success",
        "serial": serial,
        "adb": adb,
        "timestamp": ts,
        "nodeCount": len(nodes),
        "currentFocus": "\n".join(focus_lines),
        "screenshotSaved": screenshot_ok,
        "candidates": candidates,
        "artifacts": {
            "xml": str(xml_path),
            "screenshot": str(png_path) if screenshot_ok else None,
            "tree": str(tree_path),
            "nodes": str(nodes_path),
            "summary": str(summary_path),
        },
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    # Console output: compact + actionable.
    print("\n" + "=" * 80)
    print(f"UI LAYOUT PROBE  |  nodes={len(nodes)}  clickable={candidates['clickableCount']}  "
          f"reference-related={candidates['referenceRelatedCount']}  "
          f"reference-not-clickable={candidates['referenceNotClickableCount']}")
    print("=" * 80)
    if focus_lines:
        print("current focus:")
        for line in focus_lines:
            print(f"  {line}")

    print("\n--- Reference-related nodes (the expand-references targets) ---")
    if not candidates["referenceRelated"]:
        print("  (none found — the reference panel may not be visible on this screen)")
    for n in candidates["referenceRelated"]:
        click_flag = "CLICKABLE" if n["clickable"] == "true" else "NOT-clickable"
        print(f"  [#{n['index']} d{n['depth']}] {click_flag}  id={n['resourceId'] or '-'}  "
              f"text={n['text']!r}  center=({n['centerX']},{n['centerY']})  "
              f"size={n['width']}x{n['height']}")
        print(f"      parentPath: {n['parentPath']}")

    print("\n--- Reference-related but NOT clickable (likely need to tap an ancestor) ---")
    if not candidates["referenceNotClickable"]:
        print("  (none)")
    for n in candidates["referenceNotClickable"]:
        # Find the nearest clickable ancestor by walking up the parent path.
        print(f"  [#{n['index']}] id={n['resourceId'] or '-'}  text={n['text']!r}  bounds={n['bounds']}")
        print(f"      parentPath: {n['parentPath']}")

    print("\n--- All clickable nodes (top 40 by depth) ---")
    clickable_sorted = sorted(candidates["clickable"], key=lambda n: (n["depth"], n["index"]))
    for n in clickable_sorted[:40]:
        rid = (n["resourceId"].split(":id/")[-1]) if n["resourceId"] else "-"
        text = (n["text"] or "")[:30]
        print(f"  [#{n['index']} d{n['depth']}] id={rid}  text={text!r}  "
              f"center=({n['centerX']},{n['centerY']})  size={n['width']}x{n['height']}")

    print("\n" + "=" * 80)
    print("Artifacts saved:")
    print(f"  raw xml     : {xml_path}")
    print(f"  screenshot  : {png_path if screenshot_ok else '(failed)'}")
    print(f"  readable tree: {tree_path}")
    print(f"  nodes json  : {nodes_path}")
    print(f"  summary json: {summary_path}")
    print("=" * 80)


if __name__ == "__main__":
    main()
