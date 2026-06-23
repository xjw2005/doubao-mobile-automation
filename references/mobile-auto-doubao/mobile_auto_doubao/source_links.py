import time
from .adb_client import AdbClient
from .artifacts import save_state
from .constants import (
    DOUBAO_PACKAGE,
    INPUT_ID,
    REFERENCE_CONTENT_ID,
    REFERENCE_TITLE_CLICKABLE_ID,
    SEARCH_REFERENCE_TITLE_CONTAINER_ID,
    SHARE_BUTTON_ID,
)
from .doubao_app import center, clear_input_if_visible
from .ui_xml import collect_nodes, extract_urls_from_text, find_nodes, visible_source_items
from .answer_share import clear_focused_input, copy_link_targets_from_xml, read_clipboard


def paste_clipboard_into_input_and_read(adb: AdbClient, output_dir: str, label: str) -> dict:
    """Paste clipboard text into the input field and extract URLs from it."""
    state = save_state(adb, output_dir, f"{label}-before-paste")
    input_nodes = find_nodes(state["nodes"], resource_id=INPUT_ID)
    if not input_nodes:
        return {"urls": [], "error": "input_not_found", "state": state}
    x, y = center(input_nodes[-1])
    adb.tap(x, y)
    time.sleep(0.1)
    clear_focused_input(adb, verify=False)
    adb.keyevent(279)
    time.sleep(0.2)
    pasted = save_state(adb, output_dir, f"{label}-after-paste")
    urls = []
    pasted_text = ""
    for node in find_nodes(pasted["nodes"], resource_id=INPUT_ID):
        text = node.get("text", "")
        if text:
            pasted_text = text
        urls.extend(extract_urls_from_text(text))
    clear_ok = clear_focused_input(adb, verify=True, fallback_chars=max(80, len(pasted_text) + 10 if pasted_text else 80))
    return {"urls": list(dict.fromkeys(urls)), "state": pasted, "clear": {"verified": clear_ok}, "text": pasted_text, "method": "paste_input"}


def read_copied_urls(adb: AdbClient, output_dir: str, label: str) -> dict:
    """Read URLs from the clipboard directly, or via paste as a fallback."""
    direct_text = read_clipboard(adb)
    direct_urls = extract_urls_from_text(direct_text)
    if direct_urls:
        return {"urls": list(dict.fromkeys(direct_urls)), "state": None, "clear": {"verified": True}, "text": direct_text, "method": "direct_clipboard"}
    return paste_clipboard_into_input_and_read(adb, output_dir, label)


def scroll_panel_down(adb: AdbClient) -> None:
    """Scroll the source panel downward."""
    adb.scroll_down(540, 1800, 540, 1100, 800)
    time.sleep(0.12)


def scroll_panel_up(adb: AdbClient) -> None:
    """Scroll the source panel upward."""
    adb.scroll_up(540, 800, 540, 1800, 800)
    time.sleep(0.12)


def scroll_to_source_list_top(adb: AdbClient, rounds: int = 6) -> None:
    """Try to bring the source list back to the top of the panel."""
    for _ in range(rounds):
        scroll_panel_up(adb)


def dump_nodes(adb: AdbClient) -> list[dict]:
    """Return the current UI hierarchy as node dictionaries."""
    return collect_nodes(adb.dump_xml())


def is_conversation_page(nodes: list[dict]) -> bool:
    """Check whether the current UI looks like the conversation page."""
    return bool(find_nodes(nodes, resource_id=INPUT_ID))


def recover_after_source_share_missing(adb: AdbClient) -> dict:
    """Try to recover back to the conversation page after a source tap."""
    time.sleep(3)
    adb.start_app(DOUBAO_PACKAGE)
    time.sleep(1.5)
    nodes = dump_nodes(adb)
    if is_conversation_page(nodes):
        return {"ok": True, "method": "start_app", "state": "conversation"}
    adb.keyevent(4)
    time.sleep(1.5)
    nodes = dump_nodes(adb)
    if is_conversation_page(nodes):
        return {"ok": True, "method": "start_app_back_once", "state": "conversation"}
    return {"ok": False, "method": "start_app_back_once", "state": "not_conversation"}


