# -*- coding: utf-8 -*-
"""对话历史压缩：LLM 语义摘要旧对话，近期尾部原样保留。

移植自 Data-Analysis-Agent 的 compaction 机制，适配法律问答场景：
  - 会话变长后，超出尾部预算的旧消息交给轻量 LLM 调用做结构化摘要；
  - 摘要持久化在 conversation_summaries 表（incremental：旧摘要参与下一次
    摘要，只处理新增消息），近期消息始终逐字进入下一轮上下文；
  - 摘要失败时降级为"尾部窗口截断"，绝不让压缩影响正常问答。
"""
import logging

LOG = logging.getLogger(__name__)

# 尾部预算：最近消息的字符总量低于该值时不压缩（原样全部保留）
TAIL_CHAR_BUDGET = 6000
# 摘要输入上限（字符）：避免摘要请求本身过大
MAX_SUMMARY_INPUT_CHARS = 16000
# 触发压缩的最少历史消息数（尾部预算之外的旧消息数）
MIN_MESSAGES_FOR_COMPACT = 4

_SUMMARY_SYSTEM = (
    "你是一名法律咨询对话的记录员。你的唯一任务是把一段历史对话压缩成"
    "结构化摘要。只输出摘要本身，不要任何开场白或评论。"
)

_SUMMARY_PROMPT = """\
以下是一段（可能已含此前摘要的）法律咨询历史对话，需要压缩为摘要。

要求：
- 保留用户提出的每一个问题（含案情细节：金额、日期、主体、地域）。
- 保留已给出的法律结论及其依据的具体条文（如"民法典第六百六十七条"）。
- 保留用户明确的约束、更正与偏好（如"不要引用已废止的合同法"）。
- 不要把未经核实的说法变成结论；保留证据边界。
- 未完成的追问、正在讨论的焦点必须写清。

<conversation_to_summarize>
{conversation_text}
</conversation_to_summarize>

请用以下标题输出结构化摘要（没有内容的小节可省略）：

## 1. 用户咨询目标
## 2. 已确认的案情事实
## 3. 已给出的结论与依据条文
## 4. 用户的约束与更正
## 5. 当前讨论焦点与待办
"""


def _render_messages(rows) -> str:
    """把 (role, content) 行渲染成摘要输入文本。"""
    parts = []
    for row in rows:
        role = "用户" if row["role"] == "user" else "助手"
        content = (row["content"] or "").strip()
        if content:
            parts.append(f"[{role}] {content}")
    return "\n\n".join(parts)


def _call_summarizer(llm, conversation_text: str) -> str:
    prompt = _SUMMARY_PROMPT.format(conversation_text=conversation_text)
    buffer = []
    for kind, piece in llm.stream(
        [{"role": "system", "content": _SUMMARY_SYSTEM},
         {"role": "user", "content": prompt}],
        temperature=0.1, max_tokens=1200,
    ):
        if kind == "delta":
            buffer.append(piece)
    return "".join(buffer).strip()


def load_tail(rows, budget: int = TAIL_CHAR_BUDGET):
    """按字符预算从最新往前保留消息，返回 (尾部起始下标, 尾部字符数)。"""
    used = 0
    start = len(rows)
    for index in range(len(rows) - 1, -1, -1):
        content = rows[index]["content"] or ""
        if start < len(rows) and used + len(content) > budget:
            break
        used += len(content)
        start = index
    return start, used


def build_conversation_context(db, conversation_id: str, rows, llm, now_iso) -> dict:
    """组装下一轮对话的上下文：持久化摘要（必要时增量压缩）+ 近期尾部。

    rows: 该会话的全部消息（含 user/assistant，时间正序）；已被摘要覆盖的
    前 summarized_count 条会先裁掉，避免重复进入尾部或被二次摘要。
    返回 {"summary": str|None, "tail": rows}。任何压缩失败都降级为纯尾部截断。
    """
    row = db.execute(
        "SELECT summary, summarized_count FROM conversation_summaries WHERE conversation_id=?",
        (conversation_id,)).fetchone()
    summary = row["summary"] if row else ""
    summarized_count = row["summarized_count"] if row else 0
    rows = rows[max(0, summarized_count):]

    tail_start, tail_chars = load_tail(rows)
    need_compact = (len(rows) - tail_start + summarized_count >= MIN_MESSAGES_FOR_COMPACT
                    and tail_start > 0)

    if need_compact and llm is not None and llm.configured:
        to_summarize = rows[:tail_start]
        text = _render_messages(to_summarize)
        if summary:
            text = f"[此前对话摘要]\n{summary}\n\n[新增对话]\n{text}"
        text = text[:MAX_SUMMARY_INPUT_CHARS]
        try:
            new_summary = _call_summarizer(llm, text)
            if new_summary:
                summary = new_summary
                summarized_count += len(to_summarize)
                db.execute(
                    "INSERT INTO conversation_summaries (conversation_id, summary,"
                    " summarized_count, updated_at) VALUES (?,?,?,?)"
                    " ON CONFLICT(conversation_id) DO UPDATE SET summary=excluded.summary,"
                    " summarized_count=excluded.summarized_count, updated_at=excluded.updated_at",
                    (conversation_id, summary, summarized_count, now_iso))
                LOG.info("对话压缩 · 会话 %s 摘要覆盖 %d 条消息",
                         conversation_id, summarized_count)
                rows = rows[tail_start:]
        except Exception as exc:  # 摘要失败 → 降级：仅用尾部窗口
            LOG.warning("对话压缩失败（%s），降级为尾部截断", exc)

    return {"summary": summary or None, "tail": rows}
