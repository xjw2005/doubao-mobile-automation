#!/usr/bin/env python3
"""抓取豆包专家模式回答内容。

只处理两个内容区：
1. 专家模式的思考过程
2. AI 的正式回答

脚本不会点击参考来源、分享、复制链接，也不会进入来源详情页。

Usage (PowerShell):
    python scripts/test_expert_mode_full_answer.py --serial 100.76.50.7:6666
    python scripts/test_expert_mode_full_answer.py --serial <serial> --output-dir outputs/expert-answer
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
from mobile_auto_doubao.constants import (
    INPUT_ID,
    REFERENCE_CONTENT_ID,
    REFERENCE_TITLE_CLICKABLE_ID,
)
from mobile_auto_doubao.doubao_app import center
from mobile_auto_doubao.ui_xml import collect_nodes, find_nodes


WIN_DEFAULT_ADB = r"C:\Users\Administrator\AppData\Local\Android\Sdk\platform-tools\adb.exe"

CONTENT_VIEW_ID = "com.larus.nova:id/content_view"
THINK_BLOCK_ID = "com.larus.nova:id/sub_deep_think_block_list"
REFERENCE_TITLE_IDS = {
    "com.larus.nova:id/ll_reference_title_wrapper",
    "com.larus.nova:id/ll_reference_title_bg",
    "com.larus.nova:id/ll_reference_title",
    "com.larus.nova:id/tv_reference_title",
}

IGNORE_RESOURCE_PARTS = (
    "avatar",
    "btn_share",
    "button",
    "input",
    "iv_",
    "reference_content",
    "reference_index",
    "search_reference",
    "tv_reference_content",
)


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def setup_logger(log_path: Path) -> logging.Logger:
    logger = logging.getLogger("expert_answer")
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


def swipe_up_content(adb: AdbClient) -> None:
    adb.command(["shell", "input", "swipe", "540", "850", "540", "1800", "700"])
    time.sleep(0.9)


def tap_if_visible(adb: AdbClient, node: dict, logger: logging.Logger, label: str) -> bool:
    bounds = node.get("parsedBounds")
    if not bounds:
        return False
    x, y = center(node)
    if y < 260 or y > 1980:
        return False
    logger.info(f"点击{label}: center=({x},{y}) text='{node.get('text', '')[:40]}'")
    adb.tap(x, y)
    time.sleep(1.0)
    return True


def click_completed_thinking_if_needed(adb: AdbClient, logger: logging.Logger) -> bool:
    nodes = dump_nodes(adb)
    for keyword in ("已完成思考", "完成思考", "思考完成"):
        for node in nodes:
            text = node.get("text", "") + node.get("content_desc", "")
            if keyword in text and node.get("clickable") == "true":
                return tap_if_visible(adb, node, logger, "已完成思考入口")
    logger.info("未发现单独的'已完成思考'入口，继续检查思考块是否已展开")
    return False


def thinking_block_visible(nodes: list[dict]) -> bool:
    return bool(find_nodes(nodes, resource_id=THINK_BLOCK_ID))


def expand_thinking_block(adb: AdbClient, logger: logging.Logger) -> dict:
    """展开思考过程；不展开参考来源列表。"""
    clicked_completed = click_completed_thinking_if_needed(adb, logger)
    nodes = dump_nodes(adb)
    if thinking_block_visible(nodes):
        return {"ok": True, "method": "already_visible", "clickedCompleted": clicked_completed}

    title_nodes = find_nodes(nodes, resource_id=REFERENCE_TITLE_CLICKABLE_ID)
    for node in title_nodes:
        if tap_if_visible(adb, node, logger, "思考标题"):
            after_nodes = dump_nodes(adb)
            if thinking_block_visible(after_nodes):
                return {"ok": True, "method": "title_click", "clickedCompleted": clicked_completed}

    return {"ok": False, "method": "not_found", "clickedCompleted": clicked_completed}


def walk_with_parent(root: ElementTree.Element):
    stack = [(root, None)]
    while stack:
        elem, parent = stack.pop()
        yield elem, parent
        children = list(elem)
        for child in reversed(children):
            stack.append((child, elem))


def descendant_ids(elem: ElementTree.Element) -> set[int]:
    return {id(child) for child in elem.iter("node")}


def is_text_node(elem: ElementTree.Element) -> bool:
    text = elem.attrib.get("text", "").strip()
    return bool(text) and elem.attrib.get("class", "").endswith("TextView")


def should_ignore_text(elem: ElementTree.Element) -> bool:
    rid = elem.attrib.get("resource-id", "")
    text = elem.attrib.get("text", "").strip()
    if not text:
        return True
    if rid == INPUT_ID or rid == REFERENCE_CONTENT_ID:
        return True
    if rid in REFERENCE_TITLE_IDS:
        return True
    if any(part in rid for part in IGNORE_RESOURCE_PARTS):
        return True
    if text in {"专家", "内容由豆包 AI 生成", "发消息或按住说话...", "买前问豆包"}:
        return True
    if len(text) <= 1:
        return True
    return False


def ordered_unique(texts: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for text in texts:
        text = text.strip()
        if not text or text in seen:
            continue
        if any(text in old for old in result):
            continue
        result = [old for old in result if old not in text]
        seen.add(text)
        result.append(text)
    return result


def merge_text_fragments(fragments: list[str]) -> str:
    merged = ""
    for fragment in ordered_unique(fragments):
        if not merged:
            merged = fragment
            continue
        if fragment in merged:
            continue
        if merged in fragment:
            merged = fragment
            continue

        best = 0
        max_overlap = min(len(merged), len(fragment), 300)
        for size in range(max_overlap, 19, -1):
            if merged[-size:] == fragment[:size]:
                best = size
                break
        if best:
            merged += fragment[best:]
        else:
            merged += "\n\n" + fragment
    return merged.strip()


def extract_expert_content(xml_text: str) -> dict:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return {"thinkingFragments": [], "answerFragments": []}

    think_descendants: set[int] = set()
    ignored_descendants: set[int] = set()
    content_views: list[ElementTree.Element] = []

    for elem in root.iter("node"):
        rid = elem.attrib.get("resource-id", "")
        if rid == THINK_BLOCK_ID:
            think_descendants.update(descendant_ids(elem))
        if rid == CONTENT_VIEW_ID:
            content_views.append(elem)
        if rid in REFERENCE_TITLE_IDS or "think_search_reference" in rid:
            ignored_descendants.update(descendant_ids(elem))

    thinking_fragments: list[str] = []
    answer_fragments: list[str] = []
    content_descendants = set()
    for content_view in content_views:
        content_descendants.update(descendant_ids(content_view))

    for elem, _parent in walk_with_parent(root):
        if elem.tag != "node" or not is_text_node(elem) or should_ignore_text(elem):
            continue
        elem_id = id(elem)
        text = elem.attrib.get("text", "").strip()
        if elem_id in ignored_descendants:
            continue
        if elem_id in think_descendants:
            thinking_fragments.append(text)
        elif elem_id in content_descendants and len(text) >= 20:
            answer_fragments.append(text)

    return {
        "thinkingFragments": ordered_unique(thinking_fragments),
        "answerFragments": ordered_unique(answer_fragments),
    }


def content_signature(content: dict) -> tuple[str, str]:
    thinking = "|".join(content["thinkingFragments"])
    answer = "|".join(content["answerFragments"])
    return thinking[-200:], answer[-200:]


def collect_across_scroll(adb: AdbClient, output_dir: Path, logger: logging.Logger, max_scrolls: int) -> dict:
    thinking_fragments: list[str] = []
    answer_fragments: list[str] = []
    snapshots = []
    seen_signatures = set()
    stable_rounds = 0

    for index in range(max_scrolls + 1):
        xml = dump_xml(adb, output_dir, f"content-sample-{index:02d}")
        content = extract_expert_content(xml)
        thinking_fragments.extend(content["thinkingFragments"])
        answer_fragments.extend(content["answerFragments"])

        signature = content_signature(content)
        snapshots.append({
            "index": index,
            "thinkingFragments": len(content["thinkingFragments"]),
            "answerFragments": len(content["answerFragments"]),
            "signature": signature,
        })
        logger.info(
            f"采样 {index + 1}/{max_scrolls + 1}: "
            f"thinking片段={len(content['thinkingFragments'])}, answer片段={len(content['answerFragments'])}"
        )

        if signature in seen_signatures:
            stable_rounds += 1
        else:
            stable_rounds = 0
            seen_signatures.add(signature)
        if stable_rounds >= 2 and answer_fragments:
            logger.info("连续采样未发现新内容，停止滚动采集")
            break
        if index < max_scrolls:
            swipe_down_content(adb)

    return {
        "thinking": merge_text_fragments(thinking_fragments),
        "answer": merge_text_fragments(answer_fragments),
        "snapshots": snapshots,
    }


def scroll_to_response_top(adb: AdbClient, logger: logging.Logger, rounds: int) -> None:
    logger.info(f"尝试回到当前回答顶部，向上滚动 {rounds} 次")
    for _ in range(rounds):
        swipe_up_content(adb)


def main() -> None:
    parser = argparse.ArgumentParser(description="抓取专家模式思考过程和 AI 正式回答")
    parser.add_argument("--adb", default=WIN_DEFAULT_ADB, help="Path to adb executable.")
    parser.add_argument("--serial", default=None, help="Device serial. Auto-detected if omitted.")
    parser.add_argument("--output-dir", default="outputs/expert-answer", help="Directory to save artifacts.")
    parser.add_argument("--max-scrolls", type=int, default=10, help="Max downward scrolls while collecting visible text.")
    parser.add_argument("--top-scrolls", type=int, default=6, help="Upward scrolls before collecting.")
    args = parser.parse_args()

    adb_path = resolve_adb(args.adb)
    serial = args.serial or first_device(adb_path)
    if not serial:
        sys.exit("No connected adb device found.")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = stamp()
    log_path = output_dir / f"expert-answer-{ts}.log"
    logger = setup_logger(log_path)
    set_capture_options(screenshots=False, current_focus=False)

    adb = AdbClient(adb=adb_path, serial=serial)
    logger.info("=" * 70)
    logger.info(f"专家模式回答内容抓取 serial={serial}")
    logger.info(f"输出目录: {output_dir}")
    logger.info("范围: 只抓取思考过程和正式回答，不处理参考来源")
    logger.info("=" * 70)

    initial_xml = dump_xml(adb, output_dir, "initial")
    initial_content = extract_expert_content(initial_xml)
    logger.info(
        f"初始可见: thinking片段={len(initial_content['thinkingFragments'])}, "
        f"answer片段={len(initial_content['answerFragments'])}"
    )

    expand = expand_thinking_block(adb, logger)
    logger.info(f"思考块展开结果: {expand}")
    scroll_to_response_top(adb, logger, args.top_scrolls)
    collected = collect_across_scroll(adb, output_dir, logger, args.max_scrolls)

    content_dir = output_dir / "content"
    content_dir.mkdir(exist_ok=True)
    thinking_path = content_dir / "thinking.txt"
    answer_path = content_dir / "answer.txt"
    thinking_path.write_text(collected["thinking"], encoding="utf-8")
    answer_path.write_text(collected["answer"], encoding="utf-8")

    status = "success" if collected["thinking"] and collected["answer"] else "failed"
    report = {
        "status": status,
        "serial": serial,
        "startedAt": ts,
        "finishedAt": stamp(),
        "thinkingLength": len(collected["thinking"]),
        "answerLength": len(collected["answer"]),
        "thinkingPreview": collected["thinking"][:200],
        "answerPreview": collected["answer"][:200],
        "thinkingPath": str(thinking_path),
        "answerPath": str(answer_path),
        "expand": expand,
        "snapshots": collected["snapshots"],
    }
    report_path = output_dir / f"expert-answer-report-{ts}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info("=" * 70)
    logger.info("抓取结果")
    logger.info("=" * 70)
    logger.info(f"状态: {status}")
    logger.info(f"思考过程: {len(collected['thinking'])} 字符 -> {thinking_path}")
    logger.info(f"正式回答: {len(collected['answer'])} 字符 -> {answer_path}")
    logger.info(f"报告: {report_path}")
    if status != "success":
        logger.error("未同时抓到思考过程和正式回答")
        sys.exit(2)


if __name__ == "__main__":
    main()
