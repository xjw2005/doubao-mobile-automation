#!/usr/bin/env python3
"""获取豆包 AI 回答本身的分享链接。

流程：
1. 滚动到回答底部，找到回答操作栏里的分享按钮。
2. 点击分享按钮，打开分享面板。
3. 点击分享面板左下角的“复制链接”。
4. 将剪贴板内容粘贴到输入框，读取 URL 后清空输入框。

不会点击参考来源，也不会进入来源详情页。

Usage (PowerShell):
    python scripts/test_answer_share_link.py --serial 100.76.50.7:6666
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from mobile_auto_doubao.adb_client import AdbClient
from mobile_auto_doubao.artifacts import set_capture_options
from mobile_auto_doubao.constants import ADB_KEYBOARD_IME, INPUT_ID
from mobile_auto_doubao.doubao_app import center
from mobile_auto_doubao.ui_xml import collect_nodes, extract_urls_from_text, find_nodes


WIN_DEFAULT_ADB = r"C:\Users\Administrator\AppData\Local\Android\Sdk\platform-tools\adb.exe"
ANSWER_SHARE_ID = "com.larus.nova:id/msg_action_share"
FAST_BUTTON_ID = "com.larus.nova:id/fast_button_icon"


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def setup_logger(log_path: Path) -> logging.Logger:
    logger = logging.getLogger("answer_share_link")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(fmt)
    logger.addHandler(stream_handler)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)
    return logger


def resolve_adb(adb_arg: str) -> str:
    if adb_arg and Path(adb_arg).exists():
        return adb_arg
    if Path(WIN_DEFAULT_ADB).exists():
        return WIN_DEFAULT_ADB
    return adb_arg or "adb"


def first_device(adb: str) -> str | None:
    import subprocess

    result = subprocess.run([adb, "devices"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    for line in result.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            return parts[0]
    return None


def dump_xml(adb: AdbClient, output_dir: Path, label: str) -> str:
    xml = adb.dump_xml()
    (output_dir / f"{label}-{stamp()}.xml").write_text(xml, encoding="utf-8")
    return xml


def dump_nodes(adb: AdbClient) -> list[dict]:
    return collect_nodes(adb.dump_xml())


def swipe_down_content(adb: AdbClient) -> None:
    adb.command(["shell", "input", "swipe", "540", "1800", "540", "850", "700"])
    time.sleep(0.9)


def visible_clickable(nodes: list[dict], resource_id: str) -> list[dict]:
    found = []
    for node in find_nodes(nodes, resource_id=resource_id):
        bounds = node.get("parsedBounds")
        if not bounds:
            continue
        if 260 <= bounds["centerY"] <= 1980 and node.get("clickable") == "true":
            found.append(node)
    return found


def tap_fast_bottom_if_present(adb: AdbClient, nodes: list[dict], logger: logging.Logger) -> bool:
    for node in find_nodes(nodes, resource_id=FAST_BUTTON_ID):
        desc = node.get("content_desc", "")
        if "回到底部" not in desc:
            continue
        x, y = center(node)
        logger.info(f"点击回到底部按钮 center=({x},{y})")
        adb.tap(x, y)
        time.sleep(1.0)
        return True
    return False


def scroll_to_answer_share(adb: AdbClient, logger: logging.Logger, max_scrolls: int) -> dict | None:
    for index in range(max_scrolls + 1):
        nodes = dump_nodes(adb)
        share_nodes = visible_clickable(nodes, ANSWER_SHARE_ID)
        if share_nodes:
            target = sorted(share_nodes, key=lambda n: n["parsedBounds"]["centerY"])[-1]
            logger.info(f"找到回答分享按钮: bounds={target.get('bounds')}")
            return target

        if index == 0 and tap_fast_bottom_if_present(adb, nodes, logger):
            continue
        logger.info(f"未看到回答分享按钮，向下滚动查找 ({index + 1}/{max_scrolls})")
        swipe_down_content(adb)
    return None


def click_copy_link(adb: AdbClient, output_dir: Path, logger: logging.Logger) -> dict:
    share_xml = dump_xml(adb, output_dir, "share-sheet")
    nodes = collect_nodes(share_xml)
    copy_nodes = copy_link_targets_from_xml(share_xml)
    if not copy_nodes:
        copy_nodes = find_nodes(nodes, text_contains="复制链接")
    if not copy_nodes:
        copy_nodes = [n for n in nodes if "复制链接" in n.get("content_desc", "") and n.get("parsedBounds")]
    if not copy_nodes:
        return {"ok": False, "error": "copy_link_not_found", "nodeCount": len(nodes)}

    usable = [node for node in copy_nodes if node.get("parsedBounds") and node["parsedBounds"]["centerY"] > 1800]
    if not usable:
        usable = [node for node in copy_nodes if node.get("parsedBounds")]
    target = sorted(usable, key=lambda n: (n["parsedBounds"]["centerY"], n["parsedBounds"]["centerX"]))[0]
    x, y = center(target)
    logger.info(f"点击复制链接 center=({x},{y}) text='{target.get('text', '')}' desc='{target.get('content_desc', '')}'")
    adb.tap(x, y)
    time.sleep(1.5)
    return {"ok": True, "tap": {"x": x, "y": y, "bounds": target.get("bounds", "")}}


def parse_bounds(bounds: str) -> dict[str, int] | None:
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
            if target is not None:
                results.append(node_dict(target))
            else:
                results.append(node_dict(elem))

        next_ancestors = ancestors + [elem]
        for child in reversed(list(elem)):
            stack.append((child, next_ancestors))
    return results


def read_clipboard(adb: AdbClient) -> str:
    result = adb.command(["shell", "dumpsys", "clipboard"], check=False)
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.lower().startswith("text:"):
            return line.split(":", 1)[1].strip().strip('"')
    return ""


def close_share_sheet_if_open(adb: AdbClient, logger: logging.Logger) -> list[dict]:
    nodes = dump_nodes(adb)
    has_copy_link = bool(find_nodes(nodes, text_contains="复制链接"))
    has_input = bool(find_nodes(nodes, resource_id=INPUT_ID))
    if has_copy_link or not has_input:
        logger.info("分享面板仍在前台，按返回关闭")
        adb.keyevent(4)
        time.sleep(0.8)
        nodes = dump_nodes(adb)
    return nodes


def read_clipboard_via_paste(adb: AdbClient, nodes: list[dict], logger: logging.Logger) -> str:
    input_nodes = find_nodes(nodes, resource_id=INPUT_ID)
    if not input_nodes:
        logger.warning("未找到输入框，无法通过粘贴读取剪贴板")
        return ""

    x, y = center(input_nodes[-1])
    logger.info(f"点击输入框并粘贴剪贴板 center=({x},{y})")
    adb.tap(x, y)
    time.sleep(0.3)
    clear_focused_input(adb, logger, verify=False)
    adb.keyevent(279)
    time.sleep(0.8)

    pasted_nodes = dump_nodes(adb)
    texts = [node.get("text", "") for node in find_nodes(pasted_nodes, resource_id=INPUT_ID) if node.get("text", "")]

    clear_focused_input(adb, logger, verify=True, fallback_chars=max(80, len(texts[0]) + 10 if texts else 80))

    return texts[0] if texts else ""


def input_is_empty(text: str) -> bool:
    return not text or text in {"发消息...", "发消息或按住说话..."}


def current_input_texts(adb: AdbClient) -> list[str]:
    nodes = dump_nodes(adb)
    return [node.get("text", "") for node in find_nodes(nodes, resource_id=INPUT_ID)]


def clear_focused_input(
    adb: AdbClient,
    logger: logging.Logger,
    verify: bool,
    fallback_chars: int = 100,
) -> bool:
    try:
        previous_ime = adb.current_ime()
        if previous_ime != ADB_KEYBOARD_IME:
            adb.set_ime(ADB_KEYBOARD_IME)
            time.sleep(0.2)
        adb.broadcast_clear_text()
        time.sleep(0.4)
        if verify:
            texts = current_input_texts(adb)
            if texts and all(input_is_empty(text) for text in texts):
                logger.info("输入框已清空")
                return True
            logger.warning(f"快速清空后仍有输入框文本: {texts}")
            adb.keyevent(123)
            for _ in range(fallback_chars):
                adb.keyevent(67)
            time.sleep(0.3)
            texts = current_input_texts(adb)
            if texts and all(input_is_empty(text) for text in texts):
                logger.info("输入框已通过兜底删除清空")
                return True
            logger.warning(f"兜底删除后输入框仍未清空: {texts}")
            return False
        return True
    except Exception as exc:
        logger.warning(f"清空输入框失败: {exc}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="获取 AI 回答分享链接")
    parser.add_argument("--adb", default=WIN_DEFAULT_ADB, help="Path to adb executable.")
    parser.add_argument("--serial", default=None, help="Device serial. Auto-detected if omitted.")
    parser.add_argument("--output-dir", default="outputs/answer-share", help="Directory to save artifacts.")
    parser.add_argument("--max-scrolls", type=int, default=12, help="Max downward scrolls to find answer share button.")
    parser.add_argument("--wait-share", type=float, default=1.2, help="Wait after tapping share button (s).")
    args = parser.parse_args()

    adb_path = resolve_adb(args.adb)
    serial = args.serial or first_device(adb_path)
    if not serial:
        sys.exit("No connected adb device found.")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = stamp()
    log_path = output_dir / f"answer-share-{ts}.log"
    logger = setup_logger(log_path)
    set_capture_options(screenshots=False, current_focus=False)

    adb = AdbClient(adb=adb_path, serial=serial)
    logger.info("=" * 70)
    logger.info(f"AI 回答分享链接获取 serial={serial}")
    logger.info(f"输出目录: {output_dir}")
    logger.info("=" * 70)

    dump_xml(adb, output_dir, "initial")
    share_node = scroll_to_answer_share(adb, logger, args.max_scrolls)
    if not share_node:
        report = {"status": "failed", "reason": "answer_share_button_not_found", "serial": serial}
        report_path = output_dir / f"answer-share-report-{ts}.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.error(f"未找到回答底部分享按钮，报告: {report_path}")
        sys.exit(2)

    share_x, share_y = center(share_node)
    logger.info(f"点击回答分享按钮 center=({share_x},{share_y})")
    adb.tap(share_x, share_y)
    time.sleep(args.wait_share)

    copy_result = click_copy_link(adb, output_dir, logger)
    if not copy_result.get("ok"):
        report_path = output_dir / f"answer-share-report-{ts}.json"
        report = {"status": "failed", "reason": copy_result.get("error"), "serial": serial, "copy": copy_result}
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.error(f"复制链接失败，报告: {report_path}")
        sys.exit(2)

    direct_clipboard = read_clipboard(adb)
    nodes = close_share_sheet_if_open(adb, logger)
    pasted_text = read_clipboard_via_paste(adb, nodes, logger)
    clipboard_text = pasted_text or direct_clipboard
    urls = extract_urls_from_text(clipboard_text)
    status = "success" if urls else "failed"

    link_path = output_dir / "answer_share_link.txt"
    link_path.write_text(urls[0] if urls else "", encoding="utf-8")

    report = {
        "status": status,
        "serial": serial,
        "startedAt": ts,
        "finishedAt": stamp(),
        "url": urls[0] if urls else "",
        "clipboardText": clipboard_text,
        "directClipboard": direct_clipboard,
        "pastedText": pasted_text,
        "shareTap": {"x": share_x, "y": share_y, "bounds": share_node.get("bounds", "")},
        "copy": copy_result,
        "linkPath": str(link_path),
    }
    report_path = output_dir / f"answer-share-report-{ts}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info("=" * 70)
    logger.info("测试报告")
    logger.info("=" * 70)
    logger.info(f"状态: {status}")
    logger.info(f"分享链接: {urls[0] if urls else '(未获取到)'}")
    logger.info(f"链接文件: {link_path}")
    logger.info(f"报告: {report_path}")
    if status != "success":
        sys.exit(2)


if __name__ == "__main__":
    main()
