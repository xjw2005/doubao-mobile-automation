from pathlib import Path

from .adb_client import AdbClient
from .time_utils import stamp
from .ui_xml import collect_nodes, extract_urls_from_nodes


_CAPTURE_OPTIONS = {"screenshots": True, "currentFocus": True, "debug": True}


def set_capture_options(screenshots: bool = True, current_focus: bool = True, debug: bool = True) -> None:
    """Toggle which debug artifacts are collected by save_state."""
    _CAPTURE_OPTIONS["screenshots"] = screenshots
    _CAPTURE_OPTIONS["currentFocus"] = current_focus
    _CAPTURE_OPTIONS["debug"] = debug


def save_state(adb: AdbClient, output_dir: str | Path, label: str) -> dict:
    """Capture the current XML, nodes, URLs, screenshot, and focus state."""
    # dump_xml + collect_nodes 是逻辑必需的，始终执行；
    # 截图 / current_focus / 写 XML 文件仅在 debug 模式下做，非调试模式跳过以节省每次约 1.5s。
    xml = adb.dump_xml()
    nodes = collect_nodes(xml)
    urls = extract_urls_from_nodes(nodes)
    if not _CAPTURE_OPTIONS.get("debug", True):
        return {"xml": "", "screenshot": None, "nodes": nodes, "urls": urls, "currentFocus": ""}
    base = Path(output_dir)
    base.mkdir(parents=True, exist_ok=True)
    ts = stamp()
    xml_path = base / f"{label}-{ts}.xml"
    xml_path.write_text(xml, encoding="utf-8")
    screenshot_path = base / f"{label}-{ts}.png"
    screenshot = None
    if _CAPTURE_OPTIONS["screenshots"]:
        screenshot = str(screenshot_path) if adb.screenshot(screenshot_path) else None
    current_focus = adb.current_focus() if _CAPTURE_OPTIONS["currentFocus"] else ""
    return {
        "xml": str(xml_path),
        "screenshot": screenshot,
        "nodes": nodes,
        "urls": urls,
        "currentFocus": current_focus,
    }
