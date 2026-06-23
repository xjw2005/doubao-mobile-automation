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
    parser.add_argument("--debug", action="store_true", help="启用调试模式：保留截图/current_focus/写XML等耗时采集产物（默认关闭以加速运行）。")
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


def run_question(adb: AdbClient, task: dict, session_name: str, question_item: dict, index: int) -> dict:
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
        expert_answer = {"status": "skipped", "thinking": "", "answer": "", "expand": {"ok": False, "error": "skipped"}, "snapshots": []}
        try:
            expert_answer = collect_expert_answer(adb, output_dir, task["options"], question)
        except Exception as exc:
            debug["notes"].append(f"expert_answer_failed:{exc}")
            debug["expertAnswerError"] = str(exc)
        debug["expertAnswer"] = {
            "status": expert_answer["status"],
            "thinkingLength": len(expert_answer.get("thinking", "")),
            "answerLength": len(expert_answer.get("answer", "")),
            "expand": expert_answer.get("expand"),
            "snapshots": expert_answer.get("snapshots", []),
        }
        if expert_answer.get("answer"):
            answer = clean_answer_for_writeback(expert_answer["answer"], question)
        source_result = {"sources": [], "visibleSourceCount": 0, "attemptedCount": 0}
        if answer:
            try:
                source_result = extract_sources(adb, task["options"], output_dir)
            except Exception as exc:
                debug["notes"].append(f"source_extraction_failed:{exc}")
                debug["sourceExtractionError"] = str(exc)
        sources = source_result["sources"]
        success_sources = [source for source in sources if source.get("status") == "success" and source.get("url")]
        debug["sourceExtraction"] = {"visibleSourceCount": source_result.get("visibleSourceCount", 0), "attemptedCount": source_result.get("attemptedCount", 0)}
        share_result = {"status": "skipped", "url": "", "error": "answer_not_found"}
        if answer:
            try:
                share_result = extract_answer_share_link(adb, task["options"], output_dir)
            except Exception as exc:
                debug["notes"].append(f"answer_share_failed:{exc}")
                debug["answerShareError"] = str(exc)
        debug["answerShare"] = {key: value for key, value in share_result.items() if key not in {"clipboardText"}}
        all_sources_ok = bool(sources) and len(success_sources) == len(sources)
        share_ok = share_result.get("status") == "success" and bool(share_result.get("url"))
        expert_ok = expert_answer.get("status") == "success"
        if answer and expert_ok and all_sources_ok and share_ok:
            status = "success"
        elif answer:
            status = "partial"
        else:
            status = "failed"
        return {
            "index": index,
            "question": question,
            "askedAt": asked_at,
            "finishedAt": now_iso(),
            "answer": answer,
            "thinkingContent": expert_answer["thinking"],
            "sources": sources,
            "answerShareUrl": share_result.get("url", ""),
            "status": status,
            "error": None if answer else "answer_not_found",
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


def run_task(task: dict, writeback_context: dict | None = None) -> str:
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
            result = run_question(adb, task, session["sessionName"], question, question_index)
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
    if args.task and has_base:
        raise ValueError("Use either --task or Feishu Base flags, not both.")
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


def main() -> None:
    """CLI entry point for the Doubao runner."""
    args = parse_args()
    validate_args(args)
    loaded = {"task": load_task(args.task), "taskPath": str(Path(args.task).resolve()), "source": "task-json"} if args.task else build_task_from_feishu(args)
    task = normalize_task(loaded["task"]) if not args.task else loaded["task"]
    if args.adb:
        task["device"]["adb"] = args.adb
    if args.serial:
        task["device"]["serial"] = args.serial
    if args.output:
        task["output"] = args.output
    if args.collect_account:
        task.setdefault("options", {})["collectAccount"] = args.collect_account
    if args.debug:
        task.setdefault("options", {}).setdefault("debug", {})["enabled"] = True
    if args.dry_run:
        print(json.dumps({
            "dryRun": True,
            "taskPath": loaded.get("taskPath"),
            "source": loaded.get("source"),
            "base": loaded.get("base"),
            "summary": summarize_task(task),
            "generatedTask": loaded.get("task") if loaded.get("source") == "feishu-base" else None,
            "skipped": loaded.get("skipped"),
            "plannedWriteback": planned_writeback(task, args.writeback, args.mark_collected) if loaded.get("source") == "feishu-base" else None,
        }, ensure_ascii=False, indent=2))
        return
    if not task["sessions"]:
        raise ValueError("No Feishu rows selected. Check 是否本次采集.")
    writeback_context = None
    if loaded.get("source") == "feishu-base":
        writeback_context = {
            "enabled": args.writeback,
            "base": loaded["base"],
            "markCollected": args.mark_collected,
            "collectAccount": args.collect_account or task.get("options", {}).get("collectAccount"),
            "larkCli": args.lark_cli,
            "answerTableId": planned_writeback(task, True)["answerTableId"],
            "sourceTableId": planned_writeback(task, True)["sourceTableId"],
        }
    output = run_task(task, writeback_context)
    print(json.dumps({"status": "finished", "output": output}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
