import time

from .adb_client import AdbClient
from .artifacts import save_state
from .constants import INPUT_ID
from .ui_xml import find_nodes, visible_texts


GENERATING_KEYWORDS = ("停止生成", "停止回答", "正在生成")


def extract_answer_text(nodes: list[dict], question: str) -> str:
    """Extract the latest stable answer text from visible UI nodes."""
    q = question.strip()
    texts = [text for text in visible_texts(nodes) if text and text.strip() != q]
    texts = [text for text in texts if not text.startswith("http://") and not text.startswith("https://")]
    texts = [text for text in texts if text not in ("深度思考", "打电话", "拍题答疑", "AI 生图", "豆包 P 图", "视频生成")]
    input_nodes = find_nodes(nodes, resource_id=INPUT_ID)
    input_texts = {node.get("text", "") for node in input_nodes}
    texts = [text for text in texts if text not in input_texts]
    # 移除最后一个 question 及其之前的文本（防止用户问句被拼接到答案中）
    last_q_index = -1
    for i in range(len(texts) - 1, -1, -1):
        if texts[i].strip() == q:
            last_q_index = i
            break
    if last_q_index >= 0:
        texts = texts[last_q_index + 1:]
    long_texts = [text for text in texts if len(text.strip()) > 20]
    return long_texts[-1].strip() if long_texts else ""


def has_generation_indicator(nodes: list[dict]) -> bool:
    """Detect whether the app still shows a live-generation indicator."""
    combined = "\n".join(visible_texts(nodes))
    return any(keyword in combined for keyword in GENERATING_KEYWORDS)


def wait_for_answer(adb: AdbClient, question: str, options: dict, output_dir: str) -> dict:
    """Poll the screen until the answer text stops changing."""
    timeout_ms = int(options.get("timeoutMs", 180000))
    required_stable = int(options.get("waitStableSeconds", 5))
    started = time.time()
    last_answer = ""
    stable = 0
    samples = []
    latest_state = None
    while (time.time() - started) * 1000 < timeout_ms:
        latest_state = save_state(adb, output_dir, "answer-sample")
        answer = extract_answer_text(latest_state["nodes"], question)
        generating = has_generation_indicator(latest_state["nodes"])
        if answer and answer == last_answer and not generating:
            stable += 1
        else:
            stable = 0
            last_answer = answer
        samples.append({"elapsedMs": int((time.time() - started) * 1000), "answerLength": len(answer), "stable": stable, "generating": generating})
        if answer and stable >= required_stable:
            break
        time.sleep(0.5)
    return {"answer": last_answer, "state": latest_state, "samples": samples[-20:]}
