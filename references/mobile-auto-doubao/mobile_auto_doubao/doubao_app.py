import time

from .adb_client import AdbClient
from .artifacts import save_state
from .constants import ADB_KEYBOARD_IME, DOUBAO_PACKAGE, INPUT_ID, ACTION_SEND_ID, SIDEBAR_CREATE_CONVERSATION_ID
from .ui_xml import find_nodes, visible_texts


LOGIN_KEYWORDS = ("登录", "手机号", "验证码", "注册", "同意并")
NEW_CHAT_KEYWORDS = ("新对话", "新建", "开启新对话")
CREATE_NEW_CHAT_KEYWORDS = ("创建新对话",)
THINKING_TEXT = "深度思考"
EXPERT_MODE_TEXTS = ("专家", "深度思考")
FAST_MODE_TEXT = "快速"
SEND_TEXTS = ("发送",)


def detect_blocked(nodes: list[dict]) -> str | None:
    """Detect whether the app is blocked by a login or gate screen."""
    # Temporarily disable login-state blocking.
    # The previous heuristic was too broad and could stop valid sessions when
    # the page merely contained login-related wording.
    # text = "\n".join(visible_texts(nodes))
    # for keyword in LOGIN_KEYWORDS:
    #     if keyword in text:
    #         return "login_required"
    return None


def center(node: dict) -> tuple[int, int]:
    """Return the center point of a node's parsed bounds."""
    bounds = node["parsedBounds"]
    return bounds["centerX"], bounds["centerY"]


def ensure_app(adb: AdbClient, package: str = DOUBAO_PACKAGE) -> None:
    """Bring the target app to the foreground."""
    focus = adb.current_focus()
    if package not in focus:
        adb.start_app(package)
        time.sleep(1.2)


def tap_first_by_text(adb: AdbClient, nodes: list[dict], keywords: tuple[str, ...]) -> bool:
    """Tap the first visible node whose text matches one of the keywords."""
    for keyword in keywords:
        for node in find_nodes(nodes, text_contains=keyword):
            x, y = center(node)
            adb.tap(x, y)
            return True
    return False


def tap_top_right_plus(adb: AdbClient, nodes: list[dict]) -> bool:
    """Tap the top-right plus button if it is visible."""
    candidates = []
    for node in nodes:
        bounds = node.get("parsedBounds")
        if not bounds:
            continue
        text = node.get("text", "") + node.get("content_desc", "")
        if "+" in text or "加" in text or node.get("clickable") == "true":
            if bounds["centerX"] > 430 and bounds["centerY"] < 130:
                candidates.append(node)
    if not candidates:
        return False
    candidate = sorted(candidates, key=lambda item: item["parsedBounds"]["centerX"], reverse=True)[0]
    x, y = center(candidate)
    adb.tap(x, y)
    return True


def tap_top_left_sidebar(adb: AdbClient, nodes: list[dict]) -> dict | None:
    """Tap the top-left back/sidebar control when present."""
    candidates = []
    for node in nodes:
        bounds = node.get("parsedBounds")
        if not bounds or node.get("clickable") != "true":
            continue
        text = " ".join([node.get("text", ""), node.get("content_desc", ""), node.get("resource_id", "")])
        if "返回" in text or "back" in text:
            if bounds["centerX"] < 180 and bounds["centerY"] < 280:
                candidates.append(node)
    if not candidates:
        return None
    target = sorted(candidates, key=lambda item: (item["parsedBounds"]["centerY"], item["parsedBounds"]["centerX"]))[0]
    x, y = center(target)
    adb.tap(x, y)
    return {"x": x, "y": y, "bounds": target.get("bounds", ""), "resourceId": target.get("resource_id", ""), "contentDesc": target.get("content_desc", "")}


def tap_sidebar_create_conversation(adb: AdbClient, nodes: list[dict]) -> dict | None:
    """Tap the sidebar entry that creates a new conversation."""
    matches = find_nodes(nodes, resource_id=SIDEBAR_CREATE_CONVERSATION_ID)
    if matches:
        target = matches[-1]
        x, y = center(target)
        adb.tap(x, y)
        return {"x": x, "y": y, "bounds": target.get("bounds", ""), "resourceId": target.get("resource_id", ""), "contentDesc": target.get("content_desc", "")}
    for node in nodes:
        bounds = node.get("parsedBounds")
        if not bounds or node.get("clickable") != "true":
            continue
        if node.get("content_desc") == "创建新对话":
            x, y = center(node)
            adb.tap(x, y)
            return {"x": x, "y": y, "bounds": node.get("bounds", ""), "resourceId": node.get("resource_id", ""), "contentDesc": node.get("content_desc", "")}
    return None


