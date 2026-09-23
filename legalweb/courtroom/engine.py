"""庭审引擎：程序状态机（导演）+ Agent 回合调度。

程序正义部分（开庭、阶段转换、举证顺序、检查点、休庭评议）由纯代码状态机
保证，不经模型生成；实质对抗部分（举证质证、辩论、陈述、陪审员发问的作答）
由各 Agent 生成。庭审在「法庭调查后」「法庭辩论后」两次暂停（checkpoint），
等待人民陪审员（用户）提交阶段性预判与发问，再由 /continue 接口续跑——
参与感由状态机的检查点保证，而不是提示词措辞。
"""

import re
import time

from ..llm import LLMError
from .agents import JUDGE_BADGE, ROLES

# 庭审推进的阶段顺序；mid_review / poll2 是等待陪审员输入的检查点
STAGE_ORDER = ["opening", "investigation", "mid_review", "debate", "poll2",
               "statement", "deliberation"]


def judge_line(text: str) -> dict:
    return {"type": "line", "role": "judge", "name": "审判长", "badge": JUDGE_BADGE, "text": text}


class TrialEngine:
    def __init__(self, case: dict, llm):
        self.case = case
        self.llm = llm
        self.transcript = []      # [{"role","name","text"}]
        self.summaries = []       # 书记员阶段纪要
        self.summarized_upto = 0
        self.polls = []           # 陪审员阶段性预判 [{"stage","option_id","label"}]
        self.questions = []       # 陪审员庭审发问 [{"target","question"}]

    # ------------------------------------------------------------------
    # 状态存取（检查点续跑：整场庭审可序列化到 court_sessions.state）
    # ------------------------------------------------------------------
    def export_state(self) -> dict:
        return {"summaries": self.summaries, "summarized_upto": self.summarized_upto,
                "polls": self.polls, "questions": self.questions}

    def load_state(self, state: dict):
        state = state or {}
        self.summaries = list(state.get("summaries") or [])
        self.summarized_upto = int(state.get("summarized_upto") or 0)
        self.polls = list(state.get("polls") or [])
        self.questions = list(state.get("questions") or [])

    # ------------------------------------------------------------------
    # 上下文组装
    # ------------------------------------------------------------------
    def dossier_text(self) -> str:
        return "\n".join(f"{e['id']} {e['type']}：{e['content']}" for e in self.case["dossier"])

    def laws_text(self) -> str:
        return "\n\n".join(f"{name}：{text}" for name, text in self.case["laws"].items())

    def recent_text(self, limit: int = 6) -> str:
        if not self.transcript:
            return "（庭审刚开始）"
        lines = [f"{item['name']}：{item['text']}" for item in self.transcript[-limit:]]
        if self.summaries:
            lines.insert(0, "前序阶段纪要：" + "；".join(self.summaries))
        return "\n".join(lines)

    def agent_messages(self, role_key: str, task: str, with_laws: bool = False) -> list[dict]:
        role = ROLES[role_key]
        parts = [f"【案情摘要（起诉指控）】\n{self.case['facts']}",
                 f"【卷宗】\n{self.dossier_text()}"]
        if with_laws:
            parts.append(f"【相关法条（仅可引用这些原文）】\n{self.laws_text()}")
        parts.append(f"【庭审记录】\n{self.recent_text()}")
        parts.append(f"【你的任务】\n{task}")
        return [{"role": "system", "content": role["system"]},
                {"role": "user", "content": "\n\n".join(parts)}]

    # ------------------------------------------------------------------
    # Agent 回合
    # ------------------------------------------------------------------
    def turn(self, role_key: str, task: str, with_laws: bool = False):
        """一个 Agent 回合：yield turn_start / delta* / turn_end，文本落入庭审记录。

        使用非流式调用（更稳定，推理模型的 think 标签长度不可控），
        然后逐段模拟流式输出，保留打字机效果。失败时间隔重试，最多三次。"""
        role = ROLES[role_key]
        messages = self.agent_messages(role_key, task, with_laws)
        yield {"type": "turn_start", "role": role_key, "name": role["name"], "badge": role["badge"]}
        text = ""
        last_error = None
        for attempt in range(3):
            try:
                # 8000 预算：MiniMax-M3 的 think 与回答共享额度，过小会导致可见输出为空
                text = self.llm.complete(messages, temperature=role["temperature"], max_tokens=8000)
                break
            except LLMError as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(2)
                    yield {"type": "delta", "text": "（正在重新组织发言…）"}
        else:
            raise last_error
        # 逐段模拟流式输出（按标点分割，保留打字机效果）
        segments = _re_split(text)
        for seg in segments:
            if seg:
                yield {"type": "delta", "text": seg}
        self.transcript.append({"role": role_key, "name": role["name"], "text": text})
        yield {"type": "turn_end", "role": role_key}

    def clerk_note(self):
        """阶段纪要：一次非流式短调用；失败时静默跳过（不影响庭审推进）。"""
        fresh = self.transcript[self.summarized_upto:]
        if not fresh:
            return None
        content = "\n".join(f"{item['name']}：{item['text']}" for item in fresh)
        role = ROLES["clerk"]
        messages = [{"role": "system", "content": role["system"]},
                    {"role": "user", "content": f"【需要纪要的庭审内容】\n{content}"}]
        try:
            note = self.llm.complete(messages, temperature=role["temperature"], max_tokens=500)
        except LLMError:
            note = ""
        self.summarized_upto = len(self.transcript)
        if note:
            self.summaries.append(note)
            return {"type": "summary", "text": note}
        return None

    # ------------------------------------------------------------------
    # 阶段片段（原 run() 拆分，供检查点续跑）
    # ------------------------------------------------------------------
    def stage_opening(self):
        yield {"type": "phase", "phase": "opening", "title": "开庭"}
        yield judge_line("现在开庭。海城市人民法院刑事审判庭，今天依法公开开庭审理海城市人民检察院"
                         "提起公诉的被告人陈明涉嫌故意伤害罪一案。传被告人陈明到庭。")
        yield judge_line("被告人陈明，起诉书指控你犯故意伤害罪，你对起诉书指控的犯罪事实有无异议？")
        yield from self.turn("defendant",
                             "审判长问你对起诉书指控的犯罪事实有无异议。请当庭回答，"
                             "并按讯问笔录（E1）里你陈述过的想法说明理由。")

    def stage_investigation(self):
        case = self.case
        yield {"type": "phase", "phase": "investigation", "title": "法庭调查"}
        yield judge_line("现在进行法庭调查。请公诉人宣读起诉书指控的事实与罪名要点。")
        yield from self.turn("prosecutor",
                             "当庭宣读指控要点（不超过150字）：核心事实经过、死亡原因、指控罪名，"
                             "并点出本案关键争点——追刺两刀时不法侵害是否已经结束。")
        yield judge_line("被告人，你现在就起诉书指控的事实向法庭陈述。")
        yield from self.turn("defendant",
                             "用你自己的话向法庭陈述事情经过，与讯问笔录（E1）保持一致，不超过100字。")
        yield judge_line("下面进行举证质证。请公诉人逐项举证，辩护人逐项质证。")
        for eid in case["key_evidence"]:
            item = next(e for e in case["dossier"] if e["id"] == eid)
            yield from self.turn("prosecutor",
                                 f"就证据 {eid}（{item['type']}）发表举证意见：该证据证明了什么、"
                                 f"对指控的意义。不超过140字。")
            yield from self.turn("defender",
                                 f"就公诉人对 {eid}（{item['type']}）的举证发表质证意见：可就真实性、"
                                 f"关联性发表意见，或指出其中对被告人有利的细节。不超过140字。")
        note = self.clerk_note()
        if note:
            yield note

    def stage_debate(self):
        yield {"type": "phase", "phase": "debate", "title": "法庭辩论"}
        yield judge_line("法庭调查结束，现在进行法庭辩论。先由公诉人发表公诉意见。")
        yield from self.turn("prosecutor",
                             "发表公诉意见：围绕“追刺时不法侵害是否已经结束”“夺刀后的连续捅刺是否"
                             "明显超过必要限度”论证故意伤害罪的成立。可引用【相关法条】原文。不超过160字。",
                             with_laws=True)
        yield from self.turn("defender",
                             "发表辩护意见：论证刘海的行为属于“行凶”、整体不法侵害尚未结束、"
                             "陈明构成特殊防卫。可引用【相关法条】原文。不超过160字。",
                             with_laws=True)
        yield from self.turn("prosecutor",
                             "针对辩护人刚才的意见进行第二轮答辩。不超过140字。", with_laws=True)
        yield from self.turn("defender",
                             "针对公诉人刚才的答辩进行第二轮答辩，收束你的辩护观点。不超过140字。",
                             with_laws=True)
        note = self.clerk_note()
        if note:
            yield note

    def stage_statement(self):
        yield {"type": "phase", "phase": "statement", "title": "最后陈述"}
        yield judge_line("法庭辩论结束。被告人陈明，你可以作最后陈述。")
        yield from self.turn("defendant",
                             "作最后陈述：你对行为性质的态度、对被害人死亡结果的心情，"
                             "以及你对法庭的请求。不超过120字，语气恳切。")

    # ------------------------------------------------------------------
    # 全庭流程（可从任意阶段续跑；检查点处暂停并返回）
    # ------------------------------------------------------------------
    def run(self, from_stage: str = "opening"):
        stages = {"opening": self.stage_opening,
                  "investigation": self.stage_investigation,
                  "debate": self.stage_debate,
                  "statement": self.stage_statement}
        started = False
        for name in STAGE_ORDER:
            if name == from_stage:
                started = True
            if not started:
                continue
            if name in stages:
                yield from stages[name]()
            elif name == "mid_review":
                yield judge_line("法庭调查结束。在进入法庭辩论之前，请人民陪审员记录阶段性意见，"
                                 "并可就卷宗证据向控辩双方发问。")
                yield {"type": "checkpoint", "kind": "mid_review",
                       "title": "阶段性预判与发问", "next": "debate",
                       "options": self.case["options"], "allow_question": True}
                return
            elif name == "poll2":
                yield judge_line("法庭辩论结束。请人民陪审员再次记录意见——听完控辩双方的对抗，"
                                 "你的判断是否发生了变化？")
                prev = next((p["label"] for p in reversed(self.polls)
                             if p["stage"] == "investigation"), None)
                yield {"type": "checkpoint", "kind": "poll",
                       "title": "辩论后的复核", "next": "statement",
                       "options": self.case["options"], "allow_question": False,
                       "previous_label": prev}
                return
            elif name == "deliberation":
                yield judge_line("现在休庭，合议庭评议。作为今天的人民陪审员，请你根据庭审查明的事实"
                                 "与法律，独立作出评议。")
                yield {"type": "await_deliberation", "options": self.case["options"]}
                return

    # ------------------------------------------------------------------
    # 检查点处理：记录陪审员预判；有发问则由被问方当庭作答
    # ------------------------------------------------------------------
    def apply_checkpoint(self, kind: str, data: dict):
        if kind == "mid_review":
            label = self._record_poll("investigation", data)
            yield judge_line(f"书记员记录：人民陪审员在法庭调查后的阶段意见为——{label}。")
            question = (data.get("question") or "").strip()
            target = (data.get("target") or "").strip()
            if question and target in ROLES and target != "clerk":
                role = ROLES[target]
                self.questions.append({"target": target, "question": question[:200]})
                yield judge_line(f"人民陪审员向{role['name']}发问：“{question}”请当庭作答。")
                task = (f"人民陪审员在法庭调查结束后向你发问：“{question}”\n"
                        f"请当庭正面作答：只能依据卷宗证据（写编号）与庭审记录，"
                        f"并结合你的诉讼立场，不超过140字。")
                yield from self.turn(target, task)
                yield judge_line("陪审员的发问与回答已记录在案。")
        elif kind == "poll":
            label = self._record_poll("debate", data)
            prev = next((p for p in self.polls if p["stage"] == "investigation"), None)
            if prev and prev["label"] != label:
                yield judge_line(f"书记员记录：人民陪审员的意见由“{prev['label']}”调整为"
                                 f"“{label}”，合议庭评议时将重点关注这一变化。")
            else:
                yield judge_line(f"书记员记录：人民陪审员在法庭辩论后的阶段意见为——{label}。")

    def _record_poll(self, stage: str, data: dict) -> str:
        option_id = (data.get("poll") or "").strip()
        option = next((o for o in self.case["options"] if o["id"] == option_id), None)
        if not option:
            raise ValueError("无效的预判选项")
        # 幂等：同一阶段的预判只保留最新一次（续跑重试不产生重复记录）
        self.polls = [p for p in self.polls if p["stage"] != stage]
        self.polls.append({"stage": stage, "option_id": option_id, "label": option["label"]})
        return option["label"]

    # ------------------------------------------------------------------
    # 评议复盘（宣判对照）
    # ------------------------------------------------------------------
    def verdict_events(self, deliberation: dict):
        case = self.case
        verdict = case["real_verdict"]
        trajectory = [{"stage": p["stage"], "label": p["label"]} for p in self.polls]
        yield {"type": "verdict", "real": verdict, "yours": deliberation,
               "trajectory": trajectory}
        option = next(o for o in case["options"] if o["id"] == deliberation["option_id"])
        jury_text = deliberation["label"]
        if deliberation.get("convict"):
            jury_text += f"；量刑意见：{deliberation['years']} 年"
        marks = deliberation.get("marks") or []
        if marks:
            jury_text += "；标注关键证据：" + "、".join(marks)
        parts = [f"【案情摘要（起诉指控）】\n{case['facts']}",
                 f"【卷宗】\n{self.dossier_text()}",
                 f"【庭审记录（最近）】\n{self.recent_text(limit=8)}",
                 f"【法院裁判结论】\n{verdict['summary']}\n裁判理由：{verdict['reasoning']}",
                 f"【相关法条（仅可引用这些原文）】\n{self.laws_text()}"]
        trail = []
        for p in self.polls:
            when = "法庭调查后" if p["stage"] == "investigation" else "法庭辩论后"
            trail.append(f"{when}预判：{p['label']}")
        for q in self.questions:
            trail.append(f"庭审中向{ROLES[q['target']]['name']}发问：“{q['question']}”")
        if trail:
            parts.append("【人民陪审员庭审轨迹】\n" + "\n".join(trail))
        parts.append(f"【人民陪审员评议】\n{jury_text}")
        role = ROLES["judge_review"]
        messages = [{"role": "system", "content": role["system"]},
                    {"role": "user", "content": "\n\n".join(parts)}]
        yield {"type": "turn_start", "role": "judge_review", "name": "审判长", "badge": JUDGE_BADGE}
        analysis = self.llm.complete(messages, temperature=role["temperature"], max_tokens=8000)
        # 逐段模拟流式输出
        for seg in _re_split(analysis):
            if seg:
                yield {"type": "delta", "text": seg}
        yield {"type": "turn_end", "role": "judge_review"}
        yield {"type": "result", "analysis": analysis}


def _re_split(text: str) -> list[str]:
    """按中文标点切分长文本，模拟流式输出的节奏。"""
    return [seg for seg in re.split(r"(?<=[，。！？；：、\n])", text) if seg]
