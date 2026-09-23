"""法律问答：RAG 证据组装、系统提示与引用校验。"""

import json
import re

SYSTEM_PROMPT = """你是一名严谨的中文法律咨询助手「法晓」，面向普通公众提供普法咨询。

回答规则：
1. 只依据本次提供的证据片段（法律法规节选，编号 C1、C2…）回答问题；引用时在相应句末用 [C1] 形式标注，可一句多引。
2. 证据片段没有覆盖的问题，明确说明"当前知识库未收录相关依据"，不要编造法条、条文号或案例。
3. 结构：先用一两句话直接回答，再分点展开解释（是什么/怎么认定/后果），最后给一句实务建议。
4. 涉及具体纠纷时提醒：本回答是一般性法律信息，不构成法律意见，重要事项请咨询执业律师。
5. 简体中文，通俗准确，避免堆砌术语；条文原文只在必要时引用关键句。
6. 使用 Markdown 组织回答：可使用二、三级标题、列表、加粗和代码格式；不要输出 HTML。"""

CITATION_RE = re.compile(r"\[(C\d+)\]")


class Evidence:
    """检索命中 → 证据引用（C1..Cn）。"""

    def __init__(self, hits):
        self.refs = [{"label": f"C{i}", "chunk_id": hit["id"], "section": hit["section"],
                      "ordinal": hit["ordinal"], "excerpt": hit["text"]}
                     for i, hit in enumerate(hits, 1)]

    @property
    def valid_labels(self):
        return {ref["label"] for ref in self.refs}


def build_messages(question, evidence: Evidence, previous_questions, recent_answers):
    context = {
        "question": question,
        "evidence": [{"label": ref["label"], "section": ref["section"],
                      "excerpt": ref["excerpt"]} for ref in evidence.refs],
    }
    # 历史仅用于消解指代（"上面说的""它"），不作为新证据。
    if previous_questions:
        context["previous_questions_for_resolving_pronouns_only"] = previous_questions[-3:]
    if recent_answers:
        context["recent_answer_summaries_for_continuity_only"] = [a[:400] for a in recent_answers[-2:]]
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)}]


def sanitize_citations(text: str, valid_labels: set) -> dict:
    """移除不在证据白名单里的引用标记，返回正文、实际使用的标签与清理数。"""
    removed = 0

    def repl(match):
        nonlocal removed
        if match.group(1) in valid_labels:
            return match.group(0)
        removed += 1
        return ""

    clean = CITATION_RE.sub(repl, text).strip()
    used = set(CITATION_RE.findall(clean))
    return {"content": clean, "used": sorted(used, key=lambda x: int(x[1:])), "removed": removed}
