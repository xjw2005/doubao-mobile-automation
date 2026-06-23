import time
from pathlib import Path
from xml.etree import ElementTree

from .adb_client import AdbClient
from .constants import ADB_KEYBOARD_IME, INPUT_ID
from .doubao_app import center
from .ui_xml import collect_nodes, extract_urls_from_text, find_nodes


ANSWER_SHARE_ID = "com.larus.nova:id/msg_action_share"
FAST_BUTTON_ID = "com.larus.nova:id/fast_button_icon"


def dump_xml(adb: AdbClient, output_dir: str | Path, label: str) -> str:
    """Dump the current screen XML and persist it under the given label."""
    xml = adb.dump_xml()
    Path(output_dir, f"{label}.xml").write_text(xml, encoding="utf-8")
    return xml


def dump_nodes(adb: AdbClient) -> list[dict]:
    """Return the current UI hierarchy as node dictionaries."""
    return collect_nodes(adb.dump_xml())


def swipe_down_content(adb: AdbClient) -> None:
    """Scroll the main content downward a small amount."""
    adb.scroll_down()
    time.sleep(0.2)


def visible_clickable(nodes: list[dict], resource_id: str) -> list[dict]:
    """Return clickable nodes for a resource id that are on-screen."""
    found = []
    for node in find_nodes(nodes, resource_id=resource_id):
        bounds = node.get("parsedBounds")
        if bounds and 260 <= bounds["centerY"] <= 1980 and node.get("clickable") == "true":
            found.append(node)
    return found


def tap_fast_bottom_if_present(adb: AdbClient, nodes: list[dict]) -> bool:
    """Tap the quick-jump-to-bottom button when it is visible."""
    for node in find_nodes(nodes, resource_id=FAST_BUTTON_ID):
        if "回到底部" in node.get("content_desc", ""):
            x, y = center(node)
            adb.tap(x, y)
            time.sleep(0.25)
            return True
    return False


def scroll_to_answer_share(adb: AdbClient, max_scrolls: int) -> dict | None:
    """Scroll until the answer share button appears, or give up."""
    for index in range(max_scrolls + 1):
        nodes = dump_nodes(adb)
        share_nodes = visible_clickable(nodes, ANSWER_SHARE_ID)
        if share_nodes:
            return sorted(share_nodes, key=lambda n: n["parsedBounds"]["centerY"])[-1]
        if index == 0 and tap_fast_bottom_if_present(adb, nodes):
            continue
        swipe_down_content(adb)
    return None


def parse_bounds(bounds: str) -> dict[str, int] | None:
    """Parse an Android bounds string into coordinates."""
    import re

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


def node_dict(elem: ElementTree.Element) -> dict:
    """Convert an XML element into the node shape used elsewhere."""
    bounds = elem.attrib.get("bounds", "")
    return {
        "text": elem.attrib.get("text", ""),
        "resource_id": elem.attrib.get("resource-id", ""),
        "content_desc": elem.attrib.get("content-desc", ""),
        "class": elem.attrib.get("class", ""),
        "bounds": bounds,
        "clickable": elem.attrib.get("clickable", ""),
        "enabled": elem.attrib.get("enabled", ""),
        "parsedBounds": parse_bounds(bounds),
    }


def copy_link_targets_from_xml(xml_text: str) -> list[dict]:
    """Find likely copy-link tap targets from a raw XML dump."""
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return []
    results: list[dict] = []
    stack: list[tuple[ElementTree.Element, list[ElementTree.Element]]] = [(root, [])]
    while stack:
        elem, ancestors = stack.pop()
        text = elem.attrib.get("text", "") + elem.attrib.get("content-desc", "")
        if "复制链接" in text:
            target = None
            for ancestor in reversed(ancestors):
                bounds = parse_bounds(ancestor.attrib.get("bounds", ""))
                if ancestor.attrib.get("clickable") == "true" and bounds and bounds["centerY"] > 1800:
                    target = ancestor
                    break
            results.append(node_dict(target or elem))
        next_ancestors = ancestors + [elem]
        for child in reversed(list(elem)):
            stack.append((child, next_ancestors))
    return results


def click_copy_link(adb: AdbClient, output_dir: str) -> dict:
    """Tap the copy-link target inside the share sheet."""
    share_xml = dump_xml(adb, output_dir, "answer-share-sheet")
    nodes = collect_nodes(share_xml)
    copy_nodes = copy_link_targets_from_xml(share_xml)
    if not copy_nodes:
        copy_nodes = find_nodes(nodes, text_contains="复制链接")
    if not copy_nodes:
        copy_nodes = [n for n in nodes if "复制链接" in n.get("content_desc", "") and n.get("parsedBounds")]
    usable = [node for node in copy_nodes if node.get("parsedBounds") and node["parsedBounds"]["centerY"] > 1800]
    if not usable:
        usable = [node for node in copy_nodes if node.get("parsedBounds")]
    if not usable:
        return {"ok": False, "error": "copy_link_not_found", "nodeCount": len(nodes)}
    target = sorted(usable, key=lambda n: (n["parsedBounds"]["centerY"], n["parsedBounds"]["centerX"]))[0]
    x, y = center(target)
    adb.tap(x, y)
    time.sleep(0.3)
    return {"ok": True, "tap": {"x": x, "y": y, "bounds": target.get("bounds", "")}}


