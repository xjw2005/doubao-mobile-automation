"""Bridge module: invokes the Doubao share-page source extractor JS script.

Pipeline:
    手机端捕获 share_url + linked_natural_question
        -> 本桥接模块校验参数
        -> 调用 node doubao-source-extractor/run.js
        -> JS 脚本解析豆包分享页 shareInfo.message_snapshot
        -> 返回 answer / thinkingContent / sources，并可写回飞书来源表
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .time_utils import now_iso, stamp


DEFAULT_SCRIPT_RELATIVE = Path("doubao-source-extractor", "run.js")
DEFAULT_CDP_URL = "http://127.0.0.1:9222"
DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_MAX_RETRIES = 2
DEFAULT_RETRY_BACKOFF_BASE = 2.0

DOUBAO_SHARE_URL_RE = re.compile(
    r"^https?://(www\.)?doubao\.com/thread/[A-Za-z0-9]+(?:[/?#].*)?$",
    re.IGNORECASE,
)


class SourceExtractorError(RuntimeError):
    """Raised when the JS source extractor cannot complete successfully."""


@dataclass
class ExtractorOptions:
    """Runtime configuration for the JS source extractor."""

    script_path: str = ""
    cdp_url: str = DEFAULT_CDP_URL
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES
    retry_backoff_base: float = DEFAULT_RETRY_BACKOFF_BASE
    node_binary: str = "node"
    extra_args: list[str] = field(default_factory=list)

    @classmethod
    def from_task_options(cls, task_options: dict) -> "ExtractorOptions":
        """Build options from task['options']['sourceExtractor']."""
        cfg = task_options.get("sourceExtractor") or {}
        return cls(
            script_path=cfg.get("scriptPath", ""),
            cdp_url=cfg.get("cdpUrl", DEFAULT_CDP_URL),
            timeout_seconds=int(cfg.get("timeoutSeconds", DEFAULT_TIMEOUT_SECONDS)),
            max_retries=int(cfg.get("maxRetries", DEFAULT_MAX_RETRIES)),
            retry_backoff_base=float(cfg.get("retryBackoffBase", DEFAULT_RETRY_BACKOFF_BASE)),
            node_binary=cfg.get("nodeBinary", "node"),
            extra_args=list(cfg.get("extraArgs", [])),
        )


def resolve_script_path(explicit: str = "") -> Path:
    """Locate the extractor run.js script from an explicit or default path."""
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.exists():
            raise SourceExtractorError(f"Source extractor script not found: {path}")
        return path
    here = Path(__file__).resolve().parent
    candidate = here.parent / DEFAULT_SCRIPT_RELATIVE
    if candidate.exists():
        return candidate
    candidate = Path.cwd() / DEFAULT_SCRIPT_RELATIVE
    if candidate.exists():
        return candidate
    raise SourceExtractorError(
        f"Could not locate doubao-source-extractor/run.js. "
        f"Checked: {here.parent / DEFAULT_SCRIPT_RELATIVE} and {Path.cwd() / DEFAULT_SCRIPT_RELATIVE}. "
        f"Pass options.sourceExtractor.scriptPath explicitly."
    )


def validate_params(share_url: str, natural_question: str) -> None:
    """Validate the share URL and question ID before extraction."""
    if not share_url or not share_url.strip():
        raise SourceExtractorError("share_url is required (mobile side must capture the answer share link first)")
    if not natural_question or not natural_question.strip():
        raise SourceExtractorError("natural_question is required (question ID from Feishu)")
    if not DOUBAO_SHARE_URL_RE.match(share_url.strip()):
        raise SourceExtractorError(
            f"share_url does not look like a Doubao share URL: {share_url[:80]!r}. "
            f"Expected: https://www.doubao.com/thread/<id>"
        )


def _build_command(
    options: ExtractorOptions,
    script_path: Path,
    share_url: str,
    natural_question: str,
    base_token: str,
    table_id: str,
    output_file: Path,
    extract_only: bool = False,
) -> list[str]:
    """Build the node command used to invoke the extractor."""
    cmd = [
        options.node_binary,
        str(script_path),
        "--url",
        share_url,
        "--cdp",
        options.cdp_url,
        "--output",
        str(output_file),
    ]
    if extract_only:
        cmd.append("--extract-only")
    else:
        cmd.extend([
            "--natural-question",
            natural_question,
            "--base-token",
            base_token,
            "--table-id",
            table_id,
        ])
    cmd.extend(options.extra_args)
    return cmd


def _invoke_once(
    options: ExtractorOptions,
    script_path: Path,
    share_url: str,
    natural_question: str,
    base_token: str,
    table_id: str,
    output_file: Path,
    logger: logging.Logger,
    attempt: int,
    extract_only: bool = False,
) -> dict:
    """Invoke the JS extractor once and parse its output artifacts."""
    cmd = _build_command(options, script_path, share_url, natural_question, base_token, table_id, output_file, extract_only=extract_only)
    logger.info("Attempt %d: %s", attempt, " ".join(cmd[:6]) + " ...")

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=options.timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise SourceExtractorError(f"JS source extractor timed out after {options.timeout_seconds}s (attempt {attempt})") from exc
    except FileNotFoundError as exc:
        raise SourceExtractorError(f"Node.js binary not found: {options.node_binary}. Install Node.js or set options.sourceExtractor.nodeBinary.") from exc

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    logger.info("Attempt %d exit code: %d", attempt, proc.returncode)
    if stdout:
        logger.info("Attempt %d stdout (tail):\n%s", attempt, _tail(stdout, 1200))
    if stderr:
        logger.warning("Attempt %d stderr (tail):\n%s", attempt, _tail(stderr, 1200))

    if proc.returncode != 0:
        raise SourceExtractorError(
            f"JS source extractor exited with code {proc.returncode} (attempt {attempt}). "
            f"stderr: {_tail(stderr, 400)}"
        )

    result_payload: dict = {
        "ok": True,
        "exitCode": proc.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "outputFile": str(output_file),
        "extractedAt": now_iso(),
        "attempt": attempt,
    }

    if output_file.exists():
        try:
            extracted = json.loads(output_file.read_text(encoding="utf-8"))
            result_payload["extractedSources"] = extracted
            result_payload["sourceCount"] = int(extracted.get("count", 0)) if extracted.get("ok") else 0
            result_payload["extractOk"] = bool(extracted.get("ok"))
        except json.JSONDecodeError as exc:
            logger.warning("Could not parse output file %s: %s", output_file, exc)
            result_payload["extractOk"] = False
            result_payload["extractError"] = f"output_file_parse_failed: {exc}"
    else:
        logger.warning("Output file not created by JS script: %s", output_file)
        result_payload["extractOk"] = False
        result_payload["extractError"] = "output_file_not_created"

    write_result = _parse_write_result(stdout)
    if write_result is not None:
        result_payload["feishuWriteResult"] = write_result
        result_payload["feishuWriteOk"] = bool(write_result.get("ok"))
    else:
        result_payload["feishuWriteOk"] = None

    return result_payload


def _tail(text: str, max_chars: int) -> str:
    """Keep only the tail of a long log string."""
    if len(text) <= max_chars:
        return text
    return "..." + text[-max_chars:]


_WRITE_RESULT_RE = re.compile(r"Write result:\s*(\{.*\})", re.DOTALL)


def _parse_write_result(stdout: str) -> dict | None:
    """Extract the JSON writeback payload from stdout."""
    match = _WRITE_RESULT_RE.search(stdout)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def _get_logger(output_dir: str | Path) -> logging.Logger:
    """Create or reuse a logger for one extraction run."""
    logger = logging.getLogger(f"doubao.source_extractor.{stamp()}")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    log_path = Path(output_dir, "source-extractor.log")
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(handler)
    except OSError:
        pass
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("[source-extractor] %(message)s"))
    logger.addHandler(console)
    return logger


def run_source_extractor(
    share_url: str,
    natural_question: str,
    base_token: str = "",
    table_id: str = "",
    output_dir: str | Path = "",
    options: ExtractorOptions | None = None,
) -> dict:
    """Run the JS extraction flow with retries and logging.

    When *base_token* is empty the extractor runs in extract-only mode and does
    not write sources to Feishu.
    """
    options = options or ExtractorOptions()
    output_dir = Path(output_dir) if output_dir else Path.cwd() / "outputs" / "extractor"
    output_dir.mkdir(parents=True, exist_ok=True)
    logger = _get_logger(output_dir)
    extract_only = not base_token

    validate_params(share_url, natural_question)
    if not extract_only and not table_id:
        raise SourceExtractorError("table_id is required for Feishu source table writeback")

    script_path = resolve_script_path(options.script_path)
    logger.info("Script: %s", script_path)
    logger.info("Share URL: %s", share_url)
    logger.info("Question ID: %s", natural_question)
    logger.info("Mode: %s", "extract-only" if extract_only else f"full (base={base_token} table={table_id})")

    attempts: list[dict] = []
    last_error: Exception | None = None
    output_file = output_dir / f"sources-{stamp()}.json"
    for attempt in range(1, options.max_retries + 2):
        try:
            result = _invoke_once(
                options,
                script_path,
                share_url,
                natural_question,
                base_token,
                table_id,
                output_file,
                logger,
                attempt,
                extract_only=extract_only,
            )
            result["attempts"] = attempts
            result["status"] = "success"
            logger.info("Extraction succeeded on attempt %d (sources=%d)", attempt, result.get("sourceCount", 0))
            return result
        except SourceExtractorError as exc:
            last_error = exc
            attempts.append({"attempt": attempt, "error": str(exc), "timestamp": now_iso()})
            logger.warning("Attempt %d failed: %s", attempt, exc)
            if attempt <= options.max_retries:
                backoff = options.retry_backoff_base ** attempt
                logger.info("Retrying in %.1fs...", backoff)
                time.sleep(backoff)

    logger.error("All %d attempts failed. Last error: %s", len(attempts), last_error)
    return {
        "ok": False,
        "status": "failed",
        "error": str(last_error) if last_error else "unknown",
        "attempts": attempts,
        "extractedAt": now_iso(),
        "sourceCount": 0,
        "extractOk": False,
        "feishuWriteOk": False,
        "outputFile": str(output_file),
    }
