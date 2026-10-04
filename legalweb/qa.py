"""法律问答：RAG 证据组装、系统提示与引用校验。"""

import json
import re

from .rag import lexical_score

SYSTEM_PROMPT = """你是一名严谨的中文法律咨询助手「法晓」，面向普通公众提供普法咨询。用户大多不懂法律，你的首要目标是让每次提问都得到有用的回答，禁止以"知识库未收录"为由拒绝回答或只做免责声明。

回答规则：
1. 有证据片段（法律法规节选，编号 C1、C2…）时，核心法律结论必须依据证据作答，引用在相应句末用 [C1] 形式标注，可一句多引。
2. 证据没有覆盖的部分（如实务操作、取证方法、流程步骤），继续基于通用的法律常识和实践经验回答，并在该部分开头标注"**以下为一般法律实务提示（未附具体条文依据）**"。禁止在这类内容里编造具体条文号、司法解释名称或虚构案例；不确定的内容要如实说明"各地实践可能有差异"。
3. 完全没有证据片段时，直接依据你掌握的法律知识回答，在回答开头用一句话说明"本次未检索到可直接引用的条文，以下基于一般法律知识作答（不构成法律意见）"，然后照常完整回答。
4. 结构：先用一两句话直接回答，再分点展开解释（是什么/怎么认定/怎么办），最后给可操作的实务建议（步骤化，普通人能照做）。
5. 信息不足无法给出针对性建议时，先给出一般性回答，再追问 1-2 个关键信息（如合同类型、所处阶段），不要只反问不作答。
6. 涉及具体纠纷时提醒：本回答是一般性法律信息，不构成法律意见，重要事项请咨询执业律师。
7. 简体中文，通俗准确，避免堆砌术语；条文原文只在必要时引用关键句。
8. 使用 Markdown 组织回答：可使用二、三级标题、列表、加粗和代码格式；不要输出 HTML。"""

CITATION_RE = re.compile(r"\[(C\d+)\]")
GROUPED_CITATION_RE = re.compile(r"[\[【]((?:C\d+\s*[,，、;；]\s*)+C\d+)[\]】]")
FULLWIDTH_CITATION_RE = re.compile(r"【(C\d+)】")
ARTICLE_RE = re.compile(
    r"(?m)^(?:#{1,6}\s*)?(第[一二三四五六七八九十百千万零〇两\d]+条"
    r"(?:之[一二三四五六七八九十百千万零〇两\d]+)?)\s*"
)
ARTICLE_IN_SECTION_RE = re.compile(
    r"(第[一二三四五六七八九十百千万零〇两\d]+条"
    r"(?:之[一二三四五六七八九十百千万零〇两\d]+)?)"
)
SOURCE_META_RE = re.compile(r"\s*\((?:FBM[-\w.]+)(?:\s*\(\d+\))?\)\s*$", re.I)
MAX_EVIDENCE_REFS = 16


def _display_source(source: str, section: str) -> str:
    """Turn internal corpus labels into a short title suitable for readers."""
    source = re.sub(r"^(?:法条|判例)\s*·\s*", "", (source or "").strip())
    source = SOURCE_META_RE.sub("", source).strip()
    if source and not re.fullmatch(r"[a-z0-9_-]+", source, re.I):
        return source
    first_section = (section or "").split(" / ", 1)[0].strip()
    return first_section if first_section and first_section != "正文" else (source or "法律资料")


def _article_fragments(hit: dict) -> list[dict]:
    """Split a retrieved law chunk so one citation maps to one provision."""
    text = (hit.get("text") or "").strip()
    source = hit.get("source_title", "")
    section = hit.get("section", "正文")
    is_law = (source.startswith(("法条 ·", "北大法宝 ·"))
              or bool(ARTICLE_IN_SECTION_RE.search(section)))
    matches = list(ARTICLE_RE.finditer(text)) if is_law else []
    if not matches:
        section_article = ARTICLE_IN_SECTION_RE.search(section)
        return [{"article": section_article.group(1) if section_article else "",
                 "excerpt": text}]

    fragments = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        excerpt = text[match.end():end].strip()
        if excerpt:
            fragments.append({"article": match.group(1), "excerpt": excerpt})
    return fragments or [{"article": "", "excerpt": text}]


