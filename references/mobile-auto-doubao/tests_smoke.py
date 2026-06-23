import json
import subprocess
from pathlib import Path

from mobile_auto_doubao.adb_client import AdbClient
from mobile_auto_doubao.doubao_app import is_blank_new_chat_page, tap_sidebar_create_conversation, tap_top_left_sidebar
from mobile_auto_doubao.feishu_base import build_feishu_writeback_rows_for_result, planned_writeback
from mobile_auto_doubao.task_schema import load_task, normalize_task, summarize_task
from mobile_auto_doubao.ui_xml import extract_urls_from_text, parse_bounds
from runner import failed_writeback_result, parse_args


class FakeAdb(AdbClient):
    def __init__(self):
        self.taps = []

    def tap(self, x: int, y: int) -> None:
        self.taps.append((x, y))


class FakeMultiDeviceAdb(AdbClient):
    def __init__(self):
        self.serial = None

    def devices(self) -> list[str]:
        return ["emulator-5556", "100.76.50.7:6666"]


class FakeScrollAdb(AdbClient):
    def __init__(self):
        self.calls = []

    def command(self, args: list[str], check: bool = True, text: bool = True):
        self.calls.append(args)
        if args[:3] == ["shell", "input", "swipe"]:
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="java.lang.SecurityException: Injecting to another application requires INJECT_EVENTS permission")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")


def test_schema():
    task = load_task("tasks/example.json")
    summary = summarize_task(task)
    assert summary["totalSessions"] == 1
    assert summary["totalQuestions"] == 1
    assert summary["sessions"][0]["newChat"] is True
    assert summary["sessions"][0]["thinking"] is None
    assert summary["sessions"][0]["questions"][0]["newChat"] is True


def test_schema_flags():
    task = normalize_task({
        "thinking": True,
        "sessions": [
            {
                "sessionName": "reuse",
                "newChat": False,
                "thinking": False,
                "questions": [
                    "复用当前对话",
                    {"text": "新开并深思", "newChat": True, "thinking": True},
                ],
            }
        ],
    })
    session = task["sessions"][0]
    assert session["newChat"] is False
    assert session["thinking"] is False
    assert session["questions"][0] == {"text": "复用当前对话", "newChat": False, "thinking": False}
    assert session["questions"][1] == {"text": "新开并深思", "newChat": True, "thinking": True}
    summary = summarize_task(task)
    assert summary["sessions"][0]["newChat"] is False
    assert summary["sessions"][0]["questions"][1]["thinking"] is True


def test_schema_thinking_null():
    task = normalize_task({
        "thinking": None,
        "sessions": [{"sessionName": "q", "newChat": True, "thinking": None, "questions": ["不碰思考"]}],
    })
    question = task["sessions"][0]["questions"][0]
    assert task["thinking"] is None
    assert task["sessions"][0]["thinking"] is None
    assert question["thinking"] is None


def test_schema_source_limit_all():
    task = normalize_task({
        "sessions": [{"sessionName": "q", "newChat": True, "questions": ["抓全部来源"]}],
        "options": {"sourceLimit": "all"},
    })
    assert task["options"]["sourceLimit"] == "all"


def test_schema_preserves_explicit_device_serial():
    task = normalize_task({
        "device": {"serial": "emulator-5556"},
        "sessions": [{"sessionName": "q", "newChat": True, "questions": ["默认设备"]}],
    })
    assert task["device"]["serial"] == "emulator-5556"


def test_resolve_serial_requires_explicit_choice_for_multiple_devices():
    adb = FakeMultiDeviceAdb()
    try:
        adb.resolve_serial()
        raise AssertionError("Expected resolve_serial() to fail when multiple devices are online.")
    except Exception as exc:
        assert "Multiple adb devices are connected" in str(exc)


def test_runner_accepts_output_override(monkeypatch_args=None):
    import sys

    original_argv = sys.argv[:]
    try:
        sys.argv = [
            "runner.py",
            "--task",
            "tasks/example.json",
            "--serial",
            "emulator-5556",
            "--output",
            "results/example-emulator-5556.json",
            "--collect-account",
            "18870501682",
            "--dry-run",
        ]
        args = parse_args()
        assert args.serial == "emulator-5556"
        assert args.output == "results/example-emulator-5556.json"
        assert args.collect_account == "18870501682"
    finally:
        sys.argv = original_argv


