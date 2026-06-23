"""Test script for reference source link extraction (snake-eat mode).

Iterates through ALL visible reference source items one by one (like a snake
eating them), reusing the project's expand + share-copy-paste logic to extract
the real URL of each source.

Usage (PowerShell):
    python scripts/test_source_links_snake.py --serial 100.76.50.7:6666
    python scripts/test_source_links_snake.py --serial <serial> --limit 3 --output-dir outputs/source-snake

The script:
  1. Reuses expand_references_if_needed() to expand the reference panel
     (supports both normal and expert/deep-think modes).
  2. Collects all visible source items via visible_source_items().
  3. For each item (snake-eat): tap source → tap share → tap copy link → back →
     paste into input → read URL → clear input. Reuses
     paste_clipboard_into_input_and_read() and clear_input_if_visible().
  4. Logs every step and writes a JSON test report.
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

# 将项目根目录加入 sys.path，以便复用 mobile_auto_doubao 包内的逻辑
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 复用项目模块，避免重复开发
from mobile_auto_doubao.adb_client import AdbClient
from mobile_auto_doubao.artifacts import save_state, set_capture_options
from mobile_auto_doubao.constants import (
    DEFAULT_ADB,
    REFERENCE_CONTENT_ID,
    SHARE_BUTTON_ID,
)
from mobile_auto_doubao.doubao_app import center
from mobile_auto_doubao.source_links import expand_references_if_needed
from mobile_auto_doubao.ui_xml import collect_nodes, extract_urls_from_text, find_nodes, visible_source_items


# Windows default adb path (matches the WSL path used elsewhere in the repo).
WIN_DEFAULT_ADB = r"C:\Users\Administrator\AppData\Local\Android\Sdk\platform-tools\adb.exe"


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def setup_logger(log_path: Path) -> logging.Logger:
    """Configure a logger that writes to both stdout and a log file (immediate flush)."""
    logger = logging.getLogger("source_snake")
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
    """Return a usable adb executable path."""
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
    """轻量级 UI 探测：只 dump XML 并解析节点，不做截图和 current_focus，速度比 save_state 快 3-5 倍。"""
    xml = adb.dump_xml()
    return collect_nodes(xml)


def read_clipboard(adb: AdbClient) -> str:
    """直接通过 adb 读取剪贴板文本，不依赖输入框粘贴。

    尝试多种方法读取剪贴板：
    1. service call clipboard（解析 Parcel 二进制数据）
    2. dumpsys clipboard（解析文本行）
    """
    # 方法1：service call clipboard — 解析 Parcel 中的 UTF-16 文本
    result = adb.command(["shell", "service", "call", "clipboard", "1", "i32", "1", "i32", "0", "i32", "0"], check=False)
    if result.returncode == 0 and result.stdout:
        # Parcel 输出中包含 UTF-16LE 编码的文本，提取可打印字符
        raw = result.stdout
        # 提取引号内的文本内容（Parcel 格式中文本通常在引号内）
        import re
        # 尝试从 Parcel hex dump 中提取 ASCII/UTF-16 文本
        chars = []
        for match in re.finditer(r"'(.{1,4})'", raw):
            segment = match.group(1)
            for ch in segment:
                if ch.isprintable() and ch != "'":
                    chars.append(ch)
        text = "".join(chars).strip()
        if text and "No items" not in text and "Exception" not in text:
            return text

    # 方法2：dumpsys clipboard
    result = adb.command(["shell", "dumpsys", "clipboard"], check=False)
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("Text:") or line.startswith("text:"):
            return line.split(":", 1)[1].strip().strip('"')
    return ""


def read_clipboard_via_paste(adb: AdbClient, nodes: list[dict]) -> str:
    """通过粘贴到输入框读取剪贴板内容（快速清空，不使用320次backspace）。

    步骤：点击输入框 → 粘贴 → 读取文本 → broadcast_clear_text 快速清空。
    仅在输入框可见时使用，避免误导航。
    """
    from mobile_auto_doubao.constants import INPUT_ID
    from mobile_auto_doubao.constants import ADB_KEYBOARD_IME

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
    urls = []
    for node in find_nodes(pasted_nodes, resource_id=INPUT_ID):
        text = node.get("text", "")
        if text:
            urls.append(text)

    # 快速清空输入框（broadcast_clear_text，不使用320次backspace）
    try:
        previous_ime = adb.current_ime()
        if previous_ime != ADB_KEYBOARD_IME:
            adb.set_ime(ADB_KEYBOARD_IME)
        adb.broadcast_clear_text()
        time.sleep(0.3)
    except Exception:
        pass

    return urls[0] if urls else ""


def eat_one_source(
    adb: AdbClient,
    item: dict,
    index: int,
    output_dir: Path,
    logger: logging.Logger,
    wait_source: float,
    wait_share: float,
) -> dict:
    """处理单条来源：点击来源 → 点分享 → 点复制链接 → 返回 → 粘贴读取 URL。

    使用轻量级 dump_nodes 替代 save_state，避免截图和 current_focus 的开销。
    """
    result = {
        "index": index,
        "title": item["title"],
        "sourceTap": {"x": item["centerX"], "y": item["centerY"], "bounds": item["bounds"]},
        "url": "",
        "status": "pending",
        "error": None,
        "steps": [],
    }

    # ------------------------------------------------------------------
    # 步骤1：点击来源条目，进入来源详情页
    # ------------------------------------------------------------------
    logger.info(f"[#{index}] 点击来源条目 center=({item['centerX']},{item['centerY']})")
    adb.tap(item["centerX"], item["centerY"])
    time.sleep(wait_source)
    # 保存点击后的 XML 用于诊断（轻量级，不截图）
    diag_xml = adb.dump_xml()
    diag_path = output_dir / f"snake-{index}-source-page-{stamp()}.xml"
    diag_path.write_text(diag_xml, encoding="utf-8")
    source_nodes = collect_nodes(diag_xml)
    logger.info(f"[#{index}] 来源页节点数: {len(source_nodes)}, XML 已保存: {diag_path.name}")
    result["steps"].append({"step": "tap_source", "status": "done", "xml": str(diag_path)})

    # ------------------------------------------------------------------
    # 步骤2：查找分享按钮（btn_share），点击触发分享面板
    # ------------------------------------------------------------------
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
    # 保存分享面板 XML 用于诊断
    share_xml = adb.dump_xml()
    share_diag_path = output_dir / f"snake-{index}-share-sheet-{stamp()}.xml"
    share_diag_path.write_text(share_xml, encoding="utf-8")
    share_nodes_after = collect_nodes(share_xml)
    logger.info(f"[#{index}] 分享面板节点数: {len(share_nodes_after)}, XML: {share_diag_path.name}")
    result["steps"].append({"step": "tap_share", "status": "done", "xml": str(share_diag_path)})

    # ------------------------------------------------------------------
    # 步骤3：在分享面板查找"复制链接"按钮，点击复制到剪贴板
    # ------------------------------------------------------------------
    copy_nodes = find_nodes(share_nodes_after, text_contains="复制链接")
    if not copy_nodes:
        # 也尝试通过 content-desc 查找
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
    logger.info(f"[#{index}] 点击'复制链接' center=({copy_x},{copy_y}) text='{copy_nodes[0].get('text', '')}'")
    adb.tap(copy_x, copy_y)
    time.sleep(1.5)  # 等待复制操作完成
    result["steps"].append({"step": "tap_copy_link", "status": "done"})

    # ------------------------------------------------------------------
    # 步骤4：返回聊天页（来源详情页 → 聊天页）
    # ------------------------------------------------------------------
    logger.info(f"[#{index}] 返回聊天页")
    adb.keyevent(4)
    time.sleep(0.8)
    # 返回后 dump 一次，用于读取剪贴板时查找输入框
    chat_nodes = dump_nodes(adb)
    result["steps"].append({"step": "back_to_chat", "status": "done"})

    # ------------------------------------------------------------------
    # 步骤5：读取剪贴板获取 URL（两种方法：直接读取 + 粘贴读取）
    # ------------------------------------------------------------------
    logger.info(f"[#{index}] 读取剪贴板内容")
    # 方法1：直接读取剪贴板（不点击输入框）
    clipboard_text = read_clipboard(adb)
    urls = extract_urls_from_text(clipboard_text) if clipboard_text else []

    # 方法2：如果方法1失败，通过粘贴到输入框读取（快速清空，不使用320次backspace）
    if not urls:
        logger.info(f"[#{index}] 直接读取剪贴板失败，尝试粘贴到输入框读取")
        paste_text = read_clipboard_via_paste(adb, chat_nodes)
        if paste_text:
            clipboard_text = paste_text
            urls = extract_urls_from_text(paste_text)

    if urls:
        result["url"] = urls[0]
        result["status"] = "success"
        logger.info(f"[#{index}] 成功提取 URL: {urls[0]}")
    else:
        result["status"] = "no_url_found"
        result["error"] = "no_url_in_clipboard"
        logger.error(f"[#{index}] 剪贴板中未找到 URL, 剪贴板内容: {clipboard_text[:100] if clipboard_text else '(空)'}")

    result["steps"].append({
        "step": "read_clipboard",
        "status": "done",
        "urls": urls,
        "clipboardRaw": clipboard_text[:200] if clipboard_text else "",
    })
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Snake-eat test: extract URLs from all reference sources.")
    parser.add_argument("--adb", default=WIN_DEFAULT_ADB, help="Path to adb executable.")
    parser.add_argument("--serial", default=None, help="Device serial. Auto-detected if omitted.")
    parser.add_argument("--output-dir", default="outputs/source-snake", help="Directory to save artifacts.")
    parser.add_argument("--limit", type=int, default=0, help="Max items to process. 0 = all (snake-eat mode).")
    parser.add_argument("--wait-source", type=float, default=2.0, help="Wait after tapping a source item (s).")
    parser.add_argument("--wait-share", type=float, default=1.0, help="Wait after tapping share button (s).")
    parser.add_argument("--skip-expand", action="store_true", help="跳过展开参考资料面板步骤，直接从当前界面提取来源链接。")
    args = parser.parse_args()

    adb_path = resolve_adb(args.adb)
    serial = args.serial or first_device(adb_path)
    if not serial:
        sys.exit("No connected adb device found.")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = stamp()
    log_path = output_dir / f"snake-{ts}.log"
    logger = setup_logger(log_path)

    # 禁用截图和 current_focus，大幅加速 save_state（仅保留 dump_xml + collect_nodes）
    set_capture_options(screenshots=False, current_focus=False)

    # 构建 AdbClient（复用项目封装）
    adb = AdbClient(adb=adb_path, serial=serial)

    logger.info("=" * 70)
    logger.info(f"参考来源链接提取测试（贪吃蛇模式）  serial={serial}")
    logger.info(f"输出目录: {output_dir}")
    logger.info(f"日志文件: {log_path}")
    logger.info("=" * 70)

    # ------------------------------------------------------------------
    # 阶段1：探测当前界面，查找来源条目（不做清空输入框操作，避免 320 次 backspace 卡死）
    # ------------------------------------------------------------------
    logger.info("阶段1：探测当前界面状态")
    initial_nodes = dump_nodes(adb)
    logger.info(f"节点数: {len(initial_nodes)}")

    # ------------------------------------------------------------------
    # 阶段2：展开参考资料面板（复用项目逻辑，支持普通/专家模式）
    # --skip-expand 时跳过此步骤，直接从当前界面查找来源条目
    # ------------------------------------------------------------------
    if args.skip_expand:
        logger.info("阶段2：跳过展开参考资料面板（--skip-expand）")
        items = visible_source_items(initial_nodes, REFERENCE_CONTENT_ID)
        expand = {"expanded": False, "reason": "skipped", "items": items}
    else:
        logger.info("阶段2：展开参考资料面板")
        expand = expand_references_if_needed(adb, initial_nodes, str(output_dir))
        items = expand.get("items", [])
    logger.info(f"来源条目数={len(items)}" + (f", reason={expand.get('reason')}" if not args.skip_expand else ""))

    if not items:
        logger.error("未找到任何参考来源条目，测试终止")
        report = {
            "status": "failed",
            "reason": "no_source_items",
            "serial": serial,
            "startedAt": ts,
            "finishedAt": stamp(),
            "expansion": {k: v for k, v in expand.items() if k not in {"items", "state"}},
            "items": [],
        }
        report_path = output_dir / f"snake-report-{ts}.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"测试报告已保存: {report_path}")
        return

    # ------------------------------------------------------------------
    # 阶段3：贪吃蛇遍历——逐个点击来源，提取 URL
    # ------------------------------------------------------------------
    limit = args.limit if args.limit > 0 else len(items)
    items_to_process = items[:limit]
    logger.info(f"阶段3：贪吃蛇遍历（共 {len(items_to_process)} 条来源，limit={limit}）")
    logger.info("-" * 70)

    results = []
    success_count = 0
    for index, item in enumerate(items_to_process, start=1):
        logger.info(f"--- 处理第 {index}/{len(items_to_process)} 条来源 ---")
        logger.info(f"标题: {item['title'][:60]}")
        try:
            item_result = eat_one_source(
                adb, item, index, output_dir, logger, args.wait_source, args.wait_share
            )
        except Exception as exc:
            item_result = {
                "index": index,
                "title": item["title"],
                "status": "failed",
                "error": f"exception: {exc}",
                "url": "",
            }
            logger.exception(f"[#{index}] 处理异常: {exc}")
            # 异常后尝试返回聊天页，避免卡在来源页
            try:
                adb.keyevent(4)
                time.sleep(1.0)
            except Exception:
                pass

        results.append(item_result)
        if item_result["status"] == "success":
            success_count += 1

        # 输入框清空失败时终止整个流程（避免污染后续条目）
        if item_result.get("error") == "input_clear_failed_after_paste":
            logger.error(f"[#{index}] 输入框清空失败，终止贪吃蛇遍历")
            break

        logger.info(f"[#{index}] 完成，状态={item_result['status']}")
        logger.info("-" * 70)

    # ------------------------------------------------------------------
    # 阶段4：生成测试报告
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
    report_path = output_dir / f"snake-report-{ts}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info("=" * 70)
    logger.info("测试报告")
    logger.info("=" * 70)
    logger.info(f"总状态: {report['status']}")
    logger.info(f"可见来源数: {report['visibleSourceCount']}")
    logger.info(f"尝试处理数: {report['attemptedCount']}")
    logger.info(f"成功数: {report['successCount']}")
    logger.info(f"失败数: {report['failedCount']}")
    logger.info("-" * 70)
    for r in results:
        url_display = r.get("url") or "(无)"
        logger.info(f"  #{r['index']} [{r['status']}] {url_display}  | {r.get('error') or ''}")
    logger.info("-" * 70)
    logger.info(f"详细报告: {report_path}")
    logger.info(f"日志文件: {log_path}")
    logger.info("=" * 70)


if __name__ == "__main__":
    main()
