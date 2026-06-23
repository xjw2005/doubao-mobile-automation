import json
from pathlib import Path

from .constants import DEFAULT_ADB, DEFAULT_SERIAL
from .time_utils import stamp


class TaskError(ValueError):
    """Raised when a task JSON document is invalid."""
    pass


def load_task(path: str | Path) -> dict:
    """Load and normalize a task JSON file from disk."""
    task_path = Path(path)
    task = json.loads(task_path.read_text(encoding="utf-8"))
    return normalize_task(task)


def bool_value(value, default: bool) -> bool:
    """Normalize common truthy representations into a boolean."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def normalize_source_limit(value) -> int | str:
    """Normalize the source limit, preserving the special 'all' value."""
    if isinstance(value, str) and value.strip().lower() == "all":
        return "all"
    return max(0, int(value if value is not None else 5))


def thinking_value(value, default):
    """Normalize a thinking-mode value while preserving None defaults."""
    if value is None:
        return default
    return bool_value(value, bool(default))


def normalize_question(question, q_index: int, session_name: str, session_new_chat: bool, session_thinking) -> dict:
    """Normalize a single question entry inside a session."""
    if isinstance(question, str):
        if not question.strip():
            raise TaskError(f"Question {q_index} in session {session_name} must be a non-empty string.")
        return {"text": question, "newChat": session_new_chat, "thinking": session_thinking}
    if not isinstance(question, dict):
        raise TaskError(f"Question {q_index} in session {session_name} must be a string or object.")
    text = question.get("text") or question.get("question")
    if not isinstance(text, str) or not text.strip():
        raise TaskError(f"Question {q_index} in session {session_name} must contain non-empty text.")
    return {
        "text": text,
        "newChat": bool_value(question.get("newChat"), session_new_chat),
        "thinking": thinking_value(question.get("thinking"), session_thinking),
    }


def normalize_task(task: dict) -> dict:
    """Validate and normalize the full task structure."""
    if not isinstance(task, dict):
        raise TaskError("Task JSON must be an object.")
    sessions = task.get("sessions")
    if not isinstance(sessions, list) or not sessions:
        raise TaskError("Task JSON must contain non-empty sessions[].")
    task_thinking = thinking_value(task.get("thinking"), None)
    normalized_sessions = []
    total_questions = 0
    for index, session in enumerate(sessions, start=1):
        if not isinstance(session, dict):
            raise TaskError(f"sessions[{index}] must be an object.")
        session_name = session.get("sessionName") or f"session-{index}"
        questions = session.get("questions")
        if not isinstance(questions, list) or not questions:
            raise TaskError(f"Session {session_name} must contain non-empty questions[].")
        session_new_chat = bool_value(session.get("newChat"), False)
        session_thinking = thinking_value(session.get("thinking"), task_thinking)
        cleaned_questions = []
        for q_index, question in enumerate(questions, start=1):
            cleaned_questions.append(normalize_question(question, q_index, session_name, session_new_chat, session_thinking))
        total_questions += len(cleaned_questions)
        normalized_sessions.append({
            "sessionName": session_name,
            "newChat": session_new_chat,
            "thinking": session_thinking,
            "questions": cleaned_questions,
            "meta": session.get("meta") if isinstance(session.get("meta"), dict) else {},
        })
    options = {
        "sourceLimit": 5,
        "waitStableSeconds": 5,
        "intervalMs": 3000,
        "timeoutMs": 180000,
        "debug": {},
        **(task.get("options") or {}),
    }
    options["sourceLimit"] = normalize_source_limit(options.get("sourceLimit", 5))
    options["waitStableSeconds"] = max(1, int(options.get("waitStableSeconds", 5)))
    options["intervalMs"] = max(0, int(options.get("intervalMs", 3000)))
    options["timeoutMs"] = max(1000, int(options.get("timeoutMs", 180000)))
    debug_options = options.get("debug") if isinstance(options.get("debug"), dict) else {}
    options["debug"] = {
        "enabled": bool_value(debug_options.get("enabled"), False),
        "screenshots": bool_value(debug_options.get("screenshots"), True),
        "currentFocus": bool_value(debug_options.get("currentFocus"), True),
    }
    device = {"adb": DEFAULT_ADB, "serial": DEFAULT_SERIAL, **(task.get("device") or {})}
    task_name = task.get("taskName") or "doubao-mobile-run"
    output = task.get("output") or f"results/{task_name}-{stamp()}.json"
    return {
        "taskName": task_name,
        "mode": task.get("mode") or "separate",
        "thinking": task_thinking,
        "device": device,
        "sessions": normalized_sessions,
        "options": options,
        "output": output,
        "totalQuestions": total_questions,
    }


def summarize_task(task: dict) -> dict:
    """Create a compact task summary for dry-run output."""
    return {
        "taskName": task["taskName"],
        "mode": task["mode"],
        "output": task["output"],
        "device": task["device"],
        "options": task["options"],
        "totalSessions": len(task["sessions"]),
        "totalQuestions": task["totalQuestions"],
        "sessions": [
            {
                "sessionName": session["sessionName"],
                "newChat": session["newChat"],
                "thinking": session["thinking"],
                "meta": session.get("meta", {}),
                "questionCount": len(session["questions"]),
                "questions": [
                    {"newChat": question["newChat"], "thinking": question["thinking"]}
                    for question in session["questions"]
                ],
            }
            for session in task["sessions"]
        ],
    }
