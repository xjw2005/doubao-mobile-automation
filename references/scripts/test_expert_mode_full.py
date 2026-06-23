#!/usr/bin/env python3
"""专家模式参考来源链接完整获取测试。

完整流程：
1. 检测"已完成思考"按钮并点击（如可见），展示思考过程及参考资料数量
2. 双击展开参考资料面板（专家模式）
   - 第一次点击 ll_reference_title 展开思考过程
   - 第二次点击 searchReferenceTitleContainer 展开来源列表
3. 多轮滚动展开所有链接来源
   - 第一轮向下滚动：点击所有可展开的链接按钮（展开所有来源）
   - 滚动至页面顶部
   - 第二轮向下滚动：检查具体来源，发现则收集
   - 未发现则继续向下翻页
4. 贪吃蛇遍历所有来源，提取 URL（复用现有分享-复制-粘贴逻辑）
5. 翻页完成后再次检查未处理来源，如有则继续提取
6. 生成完整测试报告

Usage (PowerShell):
    python scripts/test_expert_mode_full.py --serial 100.76.50.7:6666
    python scripts/test_expert_mode_full.py --serial <serial> --output-dir outputs/expert-full
"""

import argparse
import json
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path

# 将项目根目录加入 sys.path，以便复用 mobile_auto_doubao 包内的逻辑
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 复用项目模块
from mobile_auto_doubao.adb_client import AdbClient
from mobile_auto_doubao.artifacts import set_capture_options
from mobile_auto_doubao.constants import (
    ADB_KEYBOARD_IME,
    INPUT_ID,
    REFERENCE_CONTENT_ID,
    REFERENCE_TITLE_CLICKABLE_ID,
    SEARCH_REFERENCE_TITLE_CONTAINER_ID,
    SHARE_BUTTON_ID,
)
from mobile_auto_doubao.doubao_app import center
from mobile_auto_doubao.ui_xml import collect_nodes, extract_urls_from_text, find_nodes, visible_source_items

# Windows 默认 adb 路径
WIN_DEFAULT_ADB = r"C:\Users\Administrator\AppData\Local\Android\Sdk\platform-tools\adb.exe"

# 可视区域边界（留出标题栏和输入栏空间）
VISIBLE_TOP = 300
VISIBLE_BOTTOM = 1900


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def setup_logger(log_path: Path) -> logging.Logger:
    """配置日志，同时输出到 stdout 和文件。"""
    logger = logging.getLogger("expert_full")
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
    result = subprocess.run([adb, "devices"], capture_output=True, text=True)
    for line in result.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            return parts[0]
    return None


def dump_nodes(adb: AdbClient) -> list[dict]:
    """轻量级 UI 探测：只 dump XML 并解析节点，不截图不做 current_focus。"""
    xml = adb.dump_xml()
    return collect_nodes(xml)


def save_diag_xml(adb: AdbClient, output_dir: Path, label: str) -> Path:
    """保存诊断 XML 文件，用于后续分析。"""
    xml = adb.dump_xml()
    path = output_dir / f"{label}-{stamp()}.xml"
    path.write_text(xml, encoding="utf-8")
    return path


def find_clickable_by_text(nodes: list[dict], text_keyword: str) -> list[dict]:
    """查找包含指定文本且可点击的节点。"""
    result = []
    for node in nodes:
        if text_keyword in node.get("text", "") and node.get("clickable") == "true":
            bounds = node.get("parsedBounds")
            if bounds:
                result.append(node)
    return result


def find_clickable_by_desc(nodes: list[dict], desc_keyword: str) -> list[dict]:
    """查找 content-desc 包含指定文本且可点击的节点。"""
    result = []
    for node in nodes:
        if desc_keyword in node.get("content_desc", "") and node.get("clickable") == "true":
            bounds = node.get("parsedBounds")
            if bounds:
                result.append(node)
    return result


def scroll_down(adb: AdbClient, distance: int = 500, duration: int = 300) -> None:
    """向下翻页（手指上滑，内容下移，露出下方内容）。"""
    adb.command(["shell", "input", "swipe", "540", str(VISIBLE_BOTTOM), "540", str(VISIBLE_BOTTOM - distance), str(duration)])
    time.sleep(0.4)