def collect_sources_across_scroll(adb: AdbClient, output_dir: str, max_rounds: int = 5) -> dict:
    """Collect all visible sources while scrolling through the source panel."""
    seen_titles = set()
    collected_items = []
    no_new_count = 0
    for scroll_round in range(max_rounds):
        nodes = dump_nodes(adb)
        search_nodes = find_nodes(nodes, resource_id=SEARCH_REFERENCE_TITLE_CONTAINER_ID)
        for search_node in search_nodes:
            bounds = search_node.get("parsedBounds")
            if not bounds:
                continue
            cy = bounds["centerY"]
            sources_below = [item for item in visible_source_items(nodes, REFERENCE_CONTENT_ID) if item["centerY"] > cy]
            if not sources_below and 260 <= cy <= 1980:
                adb.tap(bounds["centerX"], cy)
                time.sleep(0.15)
                nodes = dump_nodes(adb)

        new_count = 0
        for item in visible_source_items(nodes, REFERENCE_CONTENT_ID):
            title = item["title"].strip()
            if title and title not in seen_titles:
                seen_titles.add(title)
                collected_items.append(item)
                new_count += 1
        if new_count:
            no_new_count = 0
        else:
            no_new_count += 1
            if no_new_count >= 2:
                break
        scroll_panel_down(adb)
    state = save_state(adb, output_dir, "sources-after-panel-scroll")
    return {"items": collected_items, "state": state}


def find_source_by_title_realtime(nodes: list[dict], target_title: str) -> dict | None:
    """Find a currently visible source item by title."""
    current_items = visible_source_items(nodes, REFERENCE_CONTENT_ID)
    target = target_title.strip()
    for item in current_items:
        if item["title"].strip() == target:
            return item
    for item in current_items:
        title = item["title"].strip()
        if target and title and (target in title or title in target):
            return item
    return None


def locate_source_realtime(adb: AdbClient, target_title: str, max_scroll_attempts: int = 8) -> dict | None:
    """Scroll until the requested source item is visible and tappable."""
    clickable_top = 268
    clickable_bottom = 1800
    for _ in range(max_scroll_attempts + 1):
        nodes = dump_nodes(adb)
        item = find_source_by_title_realtime(nodes, target_title)
        if item:
            cy = item["centerY"]
            if clickable_top <= cy <= clickable_bottom:
                return item
            if cy > clickable_bottom:
                scroll_panel_down(adb)
            else:
                scroll_panel_up(adb)
        else:
            scroll_panel_down(adb)
    return None


