import json
import re
import subprocess
import tempfile
from pathlib import Path
from shutil import which
from urllib.parse import parse_qs, urlparse

from .time_utils import now_iso, stamp


FEISHU_ANSWER_TABLE_ID = "tblht063DmBFxBiW"
FEISHU_SOURCE_TABLE_ID = "tblsVl8TzU5OeTHV"
DEFAULT_PLATFORM = "豆包"
DEFAULT_COLLECT_ACCOUNT = "18870501682"
ANSWER_WRITEBACK_FIELDS = ["采集账号", "自然问句", "是否开启深度思考", "AI回答", "深度思考", "关联自然问句", "是否触发联网", "对话链接"]
SOURCE_WRITEBACK_FIELDS = ["来源标题", "来源URL", "引用来源类型", "引用来源平台", "关联自然问句"]
DOMAIN_PLATFORM_MAP = [
    ("bjnews.com.cn", "新京报"),
    ("39.net", "39健康网"),
    ("mp.weixin.qq.com", "微信"),
    ("weixin.qq.com", "微信"),
    ("zhihu.com", "知乎"),
    ("xiaohongshu.com", "小红书"),
    ("douyin.com", "抖音"),
    ("iesdouyin.com", "抖音"),
    ("bilibili.com", "哔哩哔哩"),
    ("weibo.com", "微博"),
    ("sina.com.cn", "新浪"),
    ("sina.cn", "新浪新闻"),
    ("toutiao.com", "今日头条"),
    ("thepaper.cn", "澎湃新闻"),
    ("ifeng.com", "凤凰网"),
    ("163.com", "网易"),
    ("qq.com", "腾讯网"),
    ("sohu.com", "搜狐"),
    ("smzdm.com", "什么值得买"),
    ("baidu.com", "百度"),
    ("doubao.com", "豆包"),
    ("feishu.cn", "飞书"),
]
TITLE_PLATFORM_SEPARATORS = r"[_|｜—–-]"
DOMAIN_PREFIXES = ("www.", "m.", "h5.", "app.", "wap.", "i.", "k.")
TITLE_PLATFORM_DENYLIST = {
    "正文",
    "来源",
    "官网",
    "网页",
    "文章",
    "详情",
    "首页",
    "资讯",
    "新闻",
    "视频",
    "内容",
}


class FeishuError(RuntimeError):
    """Raised when a Feishu CLI call or task transformation fails."""
    pass


def clean_text(value) -> str:
    """Normalize whitespace and newlines in a text value."""
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", str(value or "").replace("\u00a0", " "))).strip()


def resolve_command(command: str) -> list[str]:
    """Resolve a CLI name to an executable path that subprocess can run."""
    candidate = Path(command)
    if candidate.is_file():
        return [str(candidate)]

    resolved = which(command)
    if resolved:
        resolved_path = Path(resolved)
        if resolved_path.suffix.lower() == ".ps1":
            powershell = which("powershell") or which("pwsh") or "powershell"
            return [
                powershell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(resolved_path),
            ]
        return [str(resolved_path)]

    if not candidate.suffix and Path(f"{command}.cmd").is_file():
        return [str(Path(f"{command}.cmd"))]
    if not candidate.suffix and Path(f"{command}.exe").is_file():
        return [str(Path(f"{command}.exe"))]
    if not candidate.suffix and Path(f"{command}.bat").is_file():
        return [str(Path(f"{command}.bat"))]
    return [command]


def is_yes(value) -> bool:
    """Interpret common yes-like values as booleans."""
    return value is True or value == "是" or (isinstance(value, list) and "是" in value)


def parse_base_location(args) -> dict:
    """Extract base token, table id, and view id from CLI arguments."""
    base_token = getattr(args, "base_token", None)
    table_id = getattr(args, "table_id", None)
    view_id = getattr(args, "view_id", None)
    base_url = getattr(args, "base_url", None)
    if base_url:
        parsed = urlparse(base_url)
        match = re.search(r"/base/([^/?#]+)", parsed.path)
        if not match:
            raise FeishuError("Feishu Base URL must contain /base/{baseToken}.")
        query = parse_qs(parsed.query)
        base_token = base_token or match.group(1)
        table_id = table_id or (query.get("table") or [None])[0]
        view_id = view_id or (query.get("view") or [None])[0]
    if not base_token or not table_id:
        raise FeishuError("Feishu Base mode requires --base-url or both --base-token and --table-id.")
    return {"baseToken": base_token, "tableId": table_id, "viewId": view_id}