def test_ui_helpers():
    assert parse_bounds("[1,2][5,8]") == {"left": 1, "top": 2, "right": 5, "bottom": 8, "centerX": 3, "centerY": 5}
    urls = extract_urls_from_text("复制 https://example.com/a?b=1 链接")
    assert urls == ["https://example.com/a?b=1"]


def test_new_chat_sidebar_targets():
    adb = FakeAdb()
    sidebar = tap_top_left_sidebar(adb, [
        {"text": "", "content_desc": "返回", "resource_id": "com.larus.nova:id/back_icon", "clickable": "true", "bounds": "[22,124][144,246]", "parsedBounds": parse_bounds("[22,124][144,246]")},
    ])
    assert sidebar == {"x": 83, "y": 185, "bounds": "[22,124][144,246]", "resourceId": "com.larus.nova:id/back_icon", "contentDesc": "返回"}
    create = tap_sidebar_create_conversation(adb, [
        {"text": "", "content_desc": "创建新对话", "resource_id": "com.larus.nova:id/side_bar_create_conversation", "clickable": "true", "bounds": "[804,151][870,217]", "parsedBounds": parse_bounds("[804,151][870,217]")},
    ])
    assert create == {"x": 837, "y": 184, "bounds": "[804,151][870,217]", "resourceId": "com.larus.nova:id/side_bar_create_conversation", "contentDesc": "创建新对话"}
    assert adb.taps == [(83, 185), (837, 184)]


def test_blank_new_chat_page_detection():
    nodes = [
        {"text": "嗨，我是豆包，可以帮你解答问题", "content_desc": "", "resource_id": "", "parsedBounds": parse_bounds("[33,238][1047,339]")},
        {"text": "发消息或按住说话...", "content_desc": "", "resource_id": "com.larus.nova:id/input_text", "parsedBounds": parse_bounds("[155,1708][925,1837]")},
    ]
    assert is_blank_new_chat_page(nodes) is True


def test_failed_writeback_result():
    exc = RuntimeError('{"ok": false, "error": {"code": 800004135, "message": "limited"}}')
    payload = failed_writeback_result(
        exc,
        {"meta": {"feishuRecordId": "rec123"}},
        {"question": "问题", "status": "partial"},
    )
    assert payload["status"] == "failed"
    assert payload["errorType"] == "RuntimeError"
    assert payload["sourceRecordId"] == "rec123"
    assert payload["question"] == "问题"
    assert payload["resultStatus"] == "partial"
    assert payload["answerCount"] == 0
    assert payload["sourceCount"] == 0
    assert payload["error"]["error"]["code"] == 800004135


def test_writeback_uses_custom_collect_account():
    rows = build_feishu_writeback_rows_for_result(
        {"meta": {"naturalQuestion": "问题", "thinking": True, "platform": "豆包"}},
        {"status": "success", "answer": "答案", "thinkingContent": "深度思考", "sources": [], "answerShareUrl": ""},
        "18870000000",
    )
    assert rows["answerRows"][0][0] == "18870000000"


def test_planned_writeback_includes_collect_account():
    task = normalize_task({
        "sessions": [{"sessionName": "q", "newChat": True, "questions": ["问题"], "meta": {"feishuRecordId": "rec1"}}],
        "options": {"collectAccount": "18870000000"},
    })
    payload = planned_writeback(task, True)
    assert payload["collectAccount"] == "18870000000"


def test_scroll_falls_back_to_keyevent():
    adb = FakeScrollAdb()
    result = adb.scroll_down()
    assert result["fallback"] is True
    assert any(call[:3] == ["shell", "input", "keyevent"] and call[3] == "93" for call in adb.calls)


# —— DeepSeek runner smoke tests (ADR-001) ——

def test_deepseek_constants_backfilled():
    """ADR-001 第 5 节回填检查：10 个字段均已填值，无占位符。"""
    from mobile_auto_deepseek import constants as ds

    assert ds.DEEPSEEK_PACKAGE == "com.deepseek.chat"
    assert ds.thinking_supported is True and ds.THINK_BUTTON_TEXT
    assert ds.share_link_supported is True and ds.DEEPSEEK_SHARE_URL_RE_PATTERN
    assert ds.source_extraction_route == "cdp_bridge"
    assert ds.thinking_capture_method in {"ocr", "ui"}
    assert ds.share_page_requires_auth is False
    # cdp_bridge 依赖分享链
    assert ds.share_link_supported is True
    # 无占位符残留
    for value in vars(ds).values():
        if isinstance(value, str):
            assert "<填" not in value