def expand_references_if_needed(adb: AdbClient, nodes: list[dict], output_dir: str) -> dict:
    """Open the references panel if it is not already expanded."""

    # ------------------------------------------------------------------
    # 步骤1：检查参考资料是否已经展开（tv_reference_content 已可见则无需操作）
    # ------------------------------------------------------------------
    items = visible_source_items(nodes, REFERENCE_CONTENT_ID)
    if items:
        return {"expanded": False, "reason": "source_items_already_visible", "items": items}

    # ------------------------------------------------------------------
    # 步骤2：参考资料面板位于答案顶部，需先向上滑动滚动到聊天顶部
    # 答案生成后 UI 默认停留在底部，不滚动则看不到参考资料标题
    # ------------------------------------------------------------------
    for _ in range(5):
        adb.scroll_up(540, 500, 540, 1800, 300)
        time.sleep(0.08)
    scrolled = save_state(adb, output_dir, "sources-after-scroll-top")
    nodes = scrolled["nodes"]

    # 滚动后再次检查是否已经展开
    items = visible_source_items(nodes, REFERENCE_CONTENT_ID)
    if items:
        return {"expanded": False, "reason": "already_visible_after_scroll", "items": items, "state": scrolled}

    search_ref_nodes = find_nodes(nodes, resource_id=SEARCH_REFERENCE_TITLE_CONTAINER_ID)
    if search_ref_nodes:
        target = search_ref_nodes[0]
        target_bounds = target.get("parsedBounds")
        if target_bounds and 300 <= target_bounds["centerY"] <= 1900:
            x2, y2 = target_bounds["centerX"], target_bounds["centerY"]
        adb.tap(x2, y2)
        time.sleep(0.15)
        collected = collect_sources_across_scroll(adb, output_dir)
        if collected["items"]:
            return {
                "expanded": True,
                "reason": "search_container_already_visible_panel_scroll",
                "secondTap": {"x": x2, "y": y2, "bounds": target.get("bounds", "")},
                "items": collected["items"],
                "state": collected["state"],
            }

    # ------------------------------------------------------------------
    # 步骤3：第一次点击——点击 ll_reference_title 展开参考资料/思考过程
    # 注意：tv_reference_title（文本节点）本身 clickable=false，必须点击其父容器
    # ------------------------------------------------------------------
    title_nodes = find_nodes(nodes, resource_id=REFERENCE_TITLE_CLICKABLE_ID)
    if not title_nodes:
        return {"expanded": False, "reason": "reference_title_not_found", "items": []}

    x, y = center(title_nodes[0])
    adb.tap(x, y)
    time.sleep(0.15)
    after_first_tap = save_state(adb, output_dir, "sources-after-first-tap")
    nodes = after_first_tap["nodes"]

    # 普通模式下，第一次点击后来源列表直接出现，无需第二次点击
    items = visible_source_items(nodes, REFERENCE_CONTENT_ID)
    if items:
        return {
            "expanded": True,
            "reason": "expanded_after_first_click",
            "firstTap": {"x": x, "y": y, "bounds": title_nodes[0].get("bounds", "")},
            "items": items,
            "state": after_first_tap,
        }

    # ------------------------------------------------------------------
    # 步骤4：专家模式——查找 searchReferenceTitleContainer（第二次点击目标）
    # 第一次点击只展开了思考过程，来源列表嵌套在思考过程中，需第二次点击展开
    # ------------------------------------------------------------------
    search_ref_nodes = find_nodes(nodes, resource_id=SEARCH_REFERENCE_TITLE_CONTAINER_ID)
    if not search_ref_nodes:
        # 既没有来源条目，也找不到第二次点击目标，判定为展开失败
        return {
            "expanded": False,
            "reason": "search_reference_container_not_found",
            "items": [],
            "state": after_first_tap,
        }

    # ------------------------------------------------------------------
    # 步骤5：第二次点击前——检测目标元素是否在可视范围内，不在则微小滚动
    # 可视范围基于消息列表区域（标题栏下方 ~ 输入框上方）
    # ------------------------------------------------------------------
    target = search_ref_nodes[0]
    target_bounds = target.get("parsedBounds")
    if not target_bounds:
        return {
            "expanded": False,
            "reason": "search_reference_no_bounds",
            "items": [],
            "state": after_first_tap,
        }

    # 可视区域的上下边界（留出标题栏和输入栏的空间）
    VISIBLE_TOP = 300
    VISIBLE_BOTTOM = 1900
    # 微小滚动距离，避免过度滚动导致目标元素又被滑出可视区
    SCROLL_DISTANCE = 400
    # 最大滚动尝试次数，防止无限滚动
    MAX_SCROLL_ATTEMPTS = 6

    for attempt in range(MAX_SCROLL_ATTEMPTS):
        # 目标元素中心点在可视范围内则停止滚动
        if VISIBLE_TOP <= target_bounds["centerY"] <= VISIBLE_BOTTOM:
            break

        # 目标在可视区下方：手指上滑，露出下方内容。
        # 目标在可视区上方：手指下滑，回到上方内容。
        if target_bounds["centerY"] > VISIBLE_BOTTOM:
            scroll_panel_down(adb)
        else:
            scroll_panel_up(adb)

        # 滚动后重新获取界面状态，重新定位目标元素
        rescrolled = save_state(adb, output_dir, f"sources-scroll-before-second-tap-{attempt}")
        search_ref_nodes = find_nodes(rescrolled["nodes"], resource_id=SEARCH_REFERENCE_TITLE_CONTAINER_ID)
        if not search_ref_nodes:
            # 滚动后目标元素消失（可能被折叠或界面变化），终止
            return {
                "expanded": False,
                "reason": "search_reference_lost_after_scroll",
                "items": [],
                "state": rescrolled,
            }
        target = search_ref_nodes[0]
        target_bounds = target.get("parsedBounds")
        if not target_bounds:
            return {
                "expanded": False,
                "reason": "search_reference_no_bounds_after_scroll",
                "items": [],
                "state": rescrolled,
            }

    # ------------------------------------------------------------------
    # 步骤6：第二次点击——点击 searchReferenceTitleContainer 展开来源列表
    # ------------------------------------------------------------------
    if not (VISIBLE_TOP <= target_bounds["centerY"] <= VISIBLE_BOTTOM):
        # 滚动多次后目标仍不在可视范围，判定为展开失败
        return {
            "expanded": False,
            "reason": "search_reference_not_visible",
            "items": [],
            "state": after_first_tap,
        }

    x2, y2 = target_bounds["centerX"], target_bounds["centerY"]
    adb.tap(x2, y2)
    time.sleep(0.15)
    expanded = save_state(adb, output_dir, "sources-after-second-tap")
    collected = collect_sources_across_scroll(adb, output_dir)
    if collected["items"]:
        return {
            "expanded": True,
            "reason": "expanded_after_second_click_panel_scroll",
            "firstTap": {"x": x, "y": y, "bounds": title_nodes[0].get("bounds", "")},
            "secondTap": {"x": x2, "y": y2, "bounds": target.get("bounds", "")},
            "items": collected["items"],
            "state": collected["state"],
        }
    return {
        "expanded": True,
        "reason": "expanded_after_second_click",
        "firstTap": {"x": x, "y": y, "bounds": title_nodes[0].get("bounds", "")},
        "secondTap": {"x": x2, "y": y2, "bounds": target.get("bounds", "")},
        "items": visible_source_items(expanded["nodes"], REFERENCE_CONTENT_ID),
        "state": expanded,
    }