def read_clipboard(adb: AdbClient) -> str:
    """Read the current clipboard text via dumpsys clipboard."""
    result = adb.command(["shell", "dumpsys", "clipboard"], check=False)
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.lower().startswith("text:"):
            return line.split(":", 1)[1].strip().strip('"')
    return ""


def close_share_sheet_if_open(adb: AdbClient) -> list[dict]:
    """Close the share sheet if it is still covering the screen."""
    nodes = dump_nodes(adb)
    if find_nodes(nodes, text_contains="复制链接") or not find_nodes(nodes, resource_id=INPUT_ID):
        adb.keyevent(4)
        time.sleep(0.2)
        nodes = dump_nodes(adb)
    return nodes


def input_is_empty(text: str) -> bool:
    """Check whether an input field contains only placeholder text."""
    return not text or text in {"发消息...", "发消息或按住说话..."}


def current_input_texts(adb: AdbClient) -> list[str]:
    """Return the current visible text values from the input field."""
    nodes = dump_nodes(adb)
    return [node.get("text", "") for node in find_nodes(nodes, resource_id=INPUT_ID)]


def clear_focused_input(adb: AdbClient, verify: bool, fallback_chars: int = 100) -> bool:
    """Clear the focused input field, optionally verifying that it worked."""
    try:
        previous_ime = adb.current_ime()
        if previous_ime != ADB_KEYBOARD_IME:
            adb.set_ime(ADB_KEYBOARD_IME)
            time.sleep(0.08)
        adb.broadcast_clear_text()
        time.sleep(0.15)
        if verify:
            texts = current_input_texts(adb)
            if texts and all(input_is_empty(text) for text in texts):
                return True
            adb.keyevent(123)
            for _ in range(min(fallback_chars, 40)):
                adb.keyevent(67)
            time.sleep(0.08)
            texts = current_input_texts(adb)
            return bool(texts) and all(input_is_empty(text) for text in texts)
        return True
    except Exception:
        return False


def read_clipboard_via_paste(adb: AdbClient, nodes: list[dict]) -> dict:
    """Paste clipboard contents into the input box and read them back."""
    input_nodes = find_nodes(nodes, resource_id=INPUT_ID)
    if not input_nodes:
        return {"text": "", "urls": [], "clearOk": False, "error": "input_not_found"}
    x, y = center(input_nodes[-1])
    adb.tap(x, y)
    time.sleep(0.1)
    clear_focused_input(adb, verify=False)
    adb.keyevent(279)
    time.sleep(0.2)
    pasted_nodes = dump_nodes(adb)
    texts = [node.get("text", "") for node in find_nodes(pasted_nodes, resource_id=INPUT_ID) if node.get("text", "")]
    text = texts[0] if texts else ""
    clear_ok = clear_focused_input(adb, verify=True, fallback_chars=max(80, len(text) + 10 if text else 80))
    return {"text": text, "urls": extract_urls_from_text(text), "clearOk": clear_ok}


def extract_answer_share_link(adb: AdbClient, options: dict, output_dir: str) -> dict:
    """Open the answer share sheet and extract the share URL from it."""
    max_scrolls = int(options.get("answerShareMaxScrolls", 8))
    wait_share = float(options.get("answerShareWaitSeconds", 0.5))
    dump_xml(adb, output_dir, "answer-share-initial")
    share_node = scroll_to_answer_share(adb, max_scrolls)
    if not share_node:
        return {"status": "failed", "url": "", "error": "answer_share_button_not_found"}
    share_x, share_y = center(share_node)
    adb.tap(share_x, share_y)
    time.sleep(wait_share)
    copy = click_copy_link(adb, output_dir)
    if not copy.get("ok"):
        return {"status": "failed", "url": "", "error": copy.get("error") or "copy_link_failed", "shareTap": {"x": share_x, "y": share_y}, "copy": copy}
    direct_clipboard = read_clipboard(adb)
    nodes = close_share_sheet_if_open(adb)
    paste = read_clipboard_via_paste(adb, nodes)
    clipboard_text = paste.get("text") or direct_clipboard
    urls = extract_urls_from_text(clipboard_text)
    return {
        "status": "success" if urls and paste.get("clearOk") else ("partial" if urls else "failed"),
        "url": urls[0] if urls else "",
        "error": None if urls else "no_url_after_copy",
        "clipboardText": clipboard_text,
        "directClipboard": direct_clipboard,
        "paste": paste,
        "shareTap": {"x": share_x, "y": share_y, "bounds": share_node.get("bounds", "")},
        "copy": copy,
    }