def scroll_up(adb: AdbClient, distance: int = 500, duration: int = 300) -> None:
    """向上翻页（手指下滑，内容上移，露出上方内容）。"""
    adb.command(["shell", "input", "swipe", "540", str(VISIBLE_TOP), "540", str(VISIBLE_TOP + distance), str(duration)])
    time.sleep(0.4)


def scroll_panel_down(adb: AdbClient, panel_top: int = 1500, panel_bottom: int = 1950, distance: int = 300, duration: int = 300) -> None:
    """慢速滑动整个聊天页面，加载来源面板下方更多来源。

    关键发现：来源面板嵌在聊天页面内（scrollable 容器 y=268-1978），
    必须用慢速滑动滚动整个聊天页面才能加载更多来源。
    快速滑动会导致面板折叠或无效滚动。
    滑动距离调短（从1800→500改为1800→1100），避免跳过来源条目。
    注意：duration 不能超过 1000，否则 adb shell 会卡住。
    """
    # 慢速滑动整个聊天页面：从底部(1800)滑到中部(1100)，duration=800
    adb.command(["shell", "input", "swipe", "540", "1800", "540", "1100", "800"])
    time.sleep(1.0)  # 慢速滑动后等待UI稳定


def scroll_panel_up(adb: AdbClient, panel_top: int = 1500, panel_bottom: int = 1950, distance: int = 700, duration: int = 300) -> None:
    """慢速向上滚动页面，回到来源面板顶部（看到上方内容）。

    页面向上滚动 = 手指从上往下滑 = swipe 起点Y < 终点Y（从 800 滑到 1800）。
    注意：duration 不能超过 1000，否则 adb shell 会卡住。
    """
    adb.command(["shell", "input", "swipe", "540", "800", "540", "1800", "800"])
    time.sleep(1.0)


def scroll_to_top(adb: AdbClient, logger: logging.Logger) -> None:
    """滚动至页面顶部（慢速滑动避免折叠面板）。

    专家模式展开后，来源面板在页面下方，需多次向上滑动回到顶部。
    """
    logger.info("滚动至页面顶部（慢速滑动避免折叠面板）")
    for i in range(6):
        try:
            scroll_panel_up(adb)
        except Exception as e:
            logger.warning(f"第 {i+1}/6 次向上滑动失败: {e}")
            break
    time.sleep(0.5)
    logger.info("已滚动至页面顶部")


def ensure_visible(adb: AdbClient, target_y: int, logger: logging.Logger, label: str = "") -> bool:
    """确保目标 Y 坐标在可视范围内，不在则微调滚动。返回是否在可视范围。"""
    for _ in range(6):
        if VISIBLE_TOP <= target_y <= VISIBLE_BOTTOM:
            return True
        if target_y > VISIBLE_BOTTOM:
            scroll_down(adb, distance=400)
        else:
            scroll_up(adb, distance=400)
        logger.info(f"微调滚动使{label}可见，当前目标Y={target_y}")
    return VISIBLE_TOP <= target_y <= VISIBLE_BOTTOM


# ============================================================================
# 剪贴板读取（复用现有逻辑，快速清空，不使用 320 次 backspace）
# ============================================================================

def read_clipboard_via_paste(adb: AdbClient, nodes: list[dict]) -> str:
    """通过粘贴到输入框读取剪贴板内容（传统方法，快速清空，不使用320次backspace）。

    步骤：点击输入框 → 粘贴 → 读取文本 → broadcast_clear_text 快速清空。
    仅在输入框可见时使用，避免误导航。
    """
    input_nodes = find_nodes(nodes, resource_id=INPUT_ID)
    if not input_nodes:
        return ""

    x, y = center(input_nodes[-1])
    adb.tap(x, y)
    time.sleep(0.3)
    adb.keyevent(279)  # PASTE
    time.sleep(0.8)

    # 读取粘贴后的输入框文本
    pasted_nodes = dump_nodes(adb)
    texts = []
    for node in find_nodes(pasted_nodes, resource_id=INPUT_ID):
        text = node.get("text", "")
        if text:
            texts.append(text)

    # 快速清空输入框（broadcast_clear_text，不使用320次backspace）
    try:
        previous_ime = adb.current_ime()
        if previous_ime != ADB_KEYBOARD_IME:
            adb.set_ime(ADB_KEYBOARD_IME)
        adb.broadcast_clear_text()
        time.sleep(0.3)
    except Exception:
        pass

    return texts[0] if texts else ""


