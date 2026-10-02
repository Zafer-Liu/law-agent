"""庭审引擎：程序状态机（导演）+ Agent 回合调度。

程序正义部分（开庭、阶段转换、举证顺序、检查点、休庭评议）由纯代码状态机
保证，不经模型生成；实质对抗部分（举证质证、辩论、陈述、陪审员发问的作答）
由各 Agent 生成。庭审在「法庭调查后」「法庭辩论后」两次暂停（checkpoint），
等待用户提交陪审员预判或所选诉讼角色发言，再由 /continue 接口续跑——
参与感由状态机的检查点保证，而不是提示词措辞。

所有随案件变化的内容（角色人设、法官台词、检查点文案、角色发言选项）都
存放在案件 JSON 的 trial 块中，引擎本身不绑定任何具体案件。
"""

import re
import time

from ..llm import LLMError
from .agents import JUDGE_BADGE, build_roles

# 庭审推进的阶段顺序；mid_review / poll2 是等待陪审员输入的检查点
STAGE_ORDER = ["opening", "investigation", "mid_review", "debate", "poll2",
               "statement", "deliberation"]

PLAYER_ROLES = {
    "audience": {"name": "旁听观众", "badge": "观"},
    "juror": {"name": "人民陪审员", "badge": "陪"},
    "prosecutor": {"name": "公诉人", "badge": "诉"},
    "defender": {"name": "辩护人", "badge": "辩"},
    "defendant": {"name": "被告人", "badge": "被"},
}


def judge_line(text: str) -> dict:
    return {"type": "line", "role": "judge", "name": "审判长", "badge": JUDGE_BADGE, "text": text}