class Evidence:
    """检索命中 → 证据引用（C1..Cn）。"""

    def __init__(self, hits, question=""):
        candidates = []
        for hit_rank, hit in enumerate(hits):
            section = hit.get("section", "正文")
            source = _display_source(hit.get("source_title", ""), section)
            # 在线检索等来源附带的展示字段（url、timeliness…）原样透传到引用
            passthrough = {key: value for key, value in hit.items()
                           if key not in ("text", "source_title", "section")}
            for fragment_rank, fragment in enumerate(_article_fragments(hit)):
                article = fragment["article"]
                excerpt = fragment["excerpt"]
                candidates.append({
                    **passthrough,
                    "chunk_id": hit["id"],
                    "source": source,
                    "article": article,
                    "section": " / ".join(part for part in (source, article) if part),
                    "ordinal": hit["ordinal"],
                    "excerpt": excerpt,
                    "_score": lexical_score(question, f"{article} {excerpt}") if question else 0,
                    "_order": (hit_rank, fragment_rank),
                })

        # Overlapping chunks often repeat the same provision. Keep the strongest,
        # longest copy, then cap the prompt so the model sees a concise evidence list.
        unique = {}
        for item in candidates:
            key = ((item["source"], item["article"])
                   if item["article"] else
                   (item["source"], item["section"], item["excerpt"][:160]))
            current = unique.get(key)
            if current is None or (item["_score"], len(item["excerpt"])) > (
                    current["_score"], len(current["excerpt"])):
                unique[key] = item
        candidates = sorted(unique.values(), key=lambda item: (
            -item["_score"], item["_order"][0], item["_order"][1]
        ))
        # Preserve source diversity: the best provision from every retrieved hit
        # is kept before remaining high-scoring provisions fill the evidence list.
        selected, selected_ids, covered_hits = [], set(), set()
        for item in candidates:
            hit_rank = item["_order"][0]
            if hit_rank not in covered_hits:
                selected.append(item)
                selected_ids.add(id(item))
                covered_hits.add(hit_rank)
        for item in candidates:
            if len(selected) >= MAX_EVIDENCE_REFS:
                break
            if id(item) not in selected_ids:
                selected.append(item)
        ranked = sorted(selected[:MAX_EVIDENCE_REFS], key=lambda item: (
            -item["_score"], item["_order"][0], item["_order"][1]
        ))

        self.refs = []
        for index, item in enumerate(ranked, 1):
            item.pop("_score", None)
            item.pop("_order", None)
            item["label"] = f"C{index}"
            self.refs.append(item)

    @property
    def valid_labels(self):
        return {ref["label"] for ref in self.refs}


def build_messages(question, evidence: Evidence, previous_questions, recent_answers,
                   conversation_summary=None):
    context = {
        "question": question,
        "evidence": [{"label": ref["label"], "section": ref["section"],
                      "excerpt": ref["excerpt"]} for ref in evidence.refs],
    }
    # 历史用于消解指代（"上面说的""它"）与延续话题，不作为新证据。
    if conversation_summary:
        context["earlier_conversation_summary_for_continuity_only"] = conversation_summary
    if previous_questions:
        context["previous_questions_for_resolving_pronouns_only"] = [q[:800] for q in previous_questions[-12:]]
    if recent_answers:
        context["recent_answer_summaries_for_continuity_only"] = [a[:1500] for a in recent_answers[-6:]]
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)}]


def sanitize_citations(text: str, valid_labels: set) -> dict:
    """移除不在证据白名单里的引用标记，返回正文、实际使用的标签与清理数。"""
    # 模型偶尔会把多个依据合写为 [C1, C2]，或使用中文方括号。
    # 先统一为逐个引用，避免合法依据在前端缺失。
    text = FULLWIDTH_CITATION_RE.sub(r"[\1]", text)

    def expand_group(match):
        labels = re.findall(r"C\d+", match.group(1))
        return "".join(f"[{label}]" for label in labels)

    text = GROUPED_CITATION_RE.sub(expand_group, text)
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
