import argparse
import json
import traceback
import time
from pathlib import Path

from mobile_auto_doubao.adb_client import AdbClient
from mobile_auto_doubao.answer_share import extract_answer_share_link
from mobile_auto_doubao.answer_capture import wait_for_answer
from mobile_auto_doubao.artifacts import save_state, set_capture_options
from mobile_auto_doubao.constants import DOUBAO_PACKAGE
from mobile_auto_doubao.doubao_app import create_new_chat, detect_blocked, ensure_app, send_question, set_thinking_mode
from mobile_auto_doubao.expert_answer import collect_expert_answer
from mobile_auto_doubao.feishu_base import build_task_from_feishu, clean_answer_for_writeback, planned_writeback, write_feishu_result
from mobile_auto_doubao.result_writer import create_aggregate, write_result
from mobile_auto_doubao.source_extractor_bridge import ExtractorOptions, run_source_extractor
from mobile_auto_doubao.source_links import extract_sources
from mobile_auto_doubao.task_schema import load_task, normalize_task, summarize_task
from mobile_auto_doubao.time_utils import now_iso, stamp


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for the root Doubao runner."""
    parser = argparse.ArgumentParser(description="Run Doubao mobile automation through Python + adb.exe.")
    parser.add_argument("--task")
    parser.add_argument("--adb")
    parser.add_argument("--serial", "--device", dest="serial", help="Android adb serial / device id. Use this when multiple devices are connected.")
    parser.add_argument("--output", help="Override the result JSON path. Use a different output path for each parallel device run.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--writeback", action="store_true", help="Create Feishu answer/source records after each successful or partial result.")
    parser.add_argument("--mark-collected", action="store_true", help="With --writeback, set source Feishu rows 是否本次采集 to 否 after successful answer writeback.")
    parser.add_argument("--collect-account", help="Override the 采集账号 field written to Feishu answer rows.")
    parser.add_argument("--feishu-config", "--writeback-config", dest="feishu_config", help="JSON file describing Feishu input base and answer/source writeback table IDs.")
    parser.add_argument("--base-url", help="Feishu Base URL containing /base/{baseToken}?table=...&view=...")
    parser.add_argument("--base-token")
    parser.add_argument("--table-id")
    parser.add_argument("--view-id")
    parser.add_argument("--base-start", type=int, help="1-based start row in Feishu Base, inclusive.")
    parser.add_argument("--base-end", type=int, help="1-based end row in Feishu Base, inclusive.")
    parser.add_argument("--base-limit", type=int, default=50, help="Deprecated fallback for the old top-N mode when --base-start/--base-end are omitted.")
    parser.add_argument("--source-limit", type=int, default=2, help="How many sources to collect per question.")
    parser.add_argument("--platform", default="豆包")
    parser.add_argument("--lark-cli", default="lark-cli", help="lark-cli executable path.")
    parser.add_argument("--force-quick", action="store_true", help="Force Feishu Base sessions to use quick mode.")
    parser.add_argument("--link-only", action="store_true", help="Skip mobile-side expert/source capture and rely on --extract-sources to fetch answer/thinking/sources from the share page.")
    parser.add_argument("--debug", action="store_true", help="启用调试模式：保留截图/current_focus/写XML等耗时采集产物（默认关闭以加速运行）。")
    parser.add_argument("--extract-sources", action="store_true", help="After capturing the answer share link, invoke doubao-source-extractor to extract answer/thinking/sources from the share page.")
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9222", help="Chrome DevTools Protocol endpoint for the JS extractor.")
    parser.add_argument("--extractor-script", default="", help="Path to doubao-source-extractor/run.js. Auto-located if omitted.")
    parser.add_argument("--extractor-timeout", type=int, default=120, help="Per-attempt timeout (seconds) for the JS extractor.")
    parser.add_argument("--extractor-retries", type=int, default=2, help="Max retries for the JS extractor on failure.")
    parser.add_argument("--source-base-token", default="", help="Feishu base_token for the source table. Defaults to the input base_token.")
    parser.add_argument("--source-table-id", default="", help="Feishu table_id for the source table. Defaults to the built-in Doubao source table.")
    parser.add_argument("--answer-table-id", default="", help="Feishu table_id for the answer writeback table. Defaults to the built-in Doubao answer table.")
    return parser.parse_args()


def question_artifact_dir(output: str, session_name: str, index: int) -> str:
    """Create the per-question artifact directory."""
    base = Path(output).parent / "snapshots" / f"{session_name}-{index}-{stamp()}"
    base.mkdir(parents=True, exist_ok=True)
    return str(base)


def failed_writeback_result(exc: Exception, session: dict, result: dict) -> dict:
    """Format a Feishu writeback failure for result storage."""
    error_text = str(exc)
    parsed_error = None
    try:
        parsed_error = json.loads(error_text)
    except json.JSONDecodeError:
        parsed_error = None
    return {
        "finishedAt": now_iso(),
        "status": "failed",
        "errorType": exc.__class__.__name__,
        "error": parsed_error or error_text,
        "traceback": traceback.format_exc(),
        "sourceRecordId": (session.get("meta") or {}).get("feishuRecordId"),
        "question": result.get("question", ""),
        "resultStatus": result.get("status", ""),
        "answerCount": 0,
        "sourceCount": 0,
    }


def load_feishu_config(path: str | None) -> dict:
    """Load optional Feishu input/writeback table configuration from JSON."""
    if not path:
        return {}
    config_path = Path(path)
    if not config_path.is_file():
        raise ValueError(f"Feishu config file not found: {path}")
    with config_path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    if not isinstance(config, dict):
        raise ValueError("Feishu config must be a JSON object.")
    return config


def _pick(config: dict, *keys: str) -> str:
    for key in keys:
        value = config.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def apply_feishu_config(args: argparse.Namespace, config: dict) -> None:
    """Apply JSON Feishu config values as CLI defaults."""
    if not config:
        return
    input_cfg = config.get("input") or config.get("read") or config.get("base") or {}
    writeback_cfg = config.get("writeback") or config.get("output") or {}
    source_cfg = config.get("sourceExtractor") or {}
    if not isinstance(input_cfg, dict) or not isinstance(writeback_cfg, dict) or not isinstance(source_cfg, dict):
        raise ValueError("Feishu config input/writeback/sourceExtractor sections must be JSON objects.")

    args.base_url = args.base_url or _pick(input_cfg, "baseUrl", "url")
    args.base_token = args.base_token or _pick(input_cfg, "baseToken", "base_token")
    args.table_id = args.table_id or _pick(input_cfg, "tableId", "table_id")
    args.view_id = args.view_id or _pick(input_cfg, "viewId", "view_id")
    args.answer_table_id = args.answer_table_id or _pick(writeback_cfg, "answerTableId", "answer_table_id")
    args.source_table_id = args.source_table_id or _pick(writeback_cfg, "sourceTableId", "source_table_id", "aiSourceTableId", "ai_source_table_id")
    args.source_base_token = args.source_base_token or _pick(writeback_cfg, "baseToken", "base_token") or _pick(source_cfg, "baseToken", "base_token")
    args.collect_account = args.collect_account or _pick(config, "collectAccount", "collect_account")


def evaluate_writeback_guard(
    *,
    extractor_enabled: bool,
    link_only: bool,
    answer: str,
    share_ok: bool,
    source_extraction: dict,
    debug: dict,
) -> dict:
    """Decide whether a captured result is trustworthy enough to write back."""
    if not answer:
        return {"allowed": False, "reason": "answer_not_found"}
    if not extractor_enabled:
        return {"allowed": True, "reason": "mobile_capture"}
    if not share_ok:
        return {"allowed": False, "reason": "share_url_not_found"}
    if source_extraction.get("status") != "success":
        return {"allowed": False, "reason": "share_page_extraction_failed"}
    if link_only:
        api_capture = debug.get("shareApiCapture") or {}
        if int(api_capture.get("answerLength") or 0) <= 0:
            return {"allowed": False, "reason": "share_page_answer_not_found"}
    return {"allowed": True, "reason": "share_page_verified"}


def run_question(
    adb: AdbClient,
    task: dict,
    session_name: str,
    question_item: dict,
    index: int,
    source_extractor_context: dict | None = None,
    session_meta: dict | None = None,
) -> dict:
    """Run one question through the full capture and writeback flow."""
    question = question_item["text"]
    output_dir = question_artifact_dir(task["output"], session_name, index)
    asked_at = now_iso()
    debug = {"notes": [], "artifactsDir": output_dir}
    try:
        ensure_app(adb, DOUBAO_PACKAGE)
        initial = save_state(adb, output_dir, "question-initial")
        blocked = detect_blocked(initial["nodes"])
        if blocked:
            return {
                "index": index,
                "question": question,
                "askedAt": asked_at,
                "finishedAt": now_iso(),
                "answer": "",
                "sources": [],
                "status": "blocked",
                "error": blocked,
                "debug": {**debug, "initialXml": initial["xml"], "screenshot": initial["screenshot"]},
            }
        if question_item["newChat"]:
            new_chat = create_new_chat(adb, output_dir)
        else:
            new_chat = {"created": False, "method": "skipped", "requested": False}
        debug["newChat"] = {"requested": question_item["newChat"], "result": new_chat}
        if question_item["newChat"] and not new_chat.get("created"):
            return {
                "index": index,
                "question": question,
                "askedAt": asked_at,
                "finishedAt": now_iso(),
                "answer": "",
                "sources": [],
                "status": "failed",
                "error": new_chat.get("error") or "new_chat_failed",
                "debug": debug,
            }
        requested_thinking = True if question_item["thinking"] is None else question_item["thinking"]
        thinking = set_thinking_mode(adb, output_dir, requested_thinking)
        if requested_thinking is True and not thinking.get("verified"):
            time.sleep(0.5)
            thinking_retry = set_thinking_mode(adb, output_dir, requested_thinking)
            thinking = {**thinking, "retry": thinking_retry, "verified": thinking_retry.get("verified", False)}
        debug["thinking"] = thinking
        sent, send_debug = send_question(adb, question, output_dir)
        debug["send"] = send_debug
        if not sent:
            return {
                "index": index,
                "question": question,
                "askedAt": asked_at,
                "finishedAt": now_iso(),
                "answer": "",
                "sources": [],
                "status": "failed",
                "error": send_debug.get("error") or "send_failed",
                "debug": debug,
            }
        answer_result = wait_for_answer(adb, question, task["options"], output_dir)
        answer = clean_answer_for_writeback(answer_result["answer"], question)
        debug["answerSamples"] = answer_result["samples"]
        if answer_result.get("state"):
            debug["answerXml"] = answer_result["state"]["xml"]
            debug["screenshot"] = answer_result["state"].get("screenshot")
        link_only = bool(task.get("options", {}).get("linkOnly", False))
        extractor_enabled = bool(source_extractor_context and source_extractor_context.get("enabled"))
        expert_answer = {"status": "skipped", "thinking": "", "answer": "", "expand": {"ok": False, "error": "skipped"}, "snapshots": []}
        if link_only:
            expert_answer["reason"] = "link_only_mode"
        else:
            try:
                expert_answer = collect_expert_answer(adb, output_dir, task["options"], question)
            except Exception as exc:
                debug["notes"].append(f"expert_answer_failed:{exc}")
                debug["expertAnswerError"] = str(exc)
        debug["expertAnswer"] = {
            "status": expert_answer["status"],
            "reason": expert_answer.get("reason"),
            "thinkingLength": len(expert_answer.get("thinking", "")),
            "answerLength": len(expert_answer.get("answer", "")),
            "expand": expert_answer.get("expand"),
            "snapshots": expert_answer.get("snapshots", []),
        }
        if expert_answer.get("answer"):
            answer = clean_answer_for_writeback(expert_answer["answer"], question)
        source_result = {"sources": [], "visibleSourceCount": 0, "attemptedCount": 0}
        sources = []
        success_sources = []
        source_extraction = {"status": "skipped", "reason": "answer_not_found"}
        if answer and not extractor_enabled:
            try:
                source_result = extract_sources(adb, task["options"], output_dir)
            except Exception as exc:
                debug["notes"].append(f"source_extraction_failed:{exc}")
                debug["sourceExtractionError"] = str(exc)
            sources = source_result["sources"]
            success_sources = [source for source in sources if source.get("status") == "success" and source.get("url")]
            source_extraction = {
                "status": "mobile_share_copy",
                "visibleSourceCount": source_result.get("visibleSourceCount", 0),
                "attemptedCount": source_result.get("attemptedCount", 0),
            }
        elif answer and extractor_enabled:
            source_extraction = {"status": "pending", "note": "handled_by_js_extractor"}
        debug["sourceExtraction"] = source_extraction
        share_result = {"status": "skipped", "url": "", "error": "answer_not_found"}
        if answer:
            try:
                share_result = extract_answer_share_link(adb, task["options"], output_dir)
            except Exception as exc:
                debug["notes"].append(f"answer_share_failed:{exc}")
                debug["answerShareError"] = str(exc)
        debug["answerShare"] = {key: value for key, value in share_result.items() if key not in {"clipboardText"}}
        share_url = share_result.get("url", "")
        if extractor_enabled and share_url:
            meta = (session_meta or {}) if session_meta else {}
            natural_question = meta.get("linkedNaturalQuestion") or meta.get("naturalQuestion") or question
            base_token = source_extractor_context.get("baseToken", "")
            table_id = source_extractor_context.get("tableId", "")
            extractor_options = source_extractor_context.get("options") or ExtractorOptions()
            try:
                extraction_result = run_source_extractor(
                    share_url=share_url,
                    natural_question=natural_question,
                    base_token=base_token,
                    table_id=table_id,
                    output_dir=output_dir,
                    options=extractor_options,
                )
                source_extraction = {
                    "status": extraction_result.get("status", "failed"),
                    "ok": bool(extraction_result.get("ok")),
                    "sourceCount": int(extraction_result.get("sourceCount", 0)),
                    "extractOk": extraction_result.get("extractOk"),
                    "feishuWriteOk": extraction_result.get("feishuWriteOk"),
                    "attempts": extraction_result.get("attempts", []),
                    "outputFile": extraction_result.get("outputFile", ""),
                    "error": extraction_result.get("error"),
                }
                extracted = extraction_result.get("extractedSources") or {}
                api_answer = str(extracted.get("answer") or "").strip()
                api_thinking = str(extracted.get("thinkingContent") or "").strip()
                if api_answer:
                    answer = clean_answer_for_writeback(api_answer, question)
                    expert_answer["answer"] = answer
                    expert_answer["status"] = "success"
                if api_thinking:
                    expert_answer["thinking"] = api_thinking
                    expert_answer["status"] = "success" if answer else expert_answer.get("status", "success")
                sources = []
                for src in extracted.get("sources", []):
                    sources.append({
                        "index": src.get("index"),
                        "title": src.get("title", ""),
                        "url": src.get("url", ""),
                        "platform": src.get("platform", ""),
                        "method": "js_extractor",
                        "status": "success" if src.get("url") else "failed",
                    })
                success_sources = [source for source in sources if source.get("status") == "success" and source.get("url")]
                debug["shareApiCapture"] = {
                    "answerLength": len(api_answer),
                    "thinkingLength": len(api_thinking),
                    "searchEnabled": bool(extracted.get("searchEnabled")),
                    "sourceFormat": extracted.get("sourceFormat"),
                    "searchSummaries": extracted.get("searchSummaries", []),
                }
            except Exception as exc:
                source_extraction = {"status": "failed", "ok": False, "error": str(exc), "sourceCount": 0}
                debug["notes"].append(f"js_source_extractor_failed:{exc}")
        elif extractor_enabled and not share_url:
            source_extraction = {"status": "skipped", "reason": "no_share_url"}
        debug["sourceExtraction"] = source_extraction
        all_sources_ok = (source_extraction.get("status") == "success") if extractor_enabled else (bool(sources) and len(success_sources) == len(sources))
        share_ok = share_result.get("status") == "success" and bool(share_result.get("url"))
        expert_ok = expert_answer.get("status") == "success" or link_only or not requested_thinking
        if answer and expert_ok and all_sources_ok and share_ok:
            status = "success"
        elif answer:
            status = "partial"
        else:
            status = "failed"
        writeback_guard = evaluate_writeback_guard(
            extractor_enabled=extractor_enabled,
            link_only=link_only,
            answer=answer,
            share_ok=share_ok,
            source_extraction=source_extraction,
            debug=debug,
        )
        debug["writebackGuard"] = writeback_guard
        return {
            "index": index,
            "question": question,
            "askedAt": asked_at,
            "finishedAt": now_iso(),
            "answer": answer,
            "thinkingContent": expert_answer["thinking"],
            "sources": sources,
            "answerShareUrl": share_result.get("url", ""),
            "sourceExtraction": source_extraction,
            "status": status,
            "error": None if answer else "answer_not_found",
            "writebackAllowed": bool(writeback_guard.get("allowed")),
            "writebackGuard": writeback_guard,
            "debug": debug,
        }
    except Exception as exc:
        return {
            "index": index,
            "question": question,
            "askedAt": asked_at,
            "finishedAt": now_iso(),
            "answer": "",
            "sources": [],
            "status": "failed",
            "error": str(exc),
            "debug": debug,
        }


def run_task(task: dict, writeback_context: dict | None = None, source_extractor_context: dict | None = None) -> str:
    """Run all sessions in a task and write the aggregate result."""
    debug_options = task.get("options", {}).get("debug", {})
    set_capture_options(
        screenshots=bool(debug_options.get("screenshots", True)),
        current_focus=bool(debug_options.get("currentFocus", True)),
        debug=bool(debug_options.get("enabled", False)),
    )
    device = task["device"]
    adb = AdbClient(device.get("adb"), device.get("serial"))
    adb.resolve_serial()
    aggregate = create_aggregate(task)
    output = task["output"]
    writebacks = []
    for session_index, session in enumerate(task["sessions"], start=1):
        session_out = {"sessionName": session["sessionName"], "newChat": session["newChat"], "thinking": session["thinking"], "results": []}
        for question_index, question in enumerate(session["questions"], start=1):
            result = run_question(
                adb,
                task,
                session["sessionName"],
                question,
                question_index,
                source_extractor_context=source_extractor_context,
                session_meta=session.get("meta", {}),
            )
            result["debug"]["sessionIndex"] = session_index
            if writeback_context and writeback_context.get("enabled"):
                try:
                    result["writeback"] = write_feishu_result(writeback_context, session, result)
                except Exception as exc:
                    result["writeback"] = failed_writeback_result(exc, session, result)
                    result["debug"].setdefault("notes", []).append("feishu_writeback_failed")
                writebacks.append(result["writeback"])
            session_out["results"].append(result)
            partial = {**aggregate, "sessions": [*aggregate["sessions"], session_out], "finishedAt": now_iso()}
            write_result(output, partial)
            interval_ms = int(task["options"].get("intervalMs", 0))
            if interval_ms:
                time.sleep(interval_ms / 1000)
        aggregate["sessions"].append(session_out)
    if writeback_context and writeback_context.get("enabled"):
        aggregate["writeback"] = {
            "finishedAt": now_iso(),
            "mode": "per-result",
            "answerTableId": writeback_context.get("answerTableId"),
            "sourceTableId": writeback_context.get("sourceTableId"),
            "inputTableId": writeback_context.get("base", {}).get("tableId"),
            "markCollected": bool(writeback_context.get("markCollected")),
            "answerCount": sum(item.get("answerCount", 0) for item in writebacks),
            "sourceCount": sum(item.get("sourceCount", 0) for item in writebacks),
            "results": writebacks,
        }
    write_result(output, aggregate, finished=True)
    return output


def validate_args(args: argparse.Namespace) -> None:
    """Validate that the CLI received a coherent task source."""
    has_base = bool(args.base_url or args.base_token or args.table_id)
    if args.task and (args.base_url or args.table_id):
        raise ValueError("With --task, use --base-token only for output writeback/source extraction; input table flags are not allowed.")
    if args.task and args.base_token and not (args.writeback or args.extract_sources):
        raise ValueError("With --task, --base-token requires --writeback or --extract-sources.")
    if not args.task and not has_base:
        raise ValueError("Provide --task or Feishu Base flags.")
    if args.base_start is not None and args.base_start < 1:
        raise ValueError("--base-start must be an integer >= 1.")
    if args.base_end is not None and args.base_end < 1:
        raise ValueError("--base-end must be an integer >= 1.")
    if args.base_start is not None and args.base_end is not None and args.base_end < args.base_start:
        raise ValueError("--base-end must be greater than or equal to --base-start.")
    if not isinstance(args.base_limit, int) or args.base_limit < 1 or args.base_limit > 350:
        raise ValueError("--base-limit must be an integer from 1 to 350.")
    if args.link_only and not args.extract_sources:
        raise ValueError("--link-only requires --extract-sources because mobile expert/source capture is skipped.")


def main() -> None:
    """CLI entry point for the Doubao runner."""
    args = parse_args()
    feishu_config = load_feishu_config(args.feishu_config)
    apply_feishu_config(args, feishu_config)
    validate_args(args)
    loaded = {"task": load_task(args.task), "taskPath": str(Path(args.task).resolve()), "source": "task-json"} if args.task else build_task_from_feishu(args)
    task = normalize_task(loaded["task"]) if not args.task else loaded["task"]
    if args.force_quick:
        task["thinking"] = False
        for session in task.get("sessions", []):
            session["thinking"] = False
            if isinstance(session.get("meta"), dict):
                session["meta"]["thinking"] = False
            for question in session.get("questions", []):
                if isinstance(question, dict):
                    question["thinking"] = False
    if args.adb:
        task["device"]["adb"] = args.adb
    if args.serial:
        task["device"]["serial"] = args.serial
    if args.output:
        task["output"] = args.output
    if args.collect_account:
        task.setdefault("options", {})["collectAccount"] = args.collect_account
    if args.link_only:
        task.setdefault("options", {})["linkOnly"] = True
    if args.debug:
        task.setdefault("options", {}).setdefault("debug", {})["enabled"] = True
    if args.extract_sources:
        task.setdefault("options", {}).setdefault("sourceExtractor", {})["enabled"] = True
        if args.cdp_url:
            task["options"]["sourceExtractor"]["cdpUrl"] = args.cdp_url
        if args.extractor_script:
            task["options"]["sourceExtractor"]["scriptPath"] = args.extractor_script
        task["options"]["sourceExtractor"]["timeoutSeconds"] = args.extractor_timeout
        task["options"]["sourceExtractor"]["maxRetries"] = args.extractor_retries
    if args.dry_run:
        print(json.dumps({
            "dryRun": True,
            "taskPath": loaded.get("taskPath"),
            "source": loaded.get("source"),
            "base": loaded.get("base"),
            "summary": summarize_task(task),
            "generatedTask": loaded.get("task") if loaded.get("source") == "feishu-base" else None,
            "skipped": loaded.get("skipped"),
            "plannedWriteback": planned_writeback(
                task,
                args.writeback,
                args.mark_collected,
                answer_table_id=args.answer_table_id,
                source_table_id=args.source_table_id,
            ) if loaded.get("source") == "feishu-base" else None,
            "sourceExtractor": task.get("options", {}).get("sourceExtractor"),
        }, ensure_ascii=False, indent=2))
        return
    if not task["sessions"]:
        raise ValueError("No Feishu rows selected. Check 是否本次采集.")
    writeback_context = None
    if loaded.get("source") == "feishu-base":
        writeback_base = dict(loaded["base"])
        if args.source_base_token:
            writeback_base["baseToken"] = args.source_base_token
        planned = planned_writeback(
            task,
            True,
            answer_table_id=args.answer_table_id,
            source_table_id=args.source_table_id,
        )
        writeback_context = {
            "enabled": args.writeback,
            "base": writeback_base,
            "inputBase": loaded["base"],
            "markCollected": args.mark_collected,
            "collectAccount": args.collect_account or task.get("options", {}).get("collectAccount"),
            "larkCli": args.lark_cli,
            "answerTableId": planned["answerTableId"],
            "sourceTableId": planned["sourceTableId"],
        }
    elif args.writeback:
        from mobile_auto_doubao.feishu_base import FEISHU_ANSWER_TABLE_ID, FEISHU_SOURCE_TABLE_ID

        if not args.base_token:
            raise ValueError("--base-token is required for task JSON writeback.")
        answer_table_id = args.answer_table_id or FEISHU_ANSWER_TABLE_ID
        source_table_id = args.source_table_id or FEISHU_SOURCE_TABLE_ID
        writeback_context = {
            "enabled": True,
            "base": {"baseToken": args.base_token, "tableId": answer_table_id},
            "markCollected": False,
            "collectAccount": args.collect_account or task.get("options", {}).get("collectAccount"),
            "larkCli": args.lark_cli,
            "answerTableId": answer_table_id,
            "sourceTableId": source_table_id,
        }

    task_extractor_cfg = task.get("options", {}).get("sourceExtractor") or {}
    extractor_enabled = args.extract_sources or bool(task_extractor_cfg.get("enabled"))
    source_extractor_context = None
    if extractor_enabled:
        from mobile_auto_doubao.feishu_base import FEISHU_SOURCE_TABLE_ID

        base_token = args.source_base_token or args.base_token or (loaded.get("base") or {}).get("baseToken", "")
        table_id = args.source_table_id or task_extractor_cfg.get("tableId", "") or FEISHU_SOURCE_TABLE_ID
        extractor_options = ExtractorOptions(
            script_path=args.extractor_script or task_extractor_cfg.get("scriptPath", ""),
            cdp_url=args.cdp_url or task_extractor_cfg.get("cdpUrl", "http://127.0.0.1:9222"),
            timeout_seconds=args.extractor_timeout or int(task_extractor_cfg.get("timeoutSeconds", 120)),
            max_retries=args.extractor_retries or int(task_extractor_cfg.get("maxRetries", 2)),
            retry_backoff_base=float(task_extractor_cfg.get("retryBackoffBase", 2.0)),
        )
        source_extractor_context = {
            "enabled": True,
            "baseToken": base_token,
            "tableId": table_id,
            "options": extractor_options,
        }
    if writeback_context and writeback_context.get("enabled") and source_extractor_context and source_extractor_context.get("baseToken"):
        writeback_context["skipSourceWrite"] = True
        writeback_context["sourceWriteMode"] = "js_extractor"
    output = run_task(task, writeback_context, source_extractor_context)
    print(json.dumps({"status": "finished", "output": output}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
