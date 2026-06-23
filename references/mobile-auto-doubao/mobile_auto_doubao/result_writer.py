import json
from pathlib import Path

from .time_utils import now_iso


def create_aggregate(task: dict) -> dict:
    """Build the initial top-level result payload for a task."""
    return {
        "taskName": task["taskName"],
        "mode": task["mode"],
        "startedAt": now_iso(),
        "finishedAt": None,
        "totalSessions": len(task["sessions"]),
        "totalQuestions": task["totalQuestions"],
        "sessions": [],
    }


def write_result(output: str | Path, aggregate: dict, finished: bool = False) -> None:
    """Write both the debug and compact result JSON files."""
    if finished:
        aggregate["finishedAt"] = now_iso()
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    debug_path = debug_output_path(path)
    debug_path.write_text(json.dumps(aggregate, ensure_ascii=False, indent=2), encoding="utf-8")
    public_result = compact_aggregate(aggregate, debug_path=str(debug_path))
    path.write_text(json.dumps(public_result, ensure_ascii=False, indent=2), encoding="utf-8")


def debug_output_path(output: str | Path) -> Path:
    """Derive the sidecar debug JSON path from the public result path."""
    path = Path(output)
    return path.with_name(f"{path.stem}-debug{path.suffix}")


def compact_source(source: dict) -> dict:
    """Reduce a source record to the fields used in the public result."""
    compact = {
        "index": source.get("index"),
        "title": source.get("title", ""),
        "url": source.get("url", ""),
        "status": source.get("status", ""),
    }
    if source.get("error"):
        compact["error"] = source.get("error")
    return compact


def compact_result(result: dict) -> dict:
    """Reduce a question result to the public JSON shape."""
    compact = {
        "index": result.get("index"),
        "question": result.get("question", ""),
        "status": result.get("status", ""),
        "thinkingContent": result.get("thinkingContent", ""),
        "answer": result.get("answer", ""),
        "answerShareUrl": result.get("answerShareUrl", ""),
        "sources": [compact_source(source) for source in result.get("sources", [])],
    }
    debug = result.get("debug", {})
    if "thinking" in debug:
        compact["thinking"] = debug["thinking"]
    if "newChat" in debug:
        compact["newChat"] = debug["newChat"]
    source_extraction = debug.get("sourceExtraction")
    if source_extraction:
        compact["sourceSummary"] = source_extraction
    if result.get("error"):
        compact["error"] = result.get("error")
    if result.get("writeback"):
        compact["writeback"] = result.get("writeback")
    return compact


def compact_aggregate(aggregate: dict, debug_path: str | None = None) -> dict:
    """Reduce the full task aggregate to the public JSON shape."""
    compact = {
        "taskName": aggregate.get("taskName"),
        "mode": aggregate.get("mode"),
        "startedAt": aggregate.get("startedAt"),
        "finishedAt": aggregate.get("finishedAt"),
        "totalSessions": aggregate.get("totalSessions"),
        "totalQuestions": aggregate.get("totalQuestions"),
        "sessions": [],
    }
    if debug_path:
        compact["debugResult"] = debug_path
    if aggregate.get("writeback"):
        compact["writeback"] = aggregate.get("writeback")
    for session in aggregate.get("sessions", []):
        compact["sessions"].append({
            "sessionName": session.get("sessionName"),
            "newChat": session.get("newChat"),
            "thinking": session.get("thinking"),
            "results": [compact_result(result) for result in session.get("results", [])],
        })
    return compact
