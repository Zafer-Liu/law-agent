"""法律问答：RAG 证据组装、系统提示与引用校验。"""

import json
import re

from .rag import lexical_score

SYSTEM_PROMPT = """你是一名严谨的中文法律咨询助手「法晓」，面向普通公众提供普法咨询。

回答规则：
1. 只依据本次提供的证据片段（法律法规节选，编号 C1、C2…）回答问题；引用时在相应句末用 [C1] 形式标注，可一句多引。
2. 证据片段没有覆盖的问题，明确说明"当前知识库未收录相关依据"，不要编造法条、条文号或案例。
3. 结构：先用一两句话直接回答，再分点展开解释（是什么/怎么认定/后果），最后给一句实务建议。
4. 涉及具体纠纷时提醒：本回答是一般性法律信息，不构成法律意见，重要事项请咨询执业律师。
5. 简体中文，通俗准确，避免堆砌术语；条文原文只在必要时引用关键句。
6. 使用 Markdown 组织回答：可使用二、三级标题、列表、加粗和代码格式；不要输出 HTML。"""

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
    is_law = source.startswith("法条 ·") or bool(ARTICLE_IN_SECTION_RE.search(section))
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
            for fragment_rank, fragment in enumerate(_article_fragments(hit)):
                article = fragment["article"]
                excerpt = fragment["excerpt"]
                candidates.append({
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