# ============================================================================
# 阶段1：检测并点击"已完成思考"按钮
# ============================================================================

def click_thinking_button(adb: AdbClient, nodes: list[dict], logger: logging.Logger) -> bool:
    """检测并点击"已完成思考"按钮（如可见）。

    专家模式下，AI 完成深度思考后会显示"已完成思考"按钮，
    点击后展示思考过程及参考资料数量。
    """
    # 查找包含"已完成思考"或"思考"的可点击节点
    for keyword in ["已完成思考", "完成思考", "思考完成"]:
        found = find_clickable_by_text(nodes, keyword)
        if found:
            bounds = found[0]["parsedBounds"]
            x, y = bounds["centerX"], bounds["centerY"]
            logger.info(f"点击'已完成思考'按钮: text='{found[0]['text'][:40]}' center=({x},{y})")
            adb.tap(x, y)
            time.sleep(1.0)
            return True

    # 也检查 content-desc
    for keyword in ["已完成思考", "完成思考"]:
        found = find_clickable_by_desc(nodes, keyword)
        if found:
            bounds = found[0]["parsedBounds"]
            x, y = bounds["centerX"], bounds["centerY"]
            logger.info(f"点击'已完成思考'按钮(desc): desc='{found[0]['content_desc'][:40]}' center=({x},{y})")
            adb.tap(x, y)
            time.sleep(1.0)
            return True

    logger.info("未找到'已完成思考'按钮（可能已展开或非专家模式）")
    return False


# ============================================================================
# 阶段2：专家模式双击展开参考资料面板
# ============================================================================

def expand_expert_mode(adb: AdbClient, nodes: list[dict], output_dir: Path, logger: logging.Logger) -> dict:
    """专家模式完整展开流程：双击 + 多轮滚动。

    返回 {"expanded": bool, "reason": str, "items": list, ...}
    注意：即使来源已部分可见，也会进入多轮滚动收集全部来源
    （标题可能显示"参考12篇"但当前只可见2篇）
    """
    # 步骤1：检测当前可见来源数（仅用于日志）
    items = visible_source_items(nodes, REFERENCE_CONTENT_ID)
    if items:
        logger.info(f"当前已可见 {len(items)} 条来源，但仍需滚动收集全部来源")
    else:
        logger.info("当前未发现可见来源，需完整展开流程")

    # 步骤2：检测并点击"已完成思考"按钮
    click_thinking_button(adb, nodes, logger)
    nodes = dump_nodes(adb)

    # 步骤3：第一次点击 ll_reference_title 展开思考过程
    # 注意：ll_reference_title 是 toggle，已展开时点击会折叠
    # 所以先检查 searchReferenceTitleContainer 是否已可见（思考过程已展开）
    search_nodes = find_nodes(nodes, resource_id=SEARCH_REFERENCE_TITLE_CONTAINER_ID)
    if search_nodes:
        logger.info("思考过程已展开（searchReferenceTitleContainer 可见），跳过第一次点击")
    else:
        title_nodes = find_nodes(nodes, resource_id=REFERENCE_TITLE_CLICKABLE_ID)
        if not title_nodes:
            logger.error("未找到参考资料标题容器 ll_reference_title")
            return {"expanded": False, "reason": "title_not_found", "items": []}

        x, y = center(title_nodes[0])
        logger.info(f"第一次点击 ll_reference_title center=({x},{y}) 展开思考过程")
        adb.tap(x, y)
        time.sleep(1.0)
        save_diag_xml(adb, output_dir, "expert-after-first-tap")
        nodes = dump_nodes(adb)

        # 再次检查 searchReferenceTitleContainer
        search_nodes = find_nodes(nodes, resource_id=SEARCH_REFERENCE_TITLE_CONTAINER_ID)
        if not search_nodes:
            # 可能思考过程已展开，第一次点击折叠了它，再点一次恢复
            logger.warning("点击后未找到 searchReferenceTitleContainer，可能误折叠，再次点击恢复")
            adb.tap(x, y)
            time.sleep(1.0)
            nodes = dump_nodes(adb)
            search_nodes = find_nodes(nodes, resource_id=SEARCH_REFERENCE_TITLE_CONTAINER_ID)

    # 步骤4：专家模式——点击第一个未展开的 searchReferenceTitleContainer 展开来源列表
    # 后续的 searchReferenceTitleContainer 由 multi_scroll_expand 逐个展开
    if search_nodes:
        target = search_nodes[0]
        bounds = target.get("parsedBounds")
        if bounds:
            x2, y2 = bounds["centerX"], bounds["centerY"]
            # 检查来源列表是否已展开（searchReferenceTitleContainer 下方有来源条目）
            sources_below = [s for s in visible_source_items(nodes, REFERENCE_CONTENT_ID) if s["centerY"] > y2]
            if sources_below:
                logger.info(f"来源列表已展开（下方有 {len(sources_below)} 条来源），跳过第二次点击")
            else:
                logger.info(f"第二次点击 searchReferenceTitleContainer center=({x2},{y2}) 展开来源列表")
                adb.tap(x2, y2)
                time.sleep(1.0)
                save_diag_xml(adb, output_dir, "expert-after-second-tap")

    # 步骤5：进入多轮滚动展开流程
    # multi_scroll_expand 会点击所有未展开的 searchReferenceTitleContainer 并滑动收集全部来源
    nodes = dump_nodes(adb)
    items = visible_source_items(nodes, REFERENCE_CONTENT_ID)
    if items:
        logger.info(f"当前可见 {len(items)} 条来源，进入多轮滚动收集全部")
    else:
        logger.info("当前未发现来源，进入多轮滚动展开流程")

    return multi_scroll_expand(adb, output_dir, logger)


