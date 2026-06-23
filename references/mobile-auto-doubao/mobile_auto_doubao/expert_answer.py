import time
from pathlib import Path
from xml.etree import ElementTree

from .adb_client import AdbClient
from .constants import INPUT_ID, REFERENCE_CONTENT_ID, REFERENCE_TITLE_CLICKABLE_ID
from .doubao_app import center
from .ui_xml import collect_nodes, find_nodes


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


def dump_xml(adb: AdbClient, output_dir: str | Path, label: str) -> str:
    """Dump the current screen XML and save it to disk."""
    xml = adb.dump_xml()
    Path(output_dir, f"{label}.xml").write_text(xml, encoding="utf-8")
    return xml


def dump_nodes(adb: AdbClient) -> list[dict]:
    """Return the current UI hierarchy as node dictionaries."""
    return collect_nodes(adb.dump_xml())


def swipe_down_content(adb: AdbClient) -> None:
    """Scroll the content area downward."""
    adb.scroll_down()
    time.sleep(0.2)


def swipe_up_content(adb: AdbClient) -> None:
    """Scroll the content area upward."""
    adb.scroll_up()
    time.sleep(0.2)


def tap_if_visible(adb: AdbClient, node: dict) -> bool:
    """Tap a node when it is within the visible screen bounds."""
    bounds = node.get("parsedBounds")
    if not bounds:
        return False
    x, y = center(node)
    if y < 260 or y > 1980:
        return False
    adb.tap(x, y)
    time.sleep(0.25)
    return True


def click_completed_thinking_if_needed(adb: AdbClient) -> bool:
    """Tap a completed-thinking trigger if the UI exposes one."""
    nodes = dump_nodes(adb)
    for keyword in ("已完成思考", "完成思考", "思考完成"):
        for node in nodes:
            text = node.get("text", "") + node.get("content_desc", "")
            if keyword in text and node.get("clickable") == "true":
                return tap_if_visible(adb, node)
    return False


def thinking_block_visible(nodes: list[dict]) -> bool:
    """Check whether the expert-thinking block is currently visible."""
    return bool(find_nodes(nodes, resource_id=THINK_BLOCK_ID))


def expand_thinking_block(adb: AdbClient) -> dict:
    """Expand the expert-thinking block if it is collapsed."""
    clicked_completed = click_completed_thinking_if_needed(adb)
    nodes = dump_nodes(adb)
    if thinking_block_visible(nodes):
        return {"ok": True, "method": "already_visible", "clickedCompleted": clicked_completed}
    for node in find_nodes(nodes, resource_id=REFERENCE_TITLE_CLICKABLE_ID):
        if tap_if_visible(adb, node):
            if thinking_block_visible(dump_nodes(adb)):
                return {"ok": True, "method": "title_click", "clickedCompleted": clicked_completed}
    return {"ok": False, "method": "not_found", "clickedCompleted": clicked_completed}


def walk_with_parent(root: ElementTree.Element):
    """Yield each XML node together with its parent."""
    stack = [(root, None)]
    while stack:
        elem, parent = stack.pop()
        yield elem, parent
        for child in reversed(list(elem)):
            stack.append((child, elem))


def descendant_ids(elem: ElementTree.Element) -> set[int]:
    """Collect the object ids for all descendant nodes."""
    return {id(child) for child in elem.iter("node")}


def is_text_node(elem: ElementTree.Element) -> bool:
    """Check whether an XML element is a meaningful text node."""
    text = elem.attrib.get("text", "").strip()
    return bool(text) and elem.attrib.get("class", "").endswith("TextView")


def should_ignore_text(elem: ElementTree.Element) -> bool:
    """Filter out boilerplate and structural text nodes."""
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
    if text in {"专家", "快速", "内容由豆包 AI 生成", "发消息...", "发消息或按住说话...", "买前问豆包"}:
        return True
    return len(text) <= 1


