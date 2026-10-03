"""法律问题工作台：结构化案情采集 → 分析报告生成与解析。

与问答链路共用检索（RAG hits → qa.Evidence），但输出不是自由文本，
而是一份固定 schema 的 JSON 报告：问题归类、事实摘要、适用依据、
缺少的信息、证据清单、行动清单、风险提醒。适用依据只能从检索白名单
中挑选，与问答引用同一套反幻觉纪律。
"""

import json
import re

from .courtroom import cases as court_cases

# 采集字段：key → (中文名, 必填, 最大长度)
INTAKE_FIELDS = {
    "what": ("发生了什么", True, 2000),
    "when_where": ("时间与地点", False, 200),
    "parties": ("当事人关系", False, 200),
    "amount": ("涉及金额", False, 100),
    "evidence": ("已有证据", False, 500),
    "goal": ("你的诉求", True, 500),
    "urgent": ("期限或紧急风险", False, 200),
}

MATTER_SYSTEM_PROMPT = """你是法律助手「法晓」的问题工作台分析引擎。根据用户的案情采集信息和检索到的法条证据，输出一份结构化分析报告。

严格规则：
1. 只输出一个 JSON 对象，不要 markdown 代码块，不要任何 JSON 以外的文字。
2. "applicable_laws" 只能从「检索证据」列表中挑选，逐字保留其 title 和 article，excerpt 可截取至 80 字；没有合适证据时输出空数组，绝不编造法条。
3. 没有依据支撑的判断要放进 "missing_info" 或 "risks"，不要伪装成有依据的结论。
4. 全部使用简体中文，通俗、克制；行动步骤按时间顺序，具体可执行；不提供确定性法律结论，不做胜诉预测。
5. fact_summary 不超过 120 字；每条 risks 不超过 60 字。

JSON 结构：
{
  "issue_type": "问题归类，如：劳动争议 / 合同纠纷 / 消费维权 / 侵权责任 / 婚姻家庭 / 刑事风险 / 其他",
  "fact_summary": "事实摘要",
  "applicable_laws": [{"title": "法律名称", "article": "第×条", "reason": "为什么适用", "excerpt": "条文节选"}],
  "missing_info": ["仍需补充的关键信息"],
  "evidence_checklist": ["建议收集的证据"],
  "action_steps": [{"step": "第一步", "detail": "怎么做"}],
  "risks": ["风险或期限提醒"],
  "disclaimer": "一句话免责说明"
}"""

# 推荐模拟庭审：按关键词把事项映射到内置案例（最多 2 个）
CASE_MATCH_KEYWORDS = {
    "haicheng": ("正当防卫", "防卫", "故意伤害", "打架", "斗殴", "行凶", "伤人", "砍伤", "刺伤"),
    "kuaidizhan": ("诽谤", "侮辱", "造谣", "网络暴力", "名誉", "谣言"),
    "shenduzhuanzhang": ("帮信", "帮助信息网络犯罪", "掩饰", "隐瞒犯罪所得", "出借银行卡",
                         "跑分", "洗钱", "转账", "电信诈骗", "诈骗"),
}


def intake_from_request(data, text_value) -> dict:
    """读取并校验采集表单；text_value 沿用应用的 400 安全读取。"""
    intake = {}
    for key, (label, required, limit) in INTAKE_FIELDS.items():
        value = text_value(data, key, limit=limit)
        if required and not (2 <= len(value) <= limit if key == "goal"
                             else 10 <= len(value) <= limit):
            raise ValueError(f"{label}为必填项（{ '2' if key == 'goal' else '10' }-{limit} 字）")
        intake[key] = value
    return intake


def build_matter_messages(intake: dict, evidence_refs: list[dict]) -> list[dict]:
    lines = ["用户案情采集："]
    for key, (label, _required, _limit) in INTAKE_FIELDS.items():
        if intake.get(key):
            lines.append(f"- {label}：{intake[key]}")
    context = {
        "案情": "\n".join(lines),
        "检索证据": [{"title": ref["source"], "article": ref["article"],
                     "excerpt": ref["excerpt"][:160]}
                    for ref in evidence_refs[:10]],
    }
    return [{"role": "system", "content": MATTER_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)}]


def _clamp_list(value, limit=8, item_limit=200) -> list:
    if not isinstance(value, list):
        return []
    cleaned = []
    for item in value[:limit]:
        text = str(item).strip()
        if text:
            cleaned.append(text[:item_limit])
    return cleaned