# ============================================================================
# 阶段3：多轮滚动展开所有链接来源
# ============================================================================

def multi_scroll_expand(adb: AdbClient, output_dir: Path, logger: logging.Logger) -> dict:
    """慢速滑动整个聊天页面，展开所有搜索轮次并收集所有来源条目。

    关键发现：
    1. 来源面板嵌在聊天页面内（scrollable 容器 y=268-1978），
       必须用慢速滑动（duration=2000）滚动整个聊天页面才能加载更多来源。
    2. 专家模式可能有多个搜索轮次，每轮有独立的 searchReferenceTitleContainer，
       需要逐个点击展开，才能收集到全部来源（如"参考12篇"可能来自多个搜索轮次）。

    流程：
    1. 每轮：先点击所有可见的 searchReferenceTitleContainer 展开来源列表
    2. 收集当前屏幕的来源条目（用标题去重，因为 bounds 会随滑动变化）
    3. 慢速向下滑动整个聊天页面，加载更多内容
    4. 连续 3 轮无新增来源时认为已到底部
    """
    seen_titles = set()       # 已收集来源的标题，用于去重（bounds 会随滑动变化，不可靠）
    collected_items = []      # 收集到的来源条目列表
    no_new_count = 0          # 连续无新增来源的次数

    logger.info("=" * 50)
    logger.info("慢速滑动整个聊天页面，展开所有搜索轮次并收集来源（按标题去重）")
    logger.info("=" * 50)

    for scroll_round in range(25):
        nodes = dump_nodes(adb)

        # 步骤1：点击所有未展开的 searchReferenceTitleContainer 展开来源列表
        # 专家模式有多个搜索轮次，每个轮次需点击展开才能看到来源
        # 判断是否已展开：该容器下方是否有来源条目，有则已展开（不能再点，点了会折叠）
        search_nodes = find_nodes(nodes, resource_id=SEARCH_REFERENCE_TITLE_CONTAINER_ID)
        for sn in search_nodes:
            sn_parsed = sn.get("parsedBounds")
            if not sn_parsed:
                continue
            cx, cy = sn_parsed["centerX"], sn_parsed["centerY"]
            # 检查该容器下方是否已有来源条目
            sources_below = [s for s in visible_source_items(nodes, REFERENCE_CONTENT_ID) if s["centerY"] > cy]
            if not sources_below:
                # 下方无来源 → 未展开 → 点击展开
                logger.info(f"点击 searchReferenceTitleContainer center=({cx},{cy}) 展开来源列表")
                adb.tap(cx, cy)
                time.sleep(1.0)
                nodes = dump_nodes(adb)  # 重新获取节点

        # 步骤2：收集当前屏幕的来源条目（用标题去重）
        current_items = visible_source_items(nodes, REFERENCE_CONTENT_ID)
        new_count = 0
        for item in current_items:
            title_key = item["title"].strip()
            if title_key and title_key not in seen_titles:
                seen_titles.add(title_key)
                collected_items.append(item)
                new_count += 1

        if new_count > 0:
            logger.info(f"第 {scroll_round + 1}/25 次: 新增 {new_count} 条，累计 {len(collected_items)} 条")
            no_new_count = 0
        else:
            no_new_count += 1
            logger.info(f"第 {scroll_round + 1}/25 次: 无新增来源（连续 {no_new_count} 次），累计 {len(collected_items)} 条")
            # 连续 2 次无新增，认为已到底部
            if no_new_count >= 2:
                logger.info("连续 2 次无新增来源，认为已收集完毕")
                break

        # 步骤3：慢速滑动整个聊天页面，加载更多来源
        scroll_panel_down(adb)

    save_diag_xml(adb, output_dir, "expert-after-panel-scroll")

    if collected_items:
        logger.info(f"慢速滑动后共收集 {len(collected_items)} 条来源")
        return {"expanded": True, "reason": "expert_mode_panel_scroll", "items": collected_items}

    # 最终检查
    nodes = dump_nodes(adb)
    items = visible_source_items(nodes, REFERENCE_CONTENT_ID)
    if items:
        logger.info(f"最终检查发现 {len(items)} 条来源")
        return {"expanded": True, "reason": "final_check", "items": items}

    logger.error("慢速滑动后仍未找到来源条目")
    return {"expanded": False, "reason": "no_sources_after_scroll", "items": []}