def test_deepseek_share_url_regex():
    from mobile_auto_deepseek.source_extractor_bridge import DEEPSEEK_SHARE_URL_RE

    assert DEEPSEEK_SHARE_URL_RE.match("https://chat.deepseek.com/share/o7a2kswga666sdv2di")
    assert DEEPSEEK_SHARE_URL_RE.match("https://deepseek.com/share/abc123")
    assert not DEEPSEEK_SHARE_URL_RE.match("https://www.qianwen.com/share/chat/xxx")


def test_deepseek_bridge_validate_params():
    from mobile_auto_deepseek.source_extractor_bridge import SourceExtractorError, validate_params

    # 合法
    validate_params("https://chat.deepseek.com/share/abc123", "NQ-001")
    # 非 DeepSeek 链接应报错
    for bad in ("", "not-a-url", "https://qianwen.com/share/chat/x"):
        try:
            validate_params(bad, "NQ-001")
            raise AssertionError(f"expected failure for {bad!r}")
        except SourceExtractorError:
            pass
    # 缺自然问句
    try:
        validate_params("https://chat.deepseek.com/share/abc123", "")
        raise AssertionError("expected failure for empty natural_question")
    except SourceExtractorError:
        pass


def test_deepseek_task_schema_dry_run():
    from mobile_auto_deepseek.task_schema import load_task, summarize_task

    task = load_task("tasks/deepseek_sample.json")
    summary = summarize_task(task)
    assert summary["taskName"] == "deepseek-sample"
    assert summary["totalSessions"] == 1
    assert summary["totalQuestions"] == 1
    assert summary["sessions"][0]["thinking"] is True


def test_deepseek_default_platform():
    from mobile_auto_deepseek.feishu_base import DEFAULT_PLATFORM, FEISHU_ANSWER_TABLE_ID, FEISHU_SOURCE_TABLE_ID

    assert DEFAULT_PLATFORM == "DeepSeek"
    assert FEISHU_ANSWER_TABLE_ID == "tblz1qWt0EmduKwD"
    assert FEISHU_SOURCE_TABLE_ID == "tbltRcXROHY3NUdE"


def test_deepseek_thinking_uses_compose_structure():
    from mobile_auto_deepseek.app import extract_thinking_texts_from_nodes

    question = "请解释复利。"
    nodes = [
        {"text": "已思考（用时 3 秒）", "content_desc": "", "parsedBounds": {"left": 66, "right": 510, "top": 200, "bottom": 270}},
        {"text": question + question, "content_desc": "", "parsedBounds": {"left": 243, "right": 992, "top": 300, "bottom": 420}},
        {"text": "先确认用户需要定义、机制和例子。", "content_desc": "", "parsedBounds": {"left": 205, "right": 1019, "top": 500, "bottom": 650}},
        {"text": "复利是利息继续产生利息。", "content_desc": "", "parsedBounds": {"left": 121, "right": 1019, "top": 700, "bottom": 850}},
    ]
    assert extract_thinking_texts_from_nodes(nodes, question) == ["先确认用户需要定义、机制和例子。"]


if __name__ == "__main__":
    test_schema()
    test_schema_flags()
    test_schema_thinking_null()
    test_schema_source_limit_all()
    test_schema_preserves_explicit_device_serial()
    test_resolve_serial_requires_explicit_choice_for_multiple_devices()
    test_runner_accepts_output_override()
    test_ui_helpers()
    test_new_chat_sidebar_targets()
    test_blank_new_chat_page_detection()
    test_failed_writeback_result()
    test_writeback_uses_custom_collect_account()
    test_planned_writeback_includes_collect_account()
    test_scroll_falls_back_to_keyevent()
    test_deepseek_constants_backfilled()
    test_deepseek_share_url_regex()
    test_deepseek_bridge_validate_params()
    test_deepseek_task_schema_dry_run()
    test_deepseek_default_platform()
    test_deepseek_thinking_uses_compose_structure()
    print(json.dumps({"status": "ok", "tests": 20, "cwd": str(Path.cwd())}, ensure_ascii=False))
