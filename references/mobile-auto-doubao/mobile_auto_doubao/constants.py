from pathlib import Path
import subprocess


WINDOWS_ADB = r"C:\Users\Administrator\AppData\Local\Android\Sdk\platform-tools\adb.exe"
WSL_ADB = "/mnt/c/Users/Administrator/AppData/Local/Android/Sdk/platform-tools/adb.exe"
DEFAULT_ADB = WINDOWS_ADB if Path(WINDOWS_ADB).exists() else WSL_ADB


def _detect_default_serial() -> str | None:
    """Detect the only connected adb serial, if there is exactly one."""
    adb_path = DEFAULT_ADB
    if not Path(adb_path).exists():
        return None
    try:
        proc = subprocess.run([adb_path, "devices"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
    except Exception:
        return None
    devices = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices attached"):
            continue
        if "\tdevice" in line:
            devices.append(line.split("\t", 1)[0].strip())
    if len(devices) == 1:
        return devices[0]
    return None


DEFAULT_SERIAL = _detect_default_serial()
DOUBAO_PACKAGE = "com.larus.nova"
REFERENCE_CONTENT_ID = "com.larus.nova:id/tv_reference_content"
INPUT_ID = "com.larus.nova:id/input_text"
SIDEBAR_CREATE_CONVERSATION_ID = "com.larus.nova:id/side_bar_create_conversation"
SHARE_BUTTON_ID = "com.larus.nova:id/btn_share"
ADB_KEYBOARD_IME = "com.android.adbkeyboard/.AdbIME"

ACTION_SEND_ID = "com.larus.nova:id/action_send"
REFERENCE_TITLE_ID = "com.larus.nova:id/tv_reference_title"
# 参考资料标题的可点击容器（tv_reference_title 本身 clickable=false，需点击其父容器）
REFERENCE_TITLE_CLICKABLE_ID = "com.larus.nova:id/ll_reference_title"
# 专家模式（深度思考）下，思考过程中"搜索N个关键词，参考N篇资料"的可点击容器
# 第一次点击 ll_reference_title 只展开思考过程，需第二次点击此容器才展开来源列表
SEARCH_REFERENCE_TITLE_CONTAINER_ID = "com.larus.nova:id/searchReferenceTitleContainer"