# ============================================================================
# 阶段4：贪吃蛇遍历——逐个点击来源，提取 URL
# ============================================================================

def find_source_by_title_realtime(nodes: list[dict], target_title: str) -> dict | None:
    """在当前可见节点中通过标题实时查找来源条目。

    完全不使用任何历史/缓存位置数据，仅依据当前 dump 出的节点匹配。
    返回包含实时 centerX/centerY/bounds/title 的 dict，未找到返回 None。
    """
    current_items = visible_source_items(nodes, REFERENCE_CONTENT_ID)
    target = target_title.strip()
    # 1) 精确匹配优先
    for it in current_items:
        if it["title"].strip() == target:
            return it
    # 2) 包含匹配（标题可能被截断或存在前后差异）
    for it in current_items:
        title = it["title"].strip()
        if target and title and (target in title or title in target):
            return it
    return None


def locate_and_tap_source_realtime(
    adb: AdbClient,
    target_title: str,
    index: int,
    logger: logging.Logger,
    max_scroll_attempts: int = 20,
) -> dict | None:
    """实时抓取来源位置（放弃任何缓存位置），用于后续点击。

    流程：dump 当前节点 → 按标题查找 → 可见则返回实时位置；
          未找到或不可见则慢速滚动后重试，重复"滚动-抓取"循环。

    注意：
    - 本函数仅定位不点击，实际点击由 eat_one_source 用返回的实时位置完成，
      确保不使用任何历史位置数据。
    - 不依赖 ensure_visible（其内部不重新 dump 且用快速滑动，对来源面板无效）。
    - 使用慢速滑动 scroll_panel_down/scroll_panel_up（阶段3验证有效）。
    - 可视范围放宽到消息列表实际区域 (268-1978)，因为来源条目在该区域内均可点击，
      输入栏在 1981 以下。VISIBLE_BOTTOM=1900 过于保守会导致 Y=1917 等可点击条目被误判。
    - 每次滚动后重新 dump 获取最新 Y 坐标，并检测滚动是否有效（Y 是否变化），
      若连续滚动无效则直接返回（避免死循环）。

    返回：找到时返回实时位置 dict {"centerX","centerY","bounds","title"}；
          否则返回 None。
    """
    # 消息列表实际可点击区域（来源面板嵌在 message_list 内，bounds=[0,268][1080,1978]）
    CLICKABLE_TOP = 268
    CLICKABLE_BOTTOM = 1800

    scroll_attempts = 0
    last_y: int | None = None
    no_change_count = 0  # 连续滚动后 Y 未变化的次数

    while scroll_attempts <= max_scroll_attempts:
        nodes = dump_nodes(adb)
        realtime_item = find_source_by_title_realtime(nodes, target_title)
        if realtime_item:
            cy = realtime_item["centerY"]
            cx = realtime_item["centerX"]
            # 放宽可视范围到消息列表实际可点击区域
            if CLICKABLE_TOP <= cy <= CLICKABLE_BOTTOM:
                logger.info(
                    f"[#{index}] 实时抓取到来源位置 center=({cx},{cy}) "
                    f"bounds={realtime_item['bounds']} (滚动 {scroll_attempts} 次后定位)"
                )
                return realtime_item

            # 已找到但不在可点击范围，用慢速滑动滚动页面（快速滑动会导致面板折叠）
            logger.info(f"[#{index}] 来源已找到但不在可视范围 (Y={cy})，慢速滚动使其可见")

            # 检测滚动有效性：若 Y 与上次相同，说明滚动未生效
            if last_y is not None and cy == last_y:
                no_change_count += 1
                if no_change_count >= 3:
                    # 连续 3 次滚动无效，Y 不变，说明已到边界无法再滚动
                    # 该来源虽不在理想可视范围但仍在屏幕内，直接返回让其点击
                    logger.warning(
                        f"[#{index}] 滚动无效(Y={cy}未变)，来源仍在屏幕内，直接返回实时位置"
                    )
                    return realtime_item
            else:
                no_change_count = 0
            last_y = cy

            # 慢速滑动：来源在下方则向上滚动内容（手指上滑），在上方则向下滚动内容（手指下滑）
            if cy > CLICKABLE_BOTTOM:
                scroll_panel_down(adb)
            else:
                scroll_panel_up(adb)
            continue

        # 当前页面未找到来源，慢速向下滑动查找
        scroll_attempts += 1
        if scroll_attempts > max_scroll_attempts:
            break
        logger.info(
            f"[#{index}] 当前页面未找到来源，慢速向下滚动后重试 "
            f"({scroll_attempts}/{max_scroll_attempts})"
        )
        scroll_panel_down(adb)

    return None