class TrialEngine:
    def __init__(self, case: dict, llm, player_role: str = "juror"):
        self.case = case
        self.trial = case["trial"]
        self.roles = build_roles(case)
        self.llm = llm
        self.player_role = player_role if player_role in PLAYER_ROLES else "juror"
        self.transcript = []      # [{"role","name","text"}]
        self.summaries = []       # 书记员阶段纪要
        self.summarized_upto = 0
        self.polls = []           # 陪审员阶段性预判 [{"stage","option_id","label"}]
        self.questions = []       # 陪审员庭审发问 [{"target","question"}]
        self.contributions = []   # 用户以诉讼角色提交的关键发言

    # ------------------------------------------------------------------
    # 状态存取（检查点续跑：整场庭审可序列化到 court_sessions.state）
    # ------------------------------------------------------------------
    def export_state(self) -> dict:
        return {"summaries": self.summaries, "summarized_upto": self.summarized_upto,
                "polls": self.polls, "questions": self.questions,
                "player_role": self.player_role, "contributions": self.contributions}

    def load_state(self, state: dict):
        state = state or {}
        self.summaries = list(state.get("summaries") or [])
        self.summarized_upto = int(state.get("summarized_upto") or 0)
        self.polls = list(state.get("polls") or [])
        self.questions = list(state.get("questions") or [])
        role = state.get("player_role")
        if role in PLAYER_ROLES:
            self.player_role = role
        self.contributions = list(state.get("contributions") or [])

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
        role = self.roles[role_key]
        parts = [f"【案情摘要（起诉指控）】\n{self.case['facts']}",
                 f"【争议焦点】\n" + "\n".join(f"{i}. {d}" for i, d in enumerate(self.case["disputes"], 1)),
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
        role = self.roles[role_key]
        messages = self.agent_messages(role_key, task, with_laws)
        yield {"type": "turn_start", "role": role_key, "name": role["name"], "badge": role["badge"]}
        text = ""
        last_error = None
        for attempt in range(3):
            try:
                # M2.x 的隐藏推理与可见回答共享预算，过小会导致正文为空。
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
        role = self.roles["clerk"]
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
        yield judge_line(self.trial["lines"]["opening"])
        yield judge_line(self.trial["lines"]["ask_defendant"])
        yield from self.turn("defendant",
                             "审判长问你对起诉书指控的犯罪事实有无异议。请当庭回答，"
                             "并按卷宗中你向侦查机关所作的供述或陈述简短说明理由，不超过60字。")

    def stage_investigation(self):
        case = self.case
        yield {"type": "phase", "phase": "investigation", "title": "法庭调查"}
        yield judge_line("现在进行法庭调查。请公诉人宣读起诉书指控的事实与罪名要点。")
        yield from self.turn("prosecutor",
                             "用不超过80字宣读指控要点：发生了什么、指控什么罪、双方争议在哪里。"
                             "不要照念起诉书。")
        yield judge_line("被告人，你现在就起诉书指控的事实向法庭陈述。")
        yield from self.turn("defendant",
                             "用普通人的话说清事情经过，与卷宗中你的陈述一致，不超过65字。")
        yield judge_line("下面进行举证质证。请公诉人集中说明关键证据，辩护人随后回应。")
        evidence_names = "、".join(
            f"{item['type']}（{item['id']}）"
            for item in case["dossier"] if item["id"] in case["key_evidence"]
        )
        yield from self.turn("prosecutor",
                             f"集中说明这组关键证据：{evidence_names}。只挑两项最重要的，"
                             "用不超过90字说明它们证明什么，不要逐项宣读。")
        yield from self.turn("defender",
                             "回应公诉人刚才的举证。只指出一个最关键的证明局限或对被告人有利的细节，"
                             "不超过90字，不要重复证据全文。")
        note = self.clerk_note()
        if note:
            yield note

    def stage_debate(self):
        hints = self.trial["debate_hints"]
        yield {"type": "phase", "phase": "debate", "title": "法庭辩论"}
        yield judge_line("法庭调查结束，现在进行法庭辩论。先由公诉人发表公诉意见。")
        yield from self.turn("prosecutor",
                             f"使用规范的公诉语言发表意见：{hints['prosecutor']}。"
                             "围绕一个核心理由作出法律评价，不背诵法条，不超过110字。",
                             with_laws=True)
        yield from self.turn("defender",
                             f"使用专业的辩护语言回应公诉意见：{hints['defender']}。"
                             "围绕一个核心理由提出辩护主张，不背诵法条，不超过110字。",
                             with_laws=True)
        yield from self.turn("prosecutor",
                             "只回应辩护人刚才最关键的一句话，不重复前文，不超过60字。", with_laws=True)
        yield from self.turn("defender",
                             "只回应公诉人刚才最关键的一句话，并用一句话收束辩护，不超过60字。",
                             with_laws=True)
        note = self.clerk_note()
        if note:
            yield note

    def stage_statement(self):
        yield {"type": "phase", "phase": "statement", "title": "最后陈述"}
        yield judge_line(self.trial["lines"]["statement_invite"])
        if self.player_role == "defendant":
            yield judge_line("被告人刚才提交的最后陈述已记录在案。")
            return
        yield from self.turn("defendant",
                             "像普通人一样作最后陈述：说清你的心情和对法庭的请求，不使用法律术语，"
                             "不超过65字。")

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
                if self.player_role == "audience":
                    continue
                if self.player_role != "juror":
                    yield from self._role_checkpoint("investigation", "debate")
                    return
                yield judge_line("法庭调查结束。在进入法庭辩论之前，请人民陪审员记录阶段性意见，"
                                 "并可就卷宗证据向控辩双方发问。")
                yield {"type": "checkpoint", "kind": "mid_review",
                       "title": "阶段性预判与发问", "next": "debate",
                       "options": self.case["options"], "allow_question": True}
                return
            elif name == "poll2":
                if self.player_role == "audience":
                    continue
                if self.player_role != "juror":
                    yield from self._role_checkpoint("debate", "statement")
                    return
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
                if self.player_role == "juror":
                    text = ("现在休庭，合议庭评议。作为今天的人民陪审员，请你根据庭审查明的事实"
                            "与法律，独立作出评议。")
                elif self.player_role == "audience":
                    text = ("庭审已经结束。作为旁听观众，你可以根据刚才听到的事实与法律，"
                            "作出你的裁判预测。")
                else:
                    text = ("你的角色任务已经完成。现在请暂时跳出诉讼立场，根据庭审查明的事实与法律，"
                            "作出最终裁判预测。")
                yield judge_line(text)
                yield {"type": "await_deliberation", "options": self.case["options"],
                       "player_role": self.player_role}
                return

    def _role_checkpoint(self, stage: str, next_stage: str):
        role = PLAYER_ROLES[self.player_role]
        title, prompt, placeholder = self.trial["checkpoints"][self.player_role][stage]
        options = self.trial["statement_options"][self.player_role][stage]
        yield judge_line(f"现在请{role['name']}提交“{title}”，发言将直接记入庭审记录。")
        yield {"type": "checkpoint", "kind": "role_input", "stage": stage,
               "role": self.player_role, "role_name": role["name"], "badge": role["badge"],
               "title": title, "prompt": prompt, "placeholder": placeholder,
               "options": options,
               "next": next_stage}

    # ------------------------------------------------------------------
    # 检查点处理：记录陪审员预判；有发问则由被问方当庭作答
    # ------------------------------------------------------------------
    def apply_checkpoint(self, kind: str, data: dict):
        if kind == "mid_review":
            label = self._record_poll("investigation", data)
            yield judge_line(f"书记员记录：人民陪审员在法庭调查后的阶段意见为——{label}。")
            question = (data.get("question") or "").strip()
            target = (data.get("target") or "").strip()
            if question and target in self.roles and target != "clerk":
                role = self.roles[target]
                self.questions.append({"target": target, "question": question[:200]})
                yield judge_line(f"人民陪审员向{role['name']}发问：“{question}”请当庭作答。")
                task = (f"人民陪审员在法庭调查结束后向你发问：“{question}”\n"
                        f"请当庭正面作答：只说结论和一个理由，使用证据名称并保留内部编号，"
                        f"不超过70字。")
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
        elif kind == "role_input":
            stage = (data.get("stage") or "").strip()
            statement = (data.get("statement") or "").strip()
            if self.player_role == "juror" or stage not in ("investigation", "debate"):
                raise ValueError("无效的角色发言环节")
            if not 10 <= len(statement) <= 500:
                raise ValueError("角色发言需在 10-500 字之间")
            role = PLAYER_ROLES[self.player_role]
            self.contributions = [item for item in self.contributions if item.get("stage") != stage]
            self.contributions.append({"stage": stage, "role": self.player_role,
                                       "name": role["name"], "text": statement})
            self.transcript.append({"role": self.player_role, "name": role["name"],
                                    "text": statement, "player": True})
            yield {"type": "line", "role": self.player_role, "name": role["name"],
                   "badge": role["badge"], "text": statement, "player": True}
            yield judge_line(f"{role['name']}的发言已记录在案，庭审继续。")

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
               "trajectory": trajectory, "player_role": self.player_role,
               "contributions": self.contributions}
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
            trail.append(f"庭审中向{self.roles[q['target']]['name']}发问：“{q['question']}”")
        for item in self.contributions:
            when = "法庭调查后" if item["stage"] == "investigation" else "法庭辩论后"
            trail.append(f"{when}以{item['name']}身份发言：“{item['text']}”")
        if trail:
            parts.append("【用户庭审参与轨迹】\n" + "\n".join(trail))
        final_label = "人民陪审员评议" if self.player_role == "juror" else "用户最终裁判预测"
        parts.append(f"【{final_label}】\n{jury_text}")
        role = self.roles["judge_review"]
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