def resolve_source_limit(source_limit, item_count: int) -> int:
    """Clamp the requested source limit to the number of collected items."""
    if isinstance(source_limit, str) and source_limit.lower() == "all":
        return item_count
    return min(int(source_limit), item_count)


def extract_sources(adb: AdbClient, options: dict, output_dir: str) -> dict:
    """Extract all available source links for the current answer."""
    initial = save_state(adb, output_dir, "sources-initial")
    clear_input_if_visible(adb, initial["nodes"], verify=True)
    initial = save_state(adb, output_dir, "sources-after-input-clear")
    expand = expand_references_if_needed(adb, initial["nodes"], output_dir)
    items = expand["items"]
    limit = resolve_source_limit(options.get("sourceLimit", 5), len(items))
    sources = []
    if items:
        scroll_to_source_list_top(adb)
    for index, item in enumerate(items[:limit], start=1):
        realtime_item = locate_source_realtime(adb, item["title"], max_scroll_attempts=20)
        if not realtime_item:
            sources.append({
                "index": index,
                "title": item["title"],
                "url": "",
                "method": "share_copy_paste_read",
                "status": "failed",
                "error": "source_not_found_after_scroll",
                "debug": {},
            })
            continue
        item = realtime_item
        source = {
            "index": index,
            "title": item["title"],
            "url": "",
            "method": "share_copy_paste_read",
            "status": "pending",
            "error": None,
            "debug": {"sourceTap": {"x": item["centerX"], "y": item["centerY"], "bounds": item["bounds"]}},
        }
        try:
            adb.tap(item["centerX"], item["centerY"])
            time.sleep(float(options.get("sourcePageWaitSeconds", 0.25)))
            source_page = save_state(adb, output_dir, f"source-{index}-page")
            share_nodes = find_nodes(source_page["nodes"], resource_id=SHARE_BUTTON_ID)
            if not share_nodes:
                source.update({"status": "unsupported", "error": "share_button_not_found"})
                recovery = recover_after_source_share_missing(adb)
                source["debug"]["recovery"] = {key: value for key, value in recovery.items() if key != "nodes"}
                sources.append(source)
                if not recovery.get("ok"):
                    break
                continue
            x, y = center(share_nodes[-1])
            adb.tap(x, y)
            time.sleep(float(options.get("sourceShareWaitSeconds", 0.15)))
            share_xml = adb.dump_xml()
            share_sheet_path = f"{output_dir}/source-{index}-share-sheet.xml"
            from pathlib import Path
            Path(share_sheet_path).write_text(share_xml, encoding="utf-8")
            share_sheet = {"xml": share_xml, "nodes": collect_nodes(share_xml)}
            copy_nodes = copy_link_targets_from_xml(share_xml)
            if not copy_nodes:
                copy_nodes = find_nodes(share_sheet["nodes"], text_contains="复制链接")
            if not copy_nodes:
                source.update({"status": "unsupported", "error": "copy_link_not_found"})
                adb.keyevent(4)
                time.sleep(0.2)
                adb.keyevent(4)
                time.sleep(0.25)
                sources.append(source)
                continue
            usable_copy_nodes = [node for node in copy_nodes if node.get("parsedBounds") and node["parsedBounds"]["centerY"] > 1800]
            if not usable_copy_nodes:
                usable_copy_nodes = [node for node in copy_nodes if node.get("parsedBounds")]
            x, y = center(usable_copy_nodes[0])
            adb.tap(x, y)
            time.sleep(0.15)
            adb.keyevent(4)
            time.sleep(1)
            # 兜底：点"复制链接"后面板关闭时序不稳定，单次 keyevent(4) 可能只关面板没退详情页，
            # 导致后续 read_copied_urls 在详情页找不到输入框而丢链接。读链接前先确认已回对话页，未回才补一次返回键。
            if not is_conversation_page(dump_nodes(adb)):
                adb.keyevent(4)
                time.sleep(0.12)
            paste = read_copied_urls(adb, output_dir, f"source-{index}")
            urls = paste.get("urls", [])
            clear_result = paste.get("clear", {})
            if urls and clear_result.get("verified") is not False:
                source.update({"url": urls[0], "status": "success"})
            elif urls:
                source.update({"url": urls[0], "status": "failed", "error": "input_clear_failed_after_paste"})
            else:
                source.update({"status": "no_url_found", "error": paste.get("error") or "no_url_after_paste"})
            source["debug"].update({
                "sourcePageXml": source_page["xml"],
                "shareSheetXml": share_sheet["xml"],
                "pasteStateXml": paste.get("state", {}).get("xml"),
                "clear": clear_result,
                "readMethod": paste.get("method"),
            })
            if source.get("error") == "input_clear_failed_after_paste":
                sources.append(source)
                break
        except Exception as exc:
            source.update({"status": "failed", "error": str(exc)})
            adb.keyevent(4)
            time.sleep(0.15)
        sources.append(source)
    return {"sources": sources, "visibleSourceCount": len(items), "attemptedCount": limit, "referenceExpansion": {key: value for key, value in expand.items() if key not in {"items", "state"}}}
