"""评议说理评分：对照真实判决与卷宗，评价用户的证据判断、程序理解与说理完整性。

评分是复盘的附加体验：解析失败或模型异常时由调用方静默跳过，
不得影响判决对照与审判长复盘的主链路。
"""

import json
import re

REASONING_SCORE_PROMPT = """你是模拟法庭的评议指导法官。用户在旁听/参与庭审后作出了裁判结论并提交了说理。请对照法院真实判决与本案卷宗，评价这份说理的质量。

评分维度（各 0-10 的整数，6 分为及格）：
- evidence：证据判断——是否指向卷宗中的关键证据，是否混淆了证据效力；
- procedure：程序理解——对庭审程序、控辩职能与证明标准的理解是否正确；
- reasoning：说理完整性——结论与理由之间的逻辑是否成立，是否考虑了对方立场。

要求：
1. 只输出一个 JSON 对象，不要 markdown，不要 JSON 以外的文字。
2. 评分要克制：套话空话、只有结论没有理由的，reasoning 不应超过 4 分。
3. overall 用一句话肯定最有价值的部分；improvement 给一条最值得改进的建议。各不超过 80 字。

JSON 结构：
{"evidence": 7, "procedure": 6, "reasoning": 8,
 "overall": "一句话总评", "improvement": "一条改进建议"}"""

MAX_REASONING_CHARS = 800


def build_score_messages(case: dict, deliberation: dict, reasoning: str) -> list[dict]:
    verdict = case["real_verdict"]
    marks = deliberation.get("marks") or []
    context = {
        "案情摘要": case["facts"],
        "本案争点": case["disputes"],
        "用户裁判结论": deliberation.get("label", ""),
        "用户量刑意见": deliberation.get("years") if deliberation.get("convict") else None,
        "用户标注的关键证据": marks,
        "用户说理": reasoning[:MAX_REASONING_CHARS],
        "法院判决": verdict["summary"],
        "裁判理由": verdict["reasoning"],
    }
    return [{"role": "system", "content": REASONING_SCORE_PROMPT},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)}]


def _clamp_score(value, default=0) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, min(10, number))


def parse_score(text: str) -> dict | None:
    """解析评分 JSON；任何异常返回 None（调用方静默跳过评分展示）。"""
    raw = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fenced:
        raw = fenced.group(1)
    else:
        start, end = raw.find("{"), raw.rfind("}")
        if start >= 0 and end > start:
            raw = raw[start:end + 1]
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        scores = {key: _clamp_score(data.get(key))
                  for key in ("evidence", "procedure", "reasoning")}
        if not any(scores.values()):
            return None
        return {
            **scores,
            "overall": str(data.get("overall") or "").strip()[:120],
            "improvement": str(data.get("improvement") or "").strip()[:120],
        }
    except ValueError:
        return None