def ordered_unique(texts: list[str]) -> list[str]:
    """Deduplicate text fragments while preserving order and specificity."""
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
    """Merge overlapping text fragments into a single readable block."""
    merged = ""
    for fragment in ordered_unique(fragments):
        if not merged or merged in fragment:
            merged = fragment
            continue
        if fragment in merged:
            continue
        best = 0
        for size in range(min(len(merged), len(fragment), 300), 19, -1):
            if merged[-size:] == fragment[:size]:
                best = size
                break
        merged += fragment[best:] if best else "\n\n" + fragment
    return merged.strip()


def extract_expert_content(xml_text: str, question: str = "") -> dict:
    """Extract thinking and answer fragments from the expert-mode XML."""
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return {"thinkingFragments": [], "answerFragments": []}

    q = question.strip()
    think_descendants: set[int] = set()
    ignored_descendants: set[int] = set()
    content_descendants: set[int] = set()
    for elem in root.iter("node"):
        rid = elem.attrib.get("resource-id", "")
        if rid == THINK_BLOCK_ID:
            think_descendants.update(descendant_ids(elem))
        if rid == CONTENT_VIEW_ID:
            content_descendants.update(descendant_ids(elem))
        if rid in REFERENCE_TITLE_IDS or "think_search_reference" in rid:
            ignored_descendants.update(descendant_ids(elem))

    thinking_fragments: list[str] = []
    answer_fragments: list[str] = []
    for elem, _parent in walk_with_parent(root):
        if elem.tag != "node" or not is_text_node(elem) or should_ignore_text(elem):
            continue
        elem_id = id(elem)
        if elem_id in ignored_descendants:
            continue
        text = elem.attrib.get("text", "").strip()
        if text == q:
            continue
        if elem_id in think_descendants:
            thinking_fragments.append(text)
        elif elem_id in content_descendants and len(text) >= 20:
            answer_fragments.append(text)
    return {"thinkingFragments": ordered_unique(thinking_fragments), "answerFragments": ordered_unique(answer_fragments)}


def content_signature(content: dict) -> tuple[str, str]:
    """Summarize a content snapshot so repeated scroll states can be detected."""
    return "|".join(content["thinkingFragments"])[-200:], "|".join(content["answerFragments"])[-200:]


def collect_expert_answer(adb: AdbClient, output_dir: str, options: dict | None = None, question: str = "") -> dict:
    """Collect expert-mode thinking content and the final answer text."""
    options = options or {}
    max_scrolls = int(options.get("expertAnswerMaxScrolls", 8)) + 2
    top_scrolls = int(options.get("expertAnswerTopScrolls", 2)) + 2
    for _ in range(top_scrolls):
        swipe_up_content(adb)
    expand = expand_thinking_block(adb)

    thinking_fragments: list[str] = []
    answer_fragments: list[str] = []
    snapshots = []
    seen_signatures = set()
    stable_rounds = 0
    for index in range(max_scrolls + 1):
        xml = dump_xml(adb, output_dir, f"expert-content-sample-{index:02d}")
        content = extract_expert_content(xml, question)
        thinking_fragments.extend(content["thinkingFragments"])
        answer_fragments.extend(content["answerFragments"])
        signature = content_signature(content)
        snapshots.append({"index": index, "thinkingFragments": len(content["thinkingFragments"]), "answerFragments": len(content["answerFragments"])})
        if signature in seen_signatures:
            stable_rounds += 1
        else:
            stable_rounds = 0
            seen_signatures.add(signature)
        if stable_rounds >= 2 and answer_fragments:
            break
        if index < max_scrolls:
            swipe_down_content(adb)
    thinking = merge_text_fragments(thinking_fragments)
    answer = merge_text_fragments(answer_fragments)
    return {
        "thinking": thinking,
        "answer": answer,
        "status": "success" if thinking and answer else "failed",
        "expand": expand,
        "snapshots": snapshots,
    }