def has_input(nodes: list[dict]) -> bool:
    """Check whether the current page exposes a chat input field."""
    return bool(find_nodes(nodes, resource_id=INPUT_ID))


def containing_clickable_node(nodes: list[dict], child: dict) -> dict | None:
    """Find the smallest clickable ancestor that contains a child node."""
    child_bounds = child.get("parsedBounds")
    if not child_bounds:
        return None
    containing = []
    for node in nodes:
        bounds = node.get("parsedBounds")
        if not bounds or node.get("clickable") != "true":
            continue
        if (bounds["left"] <= child_bounds["centerX"] <= bounds["right"]
                and bounds["top"] <= child_bounds["centerY"] <= bounds["bottom"]):
            area = (bounds["right"] - bounds["left"]) * (bounds["bottom"] - bounds["top"])
            containing.append((area, node))
    if not containing:
        return None
    return sorted(containing, key=lambda item: item[0])[0][1]


def tap_text_or_container(adb: AdbClient, nodes: list[dict], text: str) -> bool:
    """Tap a text node or its clickable container by visible text."""
    matches = find_nodes(nodes, text_contains=text)
    if not matches:
        return False
    match = matches[-1]
    target = containing_clickable_node(nodes, match) or match
    x, y = center(target)
    adb.tap(x, y)
    return True


def tap_send_button(adb: AdbClient, nodes: list[dict]) -> bool:
    """Tap the send button if the action node is visible."""
    send_nodes = find_nodes(nodes, resource_id=ACTION_SEND_ID)
    if not send_nodes:
        return False
    x, y = center(send_nodes[-1])
    adb.tap(x, y)
    return True


def is_new_chat_page(nodes: list[dict]) -> bool:
    """Check whether the app is showing an empty new-chat page."""
    if not has_input(nodes):
        return False
    # Real devices may show "新对话" and expose the thinking mode as "快速" instead of "深度思考".
    return bool(find_nodes(nodes, text_exact="聊聊新话题") or find_nodes(nodes, text_exact="新对话"))


def is_blank_new_chat_page(nodes: list[dict]) -> bool:
    """Check whether the current page is a blank new-chat screen."""
    if not has_input(nodes):
        return False
    text = "\n".join(visible_texts(nodes))
    return "聊聊新话题" in text or "嗨，我是豆包" in text


def is_chat_list_page(nodes: list[dict]) -> bool:
    """Check whether the current page looks like the chat list."""
    return bool(find_nodes(nodes, text_exact="对话") and find_nodes(nodes, text_exact="豆包"))


def navigate_to_chat_list(adb: AdbClient, output_dir: str, max_steps: int = 4) -> dict:
    """Walk backwards until the chat list page appears."""
    states = []
    for step in range(max_steps + 1):
        state = save_state(adb, output_dir, f"new-chat-list-step-{step}")
        texts = visible_texts(state["nodes"])
        states.append({"step": step, "xml": state["xml"], "texts": texts[:8]})
        if is_chat_list_page(state["nodes"]):
            return {"ok": True, "method": "back-loop", "steps": states, "state": state}
        if step == max_steps:
            break
        adb.keyevent(4)
        time.sleep(0.5)
    return {"ok": False, "method": "back-loop", "steps": states}