def eat_one_source(
    adb: AdbClient,
    item: dict,
    index: int,
    output_dir: Path,
    logger: logging.Logger,
    wait_source: float,
    wait_share: float,
) -> dict:
    """处理单条来源：点击来源 → 点分享 → 点复制链接 → 返回 → 读取剪贴板 URL。"""
    result = {
        "index": index,
        "title": item["title"],
        "sourceTap": {"x": item["centerX"], "y": item["centerY"], "bounds": item["bounds"]},
        "url": "",
        "status": "pending",
        "error": None,
        "steps": [],
    }

    # 步骤1：点击来源条目，进入来源详情页
    logger.info(f"[#{index}] 点击来源条目 center=({item['centerX']},{item['centerY']})")
    adb.tap(item["centerX"], item["centerY"])
    time.sleep(wait_source)
    source_nodes = dump_nodes(adb)
    logger.info(f"[#{index}] 来源页节点数: {len(source_nodes)}")
    result["steps"].append({"step": "tap_source", "status": "done"})

    # 步骤2：查找分享按钮，点击触发分享面板
    share_nodes = find_nodes(source_nodes, resource_id=SHARE_BUTTON_ID)
    if not share_nodes:
        result["status"] = "unsupported"
        result["error"] = "share_button_not_found"
        logger.warning(f"[#{index}] 未找到分享按钮，返回上一页")
        adb.keyevent(4)
        time.sleep(0.5)
        return result

    share_x, share_y = center(share_nodes[-1])
    logger.info(f"[#{index}] 点击分享按钮 center=({share_x},{share_y})")
    adb.tap(share_x, share_y)
    time.sleep(wait_share)
    share_nodes_after = dump_nodes(adb)
    result["steps"].append({"step": "tap_share", "status": "done"})

    # 步骤3：在分享面板查找"复制链接"按钮，点击复制到剪贴板
    copy_nodes = find_nodes(share_nodes_after, text_contains="复制链接")
    if not copy_nodes:
        copy_nodes = [n for n in share_nodes_after if "复制链接" in n.get("content_desc", "")]
    if not copy_nodes:
        result["status"] = "unsupported"
        result["error"] = "copy_link_not_found"
        logger.warning(f"[#{index}] 分享面板未找到'复制链接'，关闭分享面板和来源页")
        adb.keyevent(4)
        time.sleep(0.3)
        adb.keyevent(4)
        time.sleep(0.5)
        return result

    copy_x, copy_y = center(copy_nodes[0])
    logger.info(f"[#{index}] 点击'复制链接' center=({copy_x},{copy_y})")
    adb.tap(copy_x, copy_y)
    time.sleep(1.2)
    result["steps"].append({"step": "tap_copy_link", "status": "done"})

    # 步骤4：返回聊天页
    logger.info(f"[#{index}] 返回聊天页")
    adb.keyevent(4)
    time.sleep(0.6)
    chat_nodes = dump_nodes(adb)
    result["steps"].append({"step": "back_to_chat", "status": "done"})

    # 步骤5：读取剪贴板获取 URL（通过粘贴到输入框读取，快速清空）
    logger.info(f"[#{index}] 读取剪贴板内容")
    paste_text = read_clipboard_via_paste(adb, chat_nodes)
    urls = extract_urls_from_text(paste_text) if paste_text else []

    if urls:
        result["url"] = urls[0]
        result["status"] = "success"
        logger.info(f"[#{index}] 成功提取 URL: {urls[0]}")
    else:
        result["status"] = "no_url_found"
        result["error"] = "no_url_in_clipboard"
        logger.error(f"[#{index}] 剪贴板中未找到 URL, 内容: {paste_text[:80] if paste_text else '(空)'}")

    result["steps"].append({
        "step": "read_clipboard",
        "status": "done",
        "urls": urls,
        "clipboardRaw": paste_text[:200] if paste_text else "",
    })
    return result