def parse_base_row_range(args) -> tuple[int, int]:
    """Compute the inclusive Feishu row range to read."""
    base_start = getattr(args, "base_start", None)
    base_end = getattr(args, "base_end", None)
    base_limit = getattr(args, "base_limit", None)

    if base_start is None and base_end is None:
        return 1, max(1, int(base_limit or 1))

    if base_start is None:
        base_start = 1
    if base_end is None:
        base_end = base_start + max(1, int(base_limit or 1)) - 1
    if base_start < 1:
        raise FeishuError("--base-start must be >= 1.")
    if base_end < base_start:
        raise FeishuError("--base-end must be greater than or equal to --base-start.")
    return int(base_start), int(base_end)


def run_json_command(command: str, args: list[str]) -> dict:
    """Run a Feishu CLI command and parse its JSON response."""
    resolved_command = resolve_command(command)
    try:
        proc = subprocess.run([*resolved_command, *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError as exc:
        raise FeishuError(f"{command} not found. Install lark-cli or pass --lark-cli with the executable path.") from exc
    output = (proc.stdout or proc.stderr or "").strip()
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise FeishuError(f"{command} returned non-JSON output: {output or exc}") from exc
    if proc.returncode != 0 or not payload.get("ok"):
        raise FeishuError(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


def run_json_command_with_payload(command: str, args: list[str], payload: dict) -> dict:
    """Run a JSON command while storing the payload in a temp file."""
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", prefix=".lark-json-", suffix=".json", dir=".", delete=False) as handle:
            json.dump(payload, handle, ensure_ascii=False)
            tmp_path = handle.name
        json_index = args.index("--json")
        payload_ref = f"@.\\{Path(tmp_path).name}"
        args_with_payload = [*args[: json_index + 1], payload_ref, *args[json_index + 1 :]]
        return run_json_command(command, args_with_payload)
    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)


def list_feishu_records_page(base: dict, offset: int, limit: int, lark_cli: str = "lark-cli") -> dict:
    """Fetch one page of records from a Feishu Base table."""
    args = [
        "base", "+record-list",
        "--base-token", base["baseToken"],
        "--table-id", base["tableId"],
    ]
    if base.get("viewId"):
        args.extend(["--view-id", base["viewId"]])
    args.extend([
        "--field-id", "问题",
        "--field-id", "关联自然问句",
        "--field-id", "是否开启深度思考",
        "--field-id", "是否本次采集",
        "--offset", str(offset),
        "--limit", str(limit),
        "--format", "json",
    ])
    return run_json_command(lark_cli, args).get("data", {})


def list_feishu_records(base: dict, start_row: int, end_row: int, lark_cli: str = "lark-cli") -> dict:
    """Fetch a contiguous row range from Feishu Base."""
    if end_row < start_row:
        return {"data": [], "record_id_list": []}

    remaining = end_row - start_row + 1
    offset = start_row - 1
    rows: list = []
    record_ids: list[str] = []

    while remaining > 0:
        page = list_feishu_records_page(base, offset, min(remaining, 200), lark_cli)
        page_rows = page.get("data") or []
        page_record_ids = page.get("record_id_list") or []
        if not page_rows:
            break
        rows.extend(page_rows)
        record_ids.extend(page_record_ids[: len(page_rows)])
        fetched = len(page_rows)
        remaining -= fetched
        offset += fetched
        if fetched < 200:
            break

    return {"data": rows, "record_id_list": record_ids}


def build_task_from_feishu(args) -> dict:
    """Build a runnable task from the selected Feishu Base rows."""
    base = parse_base_location(args)
    base_start, base_end = parse_base_row_range(args)
    records = list_feishu_records(base, base_start, base_end, args.lark_cli)
    rows = records.get("data") or []
    record_ids = records.get("record_id_list") or []
    sessions = []
    skipped = []
    needs_thinking = False
    platform = args.platform or DEFAULT_PLATFORM

    for index, row in enumerate(rows):
        padded = [*row, None, None, None, None]
        question, linked_natural_question, thinking, collect_now = padded[:4]
        record_id = record_ids[index] if index < len(record_ids) else None
        question_text = clean_text(question)
        should_collect = is_yes(collect_now)
        if not question_text or not should_collect:
            skipped.append({
                "recordId": record_id,
                "question": question_text,
                "collectNow": collect_now,
                "reason": "empty-question" if not question_text else "not-selected",
            })
            continue
        row_thinking = False if getattr(args, "force_quick", False) else is_yes(thinking)
        if row_thinking:
            needs_thinking = True
        sessions.append({
            "sessionName": f"feishu-{record_id or index + 1}",
            "newChat": True,
            "thinking": row_thinking,
            "questions": [question_text],
            "meta": {
                "feishuRecordId": record_id,
                "baseToken": base["baseToken"],
                "tableId": base["tableId"],
                "viewId": base.get("viewId"),
                "naturalQuestion": question_text,
                "linkedNaturalQuestion": clean_text(linked_natural_question),
                "fullQuestion": question_text,
                "thinking": row_thinking,
                "platform": platform,
            },
        })

    task = {
        "taskName": "doubao-feishu-base-run",
        "mode": "separate",
        "thinking": bool(needs_thinking),
        "sessions": sessions,
        "options": {
            "sourceLimit": max(1, int(getattr(args, "source_limit", 2))),
            "waitStableSeconds": 1,
            "intervalMs": 0,
            "timeoutMs": 180000,
            "expertAnswerTopScrolls": 4,
            "expertAnswerMaxScrolls": 8,
            "answerShareMaxScrolls": 8,
            "answerShareWaitSeconds": 0.3,
            "sourcePageWaitSeconds": 0.3,
            "sourceShareWaitSeconds": 0.15,
            "debug": {
                "screenshots": False,
                "currentFocus": False,
            },
        },
        "output": f"results/doubao-feishu-base-run-{stamp()}.json",
    }
    return {
        "task": task,
        "taskPath": None,
        "base": {**base, "rowStart": base_start, "rowEnd": base_end, "rowRange": [base_start, base_end]},
        "skipped": skipped,
        "source": "feishu-base",
    }


def create_feishu_records(base: dict, table_id: str, fields: list[str], rows: list[list], lark_cli: str = "lark-cli", dry_run: bool = False) -> dict:
    """Create one or more Feishu records via lark-cli."""
    if not rows:
        return {"skipped": True, "reason": "no-rows", "tableId": table_id, "count": 0}
    args = [
        "base", "+record-batch-create",
        "--base-token", base["baseToken"],
        "--table-id", table_id,
        "--json",
        "--format", "json",
    ]
    if dry_run:
        args.append("--dry-run")
    return run_json_command_with_payload(lark_cli, args, {"fields": fields, "rows": rows})


def update_feishu_task_rows(base: dict, record_ids: list[str], lark_cli: str = "lark-cli", dry_run: bool = False) -> dict:
    """Mark selected Feishu task rows as not collected."""
    if not record_ids:
        return {"skipped": True, "reason": "no-record-ids", "count": 0}
    args = [
        "base", "+record-batch-update",
        "--base-token", base["baseToken"],
        "--table-id", base["tableId"],
        "--json",
        "--format", "json",
    ]
    if dry_run:
        args.append("--dry-run")
    return run_json_command_with_payload(lark_cli, args, {"record_id_list": record_ids, "patch": {"是否本次采集": "否"}})


def normalize_source_platform(value) -> str:
    """Strip trailing date noise from a source platform value."""
    text = re.sub(r"\b\d{4}[/-]\d{1,2}[/-]\d{1,2}\b", "", clean_text(value)).strip()
    if not text or re.fullmatch(r"\d+", text):
        return ""
    return re.sub(r"\s+\d{4}([/-]\d{1,2}){0,2}$", "", text).strip()


def extract_platform_from_title(title: str) -> str:
    """Infer a platform name from the tail of a source title."""
    text = clean_text(title)
    if not text:
        return ""
    parts = re.split(rf"\s*{TITLE_PLATFORM_SEPARATORS}\s*", text)
    candidate = clean_text(parts[-1]) if parts else ""
    if not candidate:
        return ""
    if not (2 <= len(candidate) <= 12):
        return ""
    if " " in candidate or candidate.isdigit() or "." in candidate or "/" in candidate:
        return ""
    if candidate in TITLE_PLATFORM_DENYLIST:
        return ""
    if re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", candidate):
        return ""
    return candidate


def extract_domain_from_url(url: str) -> str:
    """Extract the normalized host name from a URL."""
    parsed = urlparse(clean_text(url))
    host = (parsed.netloc or "").lower().strip()
    if not host:
        return ""
    host = host.split("@")[-1].split(":")[0]
    changed = True
    while changed:
        changed = False
        for prefix in DOMAIN_PREFIXES:
            if host.startswith(prefix):
                host = host[len(prefix):]
                changed = True
    return host


def extract_registered_domain_label(domain: str) -> str:
    """Derive a human-readable domain label from a host name."""
    cleaned = clean_text(domain).lower()
    if not cleaned:
        return ""
    labels = [part for part in cleaned.split(".") if part]
    if len(labels) < 2:
        return labels[0] if labels else ""
    suffix_two = ".".join(labels[-2:])
    suffix_three = ".".join(labels[-3:]) if len(labels) >= 3 else ""
    known_suffixes = {
        "com.cn", "net.cn", "org.cn", "gov.cn", "edu.cn", "ac.cn", "co.uk", "com.hk", "com.tw", "com.au", "co.jp",
    }
    if suffix_two in known_suffixes and len(labels) >= 3:
        return labels[-3]
    if suffix_three in known_suffixes and len(labels) >= 4:
        return labels[-4]
    return labels[-2]


def map_domain_to_platform(url: str, fallback: str = "") -> str:
    """Map a URL domain to a known platform label."""
    domain = extract_domain_from_url(url)
    if not domain:
        return clean_text(fallback)
    for suffix, platform in DOMAIN_PLATFORM_MAP:
        if domain == suffix or domain.endswith("." + suffix):
            return platform
    return extract_registered_domain_label(domain) or domain or clean_text(fallback)


def resolve_source_platform(title: str, url: str, fallback: str = "") -> str:
    """Resolve a source platform from title, domain, or fallback text."""
    from_title = extract_platform_from_title(title)
    if from_title:
        return from_title
    from_domain = map_domain_to_platform(url, fallback)
    if from_domain:
        return from_domain
    return clean_text(fallback)


def infer_source_type(source: dict, platform: str) -> str:
    """Infer whether a source is video or text/article based."""
    text = f"{source.get('url', '')} {source.get('title', '')} {source.get('snippet', '')} {platform}"
    return "视频" if re.search(r"douyin\.com|iesdouyin\.com|抖音|视频", text) else "图文"


def unique_sources(sources: list[dict]) -> list[dict]:
    """Deduplicate sources by URL and normalize their metadata."""
    seen = set()
    result = []
    for source in sources or []:
        url = clean_text(source.get("url"))
        if not re.match(r"^https?://", url) or url in seen:
            continue
        seen.add(url)
        title = clean_text(source.get("title")) or url
        platform = resolve_source_platform(title, url, source.get("platform"))
        result.append({"title": title, "url": url, "platform": platform, "sourceType": infer_source_type(source, platform)})
    return result


def clean_answer_for_writeback(answer: str, question: str = "") -> str:
    """Trim prompt echoes and boilerplate from an answer before writeback."""
    text = clean_text(answer)
    q = clean_text(question)
    if q and text.startswith(q):
        remaining = text[len(q):].lstrip()
        # 仅当剩余内容仍足够长时才截断，避免误删真正的答案开头
        if len(remaining) >= 20:
            text = remaining
    return re.sub(r"\n?本回答由\s*AI\s*生成[\s\S]*$", "", text).strip()


def extract_quick_search_keywords(answer: str) -> tuple[str, list[str]]:
    """Extract quoted quick-search keywords from an answer."""
    text = clean_text(answer)
    if not text:
        return "", []
    head = "\n\n".join(text.split("\n\n")[:2])
    keywords = re.findall(r"[\"“](.+?)[\"”]", head)
    cleaned = []
    for keyword in keywords:
        item = clean_text(keyword)
        if item and item not in cleaned:
            cleaned.append(item)
    if cleaned:
        return f"搜索关键词：{'、'.join(cleaned)}", cleaned
    return "", []


def strip_quick_search_keywords(answer: str, keywords: list[str]) -> str:
    """Remove quick-search keywords from the answer body."""
    text = answer
    for keyword in keywords:
        keyword = clean_text(keyword)
        if not keyword:
            continue
        text = re.sub(rf"[\"“”]?\s*{re.escape(keyword)}\s*[\"”“]?", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def build_feishu_writeback_rows_for_result(source_session: dict, result: dict, collect_account: str | None = None) -> dict:
    """Build Feishu answer and source rows from a collected result."""
    answer_rows = []
    source_rows = []
    if result.get("writebackAllowed") is False:
        return {"answerRows": answer_rows, "sourceRows": source_rows}
    if result.get("status") not in {"success", "partial"}:
        return {"answerRows": answer_rows, "sourceRows": source_rows}
    meta = source_session.get("meta") or {}
    questions = source_session.get("questions") or []
    first_question = questions[0].get("text") if questions and isinstance(questions[0], dict) else (questions[0] if questions else "")
    natural_question = clean_text(meta.get("naturalQuestion")) or clean_text(first_question)
    cleaned_answer = clean_answer_for_writeback(result.get("answer", ""), natural_question)
    if not cleaned_answer:
        return {"answerRows": answer_rows, "sourceRows": source_rows}
    linked_natural_question = clean_text(meta.get("linkedNaturalQuestion"))
    platform = clean_text(meta.get("platform")) or DEFAULT_PLATFORM
    sources = unique_sources(result.get("sources", []))
    quick_search_keywords, keyword_list = extract_quick_search_keywords(result.get("answer", ""))
    thinking_text = clean_text(result.get("thinkingContent")) or quick_search_keywords
    if keyword_list:
        cleaned_answer = strip_quick_search_keywords(cleaned_answer, keyword_list)
    answer_rows.append([
        clean_text(collect_account) or DEFAULT_COLLECT_ACCOUNT,
        natural_question,
        "是" if meta.get("thinking") else "否",
        cleaned_answer,
        thinking_text,
        linked_natural_question,
        "是" if sources else "否",
        clean_text(result.get("answerShareUrl")),
    ])
    for source in sources:
        source_rows.append([source["title"], source["url"], source["sourceType"], source["platform"], linked_natural_question])
    return {"answerRows": answer_rows, "sourceRows": source_rows}


def write_feishu_result(writeback_context: dict, source_session: dict, result: dict) -> dict:
    """Write answer and source rows back to Feishu Base."""
    rows = build_feishu_writeback_rows_for_result(source_session, result, writeback_context.get("collectAccount"))
    answer_fields = ANSWER_WRITEBACK_FIELDS
    source_fields = SOURCE_WRITEBACK_FIELDS
    base = writeback_context["base"]
    lark_cli = writeback_context.get("larkCli", "lark-cli")
    dry_run = bool(writeback_context.get("dryRun"))
    answer_table_id = writeback_context.get("answerTableId") or FEISHU_ANSWER_TABLE_ID
    source_table_id = writeback_context.get("sourceTableId") or FEISHU_SOURCE_TABLE_ID
    answer_result = create_feishu_records(base, answer_table_id, answer_fields, rows["answerRows"], lark_cli, dry_run)
    if writeback_context.get("skipSourceWrite"):
        source_result = {
            "skipped": True,
            "reason": writeback_context.get("sourceWriteMode") or "handled_elsewhere",
            "tableId": source_table_id,
            "count": len(rows["sourceRows"]),
        }
    else:
        source_result = create_feishu_records(base, source_table_id, source_fields, rows["sourceRows"], lark_cli, dry_run)
    source_record_id = (source_session.get("meta") or {}).get("feishuRecordId")
    if writeback_context.get("markCollected") and rows["answerRows"] and source_record_id:
        source_update_result = update_feishu_task_rows(base, [source_record_id], lark_cli, dry_run)
    else:
        source_update_result = {"skipped": True, "reason": "no-source-record-id-or-answer-row" if writeback_context.get("markCollected") else "mark-collected-disabled"}
    return {
        "finishedAt": now_iso(),
        "answerTableId": answer_table_id,
        "sourceTableId": source_table_id,
        "inputTableId": base["tableId"],
        "sourceRecordId": source_record_id,
        "markCollected": bool(writeback_context.get("markCollected")),
        "writebackAllowed": result.get("writebackAllowed", True),
        "writebackGuard": result.get("writebackGuard"),
        "answerCount": len(rows["answerRows"]),
        "sourceCount": 0 if writeback_context.get("skipSourceWrite") else len(rows["sourceRows"]),
        "answerResult": answer_result,
        "sourceResult": source_result,
        "sourceUpdateResult": source_update_result,
    }


def planned_writeback(
    task: dict,
    enabled: bool,
    mark_collected: bool = False,
    answer_table_id: str = "",
    source_table_id: str = "",
) -> dict:
    """Describe the Feishu writeback work that would be performed."""
    collect_account = clean_text(task.get("options", {}).get("collectAccount")) or DEFAULT_COLLECT_ACCOUNT
    record_ids = [
        session.get("meta", {}).get("feishuRecordId")
        for session in task.get("sessions", [])
        if session.get("meta", {}).get("feishuRecordId")
    ]
    return {
        "enabled": bool(enabled),
        "action": "create Doubao answer/source records immediately after each successful or partial result",
        "answerTableId": clean_text(answer_table_id) or FEISHU_ANSWER_TABLE_ID,
        "sourceTableId": clean_text(source_table_id) or FEISHU_SOURCE_TABLE_ID,
        "collectAccount": collect_account,
        "markCollected": bool(mark_collected),
        "markCollectedField": "是否本次采集",
        "markCollectedValue": "否",
        "recordIds": record_ids,
        "answerFields": ANSWER_WRITEBACK_FIELDS,
        "sourceFields": SOURCE_WRITEBACK_FIELDS,
    }