def create_new_chat(adb: AdbClient, output_dir: str) -> dict:
    """Open a fresh chat conversation using the best available UI path."""
    before = save_state(adb, output_dir, "new-chat-before")
    if detect_blocked(before["nodes"]):
        return {"created": False, "method": "blocked", "error": detect_blocked(before["nodes"])}
    if is_blank_new_chat_page(before["nodes"]):
        return {"created": True, "method": "already-blank-new-chat"}
    create_tap = tap_sidebar_create_conversation(adb, before["nodes"])
    if create_tap:
        time.sleep(0.8)
        after_create = save_state(adb, output_dir, "new-chat-after-create")
        return {"created": is_new_chat_page(after_create["nodes"]) or has_input(after_create["nodes"]), "method": "sidebar-create-already-open", "createTap": create_tap}
    sidebar_tap = tap_top_left_sidebar(adb, before["nodes"])
    if sidebar_tap:
        time.sleep(0.5)
        sidebar = save_state(adb, output_dir, "new-chat-after-sidebar")
        create_tap = tap_sidebar_create_conversation(adb, sidebar["nodes"])
        if create_tap:
            time.sleep(0.8)
            after_create = save_state(adb, output_dir, "new-chat-after-create")
            return {"created": is_new_chat_page(after_create["nodes"]) or has_input(after_create["nodes"]), "method": "sidebar-create", "sidebarTap": sidebar_tap, "createTap": create_tap}
        return {"created": False, "method": "sidebar-create", "error": "sidebar_create_conversation_not_found", "sidebarTap": sidebar_tap, "sidebarXml": sidebar["xml"], "texts": visible_texts(sidebar["nodes"])[:12]}
    if tap_first_by_text(adb, before["nodes"], CREATE_NEW_CHAT_KEYWORDS):
        time.sleep(0.8)
        after = save_state(adb, output_dir, "new-chat-after-create")
        return {"created": is_new_chat_page(after["nodes"]) or has_input(after["nodes"]), "method": "visible-create-control"}
    chat_list = navigate_to_chat_list(adb, output_dir)
    if not chat_list["ok"]:
        return {"created": False, "method": "navigate-list", "error": "chat_list_not_reached", "steps": chat_list["steps"]}
    list_state = chat_list["state"]
    if tap_top_right_plus(adb, list_state["nodes"]):
        time.sleep(0.5)
        after_plus = save_state(adb, output_dir, "new-chat-after-plus")
        if tap_first_by_text(adb, after_plus["nodes"], CREATE_NEW_CHAT_KEYWORDS):
            time.sleep(0.8)
            after_create = save_state(adb, output_dir, "new-chat-after-create")
            return {"created": is_new_chat_page(after_create["nodes"]) or has_input(after_create["nodes"]), "method": "list-plus-create", "navigation": {"method": chat_list["method"]}}
        return {"created": False, "method": "list-plus", "error": "create_new_chat_menu_item_not_found"}
    return {"created": False, "method": "list-plus", "error": "top_right_plus_not_found"}


def thinking_selected(nodes: list[dict]) -> bool | None:
    """Infer whether deep-thinking mode is currently enabled."""
    for node in nodes:
        text = " ".join([node.get("text", ""), node.get("content_desc", "")])
        if any(name in text for name in EXPERT_MODE_TEXTS):
            if any(mark in text for mark in ("已开启", "已选择", "选中", "关闭深度思考")):
                return True
            if node.get("selected") == "true" or node.get("checked") == "true":
                return True
        if FAST_MODE_TEXT in text and any(mark in text for mark in ("已开启", "已选择", "选中")):
            return False
    for node in find_nodes(nodes, text_contains=THINKING_TEXT):
        text = " ".join([node.get("text", ""), node.get("content_desc", "")])
        if any(mark in text for mark in ("已开启", "已选择", "选中", "关闭深度思考")):
            return True
        if node.get("selected") == "true" or node.get("checked") == "true":
            return True
        if node.get("selected") == "false" or node.get("checked") == "false":
            return False
    return None


def thinking_chip_target(nodes: list[dict]) -> tuple[int, int, str] | None:
    """Find the UI target used to open the thinking-mode selector."""
    matches = []
    for node in nodes:
        text = " ".join([node.get("text", ""), node.get("content_desc", "")])
        bounds = node.get("parsedBounds")
        if not bounds:
            continue
        if any(name in text for name in (*EXPERT_MODE_TEXTS, FAST_MODE_TEXT)):
            if bounds["centerY"] > 1800:
                matches.append(node)
    if not matches:
        matches = find_nodes(nodes, text_contains=THINKING_TEXT)
    if not matches:
        return None
    match = sorted(matches, key=lambda node: (node["parsedBounds"]["centerY"], node["parsedBounds"]["centerX"]))[0]
    target = containing_clickable_node(nodes, match)
    if target:
        x, y = center(target)
        return x, y, target.get("bounds", "")
    bounds = match.get("parsedBounds")
    if not bounds:
        return None
    return max(18, bounds["left"] - 45), bounds["centerY"], match.get("bounds", "")


def mode_menu_target(nodes: list[dict], enabled: bool) -> tuple[int, int, str] | None:
    """Find the menu item that toggles thinking or fast mode."""
    keywords = EXPERT_MODE_TEXTS if enabled else (FAST_MODE_TEXT,)
    candidates = []
    for node in nodes:
        bounds = node.get("parsedBounds")
        if not bounds:
            continue
        text = " ".join([node.get("text", ""), node.get("content_desc", "")])
        if not any(keyword in text for keyword in keywords):
            continue
        if bounds["centerY"] < 1000:
            continue
        target = containing_clickable_node(nodes, node)
        if target:
            tb = target.get("parsedBounds")
            if tb:
                candidates.append(target)
        elif node.get("clickable") == "true":
            candidates.append(node)
    if not candidates:
        return None
    target = sorted(candidates, key=lambda item: (item["parsedBounds"]["centerY"], item["parsedBounds"]["centerX"]))[0]
    x, y = center(target)
    return x, y, target.get("bounds", "")