# ============================================================================
# 主流程
# ============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="专家模式参考来源链接完整获取测试")
    parser.add_argument("--adb", default=WIN_DEFAULT_ADB, help="Path to adb executable.")
    parser.add_argument("--serial", default=None, help="Device serial. Auto-detected if omitted.")
    parser.add_argument("--output-dir", default="outputs/expert-full", help="Directory to save artifacts.")
    parser.add_argument("--limit", type=int, default=0, help="Max items to process. 0 = all.")
    parser.add_argument("--wait-source", type=float, default=2.0, help="Wait after tapping a source item (s).")
    parser.add_argument("--wait-share", type=float, default=1.0, help="Wait after tapping share button (s).")
    args = parser.parse_args()

    adb_path = resolve_adb(args.adb)
    serial = args.serial or first_device(adb_path)
    if not serial:
        sys.exit("No connected adb device found.")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = stamp()
    log_path = output_dir / f"expert-{ts}.log"
    logger = setup_logger(log_path)

    # 禁用截图和 current_focus，加速 save_state
    set_capture_options(screenshots=False, current_focus=False)

    adb = AdbClient(adb=adb_path, serial=serial)

    logger.info("=" * 70)
    logger.info(f"专家模式参考来源链接完整获取测试  serial={serial}")
    logger.info(f"输出目录: {output_dir}")
    logger.info(f"日志文件: {log_path}")
    logger.info("=" * 70)

    # ------------------------------------------------------------------
    # 阶段1+2+3：检测思考按钮 → 双击展开 → 多轮滚动收集来源
    # ------------------------------------------------------------------
    logger.info("阶段1-3：展开参考资料面板并收集所有来源")
    initial_nodes = dump_nodes(adb)
    logger.info(f"初始节点数: {len(initial_nodes)}")

    expand = expand_expert_mode(adb, initial_nodes, output_dir, logger)
    items = expand.get("items", [])
    logger.info(f"展开结果: expanded={expand.get('expanded')}, reason={expand.get('reason')}, 来源数={len(items)}")

    # if not items:
    #     logger.error("未找到任何参考来源条目，测试终止")
    #     report = {
    #         "status": "failed",
    #         "reason": "no_source_items",
    #         "serial": serial,
    #         "startedAt": ts,
    #         "finishedAt": stamp(),
    #         "expansion": {k: v for k, v in expand.items() if k not in {"items", "state"}},
    #         "items": [],
    #     }
    #     report_path = output_dir / f"expert-report-{ts}.json"
    #     report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    #     logger.info(f"测试报告已保存: {report_path}")
    #     return

    # ------------------------------------------------------------------
    # 阶段4：贪吃蛇遍历——逐个点击来源，提取 URL
    # 先回到页面顶部处理第一个来源，之后不再回顶部，直接在当前位置找下一个。
    # 找不到时由 locate_and_tap_source_realtime 向下滚动查找，
    # 重复"滚动-抓取-点击"循环直至处理完全部来源。
    # 注意：放弃使用阶段3缓存的位置信息，每个来源都实时从当前页面抓取最新位置。
    # ------------------------------------------------------------------
    scroll_to_top(adb, logger)

    limit = args.limit if args.limit > 0 else len(items)
    items_to_process = items[:limit]
    logger.info("=" * 70)
    logger.info(f"阶段4：贪吃蛇遍历（共 {len(items_to_process)} 条来源，limit={limit}）")
    logger.info("模式：实时位置抓取（放弃缓存位置，滚动-抓取-点击循环）")
    logger.info("=" * 70)

    results = []
    success_count = 0
    for index, item in enumerate(items_to_process, start=1):
        target_title = item["title"]
        logger.info(f"--- 处理第 {index}/{len(items_to_process)} 条来源 ---")
        logger.info(f"标题: {target_title[:60]}")
        

        # 放弃缓存位置，实时从当前页面抓取最新位置
        # 若未找到或不可见，向下滚动后重试，重复"滚动-抓取"循环
        realtime_item = locate_and_tap_source_realtime(
            adb, target_title, index, logger, max_scroll_attempts=20
        )

        if not realtime_item:
            # 滚动多次后仍未找到该来源
            item_result = {
                "index": index,
                "title": target_title,
                "status": "failed",
                "error": "source_not_found_after_scroll",
                "url": "",
            }
            logger.error(f"[#{index}] 滚动 20 次后仍未找到来源: {target_title[:60]}")
            results.append(item_result)
            logger.info(f"[#{index}] 完成，状态={item_result['status']}")
            logger.info("-" * 50)
            # 不回顶部，直接在当前位置继续找下一个来源
            continue

        # 用实时位置（非缓存位置）调用 eat_one_source 进行点击+提取
        try:
            item_result = eat_one_source(
                adb, realtime_item, index, output_dir, logger, args.wait_source, args.wait_share
            )
        except Exception as exc:
            item_result = {
                "index": index,
                "title": target_title,
                "status": "failed",
                "error": f"exception: {exc}",
                "url": "",
            }
            logger.exception(f"[#{index}] 处理异常: {exc}")
            try:
                adb.keyevent(4)
                time.sleep(0.8)
            except Exception:
                pass

        results.append(item_result)
        if item_result["status"] == "success":
            success_count += 1
        logger.info(f"[#{index}] 完成，状态={item_result['status']}")
        logger.info("-" * 50)
        # 不回顶部，直接在当前位置继续找下一个来源（找不到时由 locate_and_tap_source_realtime 向下滚动）


    # ------------------------------------------------------------------
    # 阶段6：生成测试报告
    # ------------------------------------------------------------------
    finished = stamp()
    total = len(results)
    failed = total - success_count
    report = {
        "status": "success" if success_count == total else ("partial" if success_count > 0 else "failed"),
        "serial": serial,
        "startedAt": ts,
        "finishedAt": finished,
        "visibleSourceCount": len(items),
        "attemptedCount": total,
        "successCount": success_count,
        "failedCount": failed,
        "expansion": {k: v for k, v in expand.items() if k not in {"items", "state"}},
        "items": results,
    }
    report_path = output_dir / f"expert-report-{ts}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info("=" * 70)
    logger.info("测试报告")
    logger.info("=" * 70)
    logger.info(f"总状态: {report['status']}")
    logger.info(f"可见来源数: {report['visibleSourceCount']}")
    logger.info(f"额外发现数: {report['additionalSourceCount']}")
    logger.info(f"尝试处理数: {report['attemptedCount']}")
    logger.info(f"成功数: {report['successCount']}")
    logger.info(f"失败数: {report['failedCount']}")
    logger.info("-" * 50)
    for r in results:
        url_display = r.get("url") or "(无)"
        logger.info(f"  #{r['index']} [{r['status']}] {url_display}  | {r.get('error') or ''}")
    logger.info("-" * 50)
    logger.info(f"详细报告: {report_path}")
    logger.info(f"日志文件: {log_path}")
    logger.info("=" * 70)


if __name__ == "__main__":
    main()