def parse_matter_result(text: str, evidence_refs: list[dict]) -> dict:
    """解析模型输出并按 schema 清洗；适用依据强制回填检索白名单原文。"""
    raw = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fenced:
        raw = fenced.group(1)
    else:
        start, end = raw.find("{"), raw.rfind("}")
        if start >= 0 and end > start:
            raw = raw[start:end + 1]
    data = json.loads(raw)  # 由调用方捕获 ValueError
    if not isinstance(data, dict):
        raise ValueError("结构化结果不是对象")

    whitelist = {}
    for ref in evidence_refs:
        whitelist[(ref["source"], ref["article"])] = ref
    laws = []
    for item in data.get("applicable_laws") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()[:120]
        article = str(item.get("article") or "").strip()[:40]
        ref = whitelist.get((title, article))
        if not ref:
            continue  # 不在检索白名单内的依据直接丢弃
        laws.append({
            "title": ref["source"],
            "article": ref["article"],
            "reason": str(item.get("reason") or "").strip()[:160],
            "excerpt": (ref["excerpt"] or "")[:160],
            "validity": ref.get("validity"),
        })
    steps = []
    for item in data.get("action_steps") or []:
        if isinstance(item, dict):
            step = str(item.get("step") or "").strip()[:60]
            detail = str(item.get("detail") or "").strip()[:300]
            if step or detail:
                steps.append({"step": step, "detail": detail})
        elif str(item).strip():
            steps.append({"step": str(item).strip()[:60], "detail": ""})
    result = {
        "issue_type": str(data.get("issue_type") or "待归类").strip()[:40],
        "fact_summary": str(data.get("fact_summary") or "").strip()[:300],
        "applicable_laws": laws[:8],
        "missing_info": _clamp_list(data.get("missing_info"), 6, 120),
        "evidence_checklist": _clamp_list(data.get("evidence_checklist"), 10, 120),
        "action_steps": steps[:8],
        "risks": _clamp_list(data.get("risks"), 6, 120),
        "disclaimer": str(data.get("disclaimer") or "").strip()[:120],
    }
    return result


def match_suggested_cases(*texts) -> list[dict]:
    """按关键词把事项文本映射到内置模拟庭审案例（最多 2 个）。"""
    haystack = " ".join(str(text or "") for text in texts)
    matched = []
    for case_id, keywords in CASE_MATCH_KEYWORDS.items():
        case = court_cases.CASES.get(case_id)
        if case and any(keyword in haystack for keyword in keywords):
            matched.append({"case_id": case_id, "title": case["title"],
                            "theme": case["theme"]})
        if len(matched) >= 2:
            break
    return matched


# ---- 问答 → 工作台：把多轮对话蒸馏成采集表单（用户确认后再生成报告） ----
INTAKE_DISTILL_PROMPT = """你是法律助手「法晓」的问题工作台助手。把下面这段法律咨询对话，蒸馏成工作台的案情采集表单。

严格规则：
1. 只输出一个 JSON 对象，不要 markdown 代码块，不要 JSON 以外的文字。
2. 所有字段从对话内容中提取，对话没有的信息留空字符串，不要推测或编造。
3. "what" 用时间线客观转述事实（≤600 字）；"goal" 提炼用户的真实诉求（≤200 字）。
4. 去除人名、手机号、身份证号、具体住址等可识别个人的信息，用「某」「对方」等指代。

JSON 结构：
{
  "what": "发生了什么",
  "when_where": "时间与地点",
  "parties": "当事人关系",
  "amount": "涉及金额",
  "evidence": "已有证据",
  "goal": "你的诉求",
  "urgent": "期限或紧急风险"
}"""


def build_distill_messages(messages: list[dict]) -> list[dict]:
    """messages 为 [{role, content}]，按时间顺序，最多取最近 12 条。"""
    dialogue = "\n".join(
        f"{'用户' if item.get('role') == 'user' else '法晓'}：{str(item.get('content') or '')[:800]}"
        for item in messages[-12:])
    return [{"role": "system", "content": INTAKE_DISTILL_PROMPT},
            {"role": "user", "content": f"咨询对话：\n{dialogue}"}]


def parse_distilled_intake(text: str) -> dict:
    """解析蒸馏输出并裁剪到采集字段的长度限制；解析失败抛 ValueError。"""
    raw = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fenced:
        raw = fenced.group(1)
    else:
        start, end = raw.find("{"), raw.rfind("}")
        if start >= 0 and end > start:
            raw = raw[start:end + 1]
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("蒸馏结果不是对象")
    intake = {}
    for key, (_label, _required, limit) in INTAKE_FIELDS.items():
        value = data.get(key)
        intake[key] = str(value).strip()[:limit] if isinstance(value, str) else ""
    return intake