def set_thinking_mode(adb: AdbClient, output_dir: str, enabled: bool) -> dict:
    """Enable or disable deep-thinking mode in the app UI."""
    before = save_state(adb, output_dir, "thinking-before")
    nodes = before["nodes"]
    current = thinking_selected(nodes)
    if not enabled:
        if current is False:
            if has_input(nodes):
                return {"requested": enabled, "changed": False, "verified": True, "state": current}
            adb.keyevent(4)
            time.sleep(0.3)
            after_back = save_state(adb, output_dir, "thinking-after-back")
            back_state = thinking_selected(after_back["nodes"])
            return {
                "requested": enabled,
                "changed": False,
                "verified": True if has_input(after_back["nodes"]) else back_state is False,
                "state": current,
                "stateAfterBack": back_state,
                "back": {"performed": True},
            }
        target = thinking_chip_target(nodes)
        if not target:
            return {"requested": enabled, "changed": False, "verified": False, "error": "thinking_chip_not_found"}
        x, y, bounds = target
        adb.tap(x, y)
        time.sleep(0.5)
        after = save_state(adb, output_dir, "thinking-after")
        new_state = thinking_selected(after["nodes"])
        if has_input(after["nodes"]):
            return {"requested": enabled, "changed": True, "verified": new_state is False, "stateBefore": current, "stateAfter": new_state, "tap": {"x": x, "y": y, "bounds": bounds}}
        adb.keyevent(4)
        time.sleep(0.3)
        after_back = save_state(adb, output_dir, "thinking-after-back")
        back_state = thinking_selected(after_back["nodes"])
        return {
            "requested": enabled,
            "changed": True,
            "verified": True if has_input(after_back["nodes"]) else back_state is False,
            "stateBefore": current,
            "stateAfter": new_state,
            "stateAfterBack": back_state,
            "tap": {"x": x, "y": y, "bounds": bounds},
            "back": {"performed": True},
        }
    target = thinking_chip_target(nodes)
    if not target:
        return {"requested": enabled, "changed": False, "verified": False, "error": "thinking_chip_not_found"}
    x, y, bounds = target
    adb.tap(x, y)
    time.sleep(0.6)
    menu = save_state(adb, output_dir, "thinking-menu")
    menu_target = mode_menu_target(menu["nodes"], enabled)
    if menu_target:
        mx, my, menu_bounds = menu_target
        adb.tap(mx, my)
        time.sleep(0.6)
    after = save_state(adb, output_dir, "thinking-after")
    new_state = thinking_selected(after["nodes"])
    if not has_input(after["nodes"]):
        adb.keyevent(4)
        time.sleep(0.3)
        after_back = save_state(adb, output_dir, "thinking-after-back")
        back_state = thinking_selected(after_back["nodes"])
        return {
            "requested": enabled,
            "changed": True,
            "verified": new_state is True,
            "stateBefore": current,
            "stateAfter": new_state,
            "stateAfterBack": back_state,
            "tap": {"x": x, "y": y, "bounds": bounds},
            "menuTap": {"x": menu_target[0], "y": menu_target[1], "bounds": menu_target[2]} if menu_target else None,
            "back": {"performed": True},
            "reason": None if new_state is True else "state_not_exposed_in_xml_or_visual_only",
        }
    return {
        "requested": enabled,
        "changed": True,
        "verified": new_state is True,
        "stateBefore": current,
        "stateAfter": new_state,
        "tap": {"x": x, "y": y, "bounds": bounds},
        "menuTap": {"x": menu_target[0], "y": menu_target[1], "bounds": menu_target[2]} if menu_target else None,
        "reason": None if new_state is True else "state_not_exposed_in_xml_or_visual_only",
    }


def input_texts_from_xml(adb: AdbClient) -> list[str]:
    """Read the current visible input texts directly from XML."""
    nodes = find_nodes(__import__("mobile_auto_doubao.ui_xml", fromlist=["collect_nodes"]).collect_nodes(adb.dump_xml()), resource_id=INPUT_ID)
    return [node.get("text", "") for node in nodes]


def input_is_empty_text(text: str) -> bool:
    """Check whether an input string is empty or just placeholder text."""
    return not text or text in {"发消息...", "发消息或按住说话..."}


def input_contains_text(adb: AdbClient, expected: str) -> tuple[bool, list[str]]:
    """Check whether the current input field contains a given string."""
    texts = input_texts_from_xml(adb)
    return any(expected in text for text in texts), texts


def clear_focused_text(adb: AdbClient, verify: bool = False) -> dict:
    """Clear the focused text field and optionally verify the result."""
    result = {"method": "adb_clear_text", "verified": False, "fallback": False}
    try:
        previous_ime = adb.current_ime()
        if previous_ime != ADB_KEYBOARD_IME:
            adb.set_ime(ADB_KEYBOARD_IME)
        adb.broadcast_clear_text()
        time.sleep(0.25)
        if not verify:
            return result
        texts = input_texts_from_xml(adb)
        result["textsAfterClear"] = texts
        if texts and all(input_is_empty_text(text) for text in texts):
            result["verified"] = True
            return result
    except Exception as exc:
        result["error"] = str(exc)
    result["fallback"] = True
    adb.keyevent(123)
    for _ in range(320):
        adb.keyevent(67)
    time.sleep(0.2)
    if verify:
        texts = input_texts_from_xml(adb)
        result["textsAfterFallback"] = texts
        result["verified"] = bool(texts) and all(input_is_empty_text(text) for text in texts)
    return result


def clear_input_if_visible(adb: AdbClient, nodes: list[dict], verify: bool = False) -> dict:
    """Focus and clear the visible input field if one exists."""
    input_nodes = find_nodes(nodes, resource_id=INPUT_ID)
    if not input_nodes:
        return {"ok": False, "error": "input_not_found"}
    x, y = center(input_nodes[-1])
    adb.tap(x, y)
    time.sleep(0.2)
    clear = clear_focused_text(adb, verify=verify)
    return {"ok": True, "tap": {"x": x, "y": y, "bounds": input_nodes[-1].get("bounds", "")}, "clear": clear}


def focus_input(adb: AdbClient, nodes: list[dict]) -> bool:
    """Tap the visible input field to focus it."""
    input_nodes = find_nodes(nodes, resource_id=INPUT_ID)
    if not input_nodes:
        return False
    x, y = center(input_nodes[-1])
    adb.tap(x, y)
    time.sleep(0.3)
    return True


def type_question(adb: AdbClient, question: str) -> dict:
    """Input a question using the ADB keyboard broadcast path."""
    imes = adb.list_imes()
    if ADB_KEYBOARD_IME not in imes:
        return {"ok": False, "error": "adb_keyboard_not_installed", "availableImes": imes}
    previous_ime = adb.current_ime()
    switch_error = None
    if previous_ime != ADB_KEYBOARD_IME:
        try:
            adb.set_ime(ADB_KEYBOARD_IME)
            time.sleep(0.3)
        except Exception as exc:
            switch_error = str(exc)
    adb.broadcast_text(question)
    time.sleep(0.5)
    visible, texts = input_contains_text(adb, question)
    method = "adb_keyboard_text"
    if not visible:
        adb.broadcast_base64_text(question)
        time.sleep(0.5)
        visible, texts = input_contains_text(adb, question)
        method = "adb_keyboard_b64"
    if previous_ime and previous_ime != ADB_KEYBOARD_IME:
        try:
            adb.set_ime(previous_ime)
        except Exception:
            # Some ROMs block ime set. If the broadcast input already landed,
            # do not fail the question just because the restore step is denied.
            pass
    if not visible:
        error = "question_text_not_visible_in_input_after_adb_keyboard_input"
        if switch_error:
            error = f"{error}: {switch_error}"
        return {"ok": False, "error": error, "previousIme": previous_ime, "switchError": switch_error, "inputTexts": texts}
    result = {"ok": True, "method": method, "previousIme": previous_ime, "inputTexts": texts}
    if switch_error:
        result["switchError"] = switch_error
    return result


def send_question(adb: AdbClient, question: str, output_dir: str) -> tuple[bool, dict]:
    """Clear the input, type a question, and press send."""
    state = save_state(adb, output_dir, "before-send")
    blocked = detect_blocked(state["nodes"])
    if blocked:
        return False, {"error": blocked, "state": state}
    if not focus_input(adb, state["nodes"]):
        return False, {"error": "input_not_found", "state": state}
    clear_focused_text(adb)
    input_result = type_question(adb, question)
    if not input_result.get("ok"):
        return False, {"error": input_result.get("error"), "input": input_result, "state": state}
    time.sleep(0.3)
    after_type = save_state(adb, output_dir, "after-type")
    if tap_send_button(adb, after_type["nodes"]):
        time.sleep(0.6)
        return True, {"method": "action-send-button", "input": input_result, "state": after_type}
    return False, {"error": "send_button_not_found", "input": input_result, "state": after_type}
