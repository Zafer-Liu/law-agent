"""庭审引擎：程序状态机（导演）+ Agent 回合调度。

程序正义部分（开庭、阶段转换、举证顺序、检查点、休庭评议）由纯代码状态机
保证，不经模型生成；实质对抗部分（举证质证、辩论、陈述、陪审员发问的作答）
由各 Agent 生成。庭审在「法庭调查后」「法庭辩论后」两次暂停（checkpoint），
等待用户提交陪审员预判或所选诉讼角色发言，再由 /continue 接口续跑——
参与感由状态机的检查点保证，而不是提示词措辞。
"""

import re
import time

from ..llm import LLMError
from .agents import JUDGE_BADGE, ROLES

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

ROLE_CHECKPOINTS = {
    "prosecutor": {
        "investigation": ("补充举证意见", "选择一项关键证据，说明其证明内容和证明目的。",
                          "例如：现有监控视频能够证明，刘海转身后，陈明仍继续追击……"),
        "debate": ("发表最终公诉意见", "围绕争议焦点回应辩方观点，并作出明确法律评价。",
                   "请使用简明、规范的公诉语言发表意见……"),
    },
    "defender": {
        "investigation": ("提交质证意见", "指出一项关键证据的证明局限，并说明其对案件认定的影响。",
                          "例如：监控视频显示，从夺刀到冲突结束仅约 11 秒……"),
        "debate": ("发表最终辩护意见", "围绕争议焦点回应公诉意见，并提出明确辩护结论。",
                   "请使用简明、专业的辩护语言发表意见……"),
    },
    "defendant": {
        "investigation": ("补充事实陈述", "用自己的话说说，当时你看到了什么、害怕什么。",
                          "只说卷宗里已经记录的事情……"),
        "debate": ("作最后陈述", "说说你的心情，以及你想对法庭说的话。",
                   "请像普通人一样说几句真话，不要使用法律术语……"),
    },
}

ROLE_STATEMENT_OPTIONS = {
    "prosecutor": {
        "investigation": [
            {"id": "timeline", "label": "突出追刺时间线",
             "detail": "结合监控与尸检，说明追刺发生在被害人转身之后。",
             "statement": "监控视频（E4）显示，刘海转身跑向轿车后，陈明仍继续追击；尸检意见（E5）证实背部存在两处创口。上述证据相互印证，能够证明后续行为已超出防卫时间条件。"},
            {"id": "excess", "label": "强调反击超过限度",
             "detail": "从伤口数量和连续攻击说明行为强度。",
             "statement": "尸检意见（E5）显示，被害人共有七处刀伤，其中两处位于背部。公诉机关认为，即使前段行为具有防卫性质，后续连续追击也已明显超过制止侵害的必要限度。"},
        ],
        "debate": [
            {"id": "ending", "label": "主张侵害已经结束",
             "detail": "抓住转身离开与背部创伤，回应正当防卫观点。",
             "statement": "辩方强调过程仅有十一秒，但持续时间短不能当然证明侵害仍在继续。监控视频（E4）与尸检意见（E5）能够证明刘海转身后仍遭追击，后续行为不具备防卫的时间条件。"},
            {"id": "objective", "label": "客观评价全部行为",
             "detail": "承认先前侵害，同时区分夺刀反击与后续追刺。",
             "statement": "公诉机关认可刘海先持刀攻击，陈明最初的反击具有防卫起因。但监控视频（E4）和尸检意见（E5）同时证明此后发生追击，前后两段行为应依法分别评价。"},
        ],
    },
    "defender": {
        "investigation": [
            {"id": "continuous", "label": "强调冲突连续性",
             "detail": "以十一秒时间线说明危险没有清晰中断。",
             "statement": "监控视频（E4）显示，从刀落地到冲突结束仅约十一秒，整个过程高度连续。现有证据不足以证明不法侵害已明确、现实地终止，对此应作有利于被告人的评价。"},
            {"id": "danger", "label": "突出严重暴力危险",
             "detail": "结合持刀攻击和被告伤情说明现实紧迫性。",
             "statement": "证人证言（E2）与监控视频（E4）均证实刘海先持刀连续攻击，伤情鉴定（E6）亦印证陈明已经受伤。该行为属于针对正在进行的严重暴力侵害实施的防卫。"},
        ],
        "debate": [
            {"id": "special_defense", "label": "主张构成特殊防卫",
             "detail": "围绕“行凶”和持续危险作最终无罪辩护。",
             "statement": "刘海持砍刀连续攻击，属于严重危及人身安全的暴力侵害。监控视频（E4）显示夺刀与反击发生在连续十一秒内，侵害是否终止并不明确，陈明的行为依法构成特殊防卫。"},
            {"id": "reasonable_person", "label": "强调不能事后苛求",
             "detail": "从防卫人的现场认知回应公诉意见。",
             "statement": "判断防卫是否适时，应立足行为人当时所处情境，不能以事后视角苛求。讯问笔录（E1）与监控视频（E4）表明，陈明有合理理由认为现实危险尚未解除。"},
        ],
    },
    "defendant": {
        "investigation": [
            {"id": "fear", "label": "说明当时的恐惧",
             "detail": "依据讯问笔录陈述为何认为危险仍未解除。",
             "statement": "他拿刀一直砍我，我的手和肩都受了伤。抢到刀以后，我看见他往车边跑，怕他还要拿东西。事情发生得太快，我当时只想保住自己。"},
            {"id": "cooperate", "label": "说明事后没有逃离",
             "detail": "依据卷宗说明停止行为并等待警察。",
             "statement": "他倒下以后我就停手了，也没有逃跑，一直在现场等警察。我不是想伤害他，只是当时被刀追着砍，真的很害怕。"},
        ],
        "debate": [
            {"id": "final_remorse", "label": "表达遗憾并请求公正裁判",
             "detail": "承认死亡后果，同时坚持自己是在自保。",
             "statement": "刘海去世，我心里很难受。但当时是他先拿刀砍我，我真的以为自己会死。我希望法庭看看当时的情况，公正作出判断。"},
            {"id": "final_facts", "label": "坚持依据事实说明自保",
             "detail": "简洁重申持刀侵害、连续过程和留在现场。",
             "statement": "我没有先动手，是他拿刀砍我以后我才还手。整个过程只有十来秒，他倒下后我马上停手，也留在现场等警察。请法庭公正判断。"},
        ],
    },
}


def judge_line(text: str) -> dict:
    return {"type": "line", "role": "judge", "name": "审判长", "badge": JUDGE_BADGE, "text": text}


class TrialEngine:
    def __init__(self, case: dict, llm, player_role: str = "juror"):
        self.case = case
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
                             "并按讯问笔录（E1）里你说过的话简短说明理由，不超过60字。")

    def stage_investigation(self):
        case = self.case
        yield {"type": "phase", "phase": "investigation", "title": "法庭调查"}
        yield judge_line("现在进行法庭调查。请公诉人宣读起诉书指控的事实与罪名要点。")
        yield from self.turn("prosecutor",
                             "用不超过80字宣读指控要点：发生了什么、指控什么罪、双方争议在哪里。"
                             "不要照念起诉书。")
        yield judge_line("被告人，你现在就起诉书指控的事实向法庭陈述。")
        yield from self.turn("defendant",
                             "用普通人的话说清事情经过，与讯问笔录（E1）一致，不超过65字。")
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
        yield {"type": "phase", "phase": "debate", "title": "法庭辩论"}
        yield judge_line("法庭调查结束，现在进行法庭辩论。先由公诉人发表公诉意见。")
        yield from self.turn("prosecutor",
                             "使用规范的公诉语言发表意见：说明为什么追刺已经不是制止危险。"
                             "围绕一个核心理由作出法律评价，不背诵法条，不超过110字。",
                             with_laws=True)
        yield from self.turn("defender",
                             "使用专业的辩护语言回应公诉意见：说明为什么当时的危险尚未结束。"
                             "围绕一个核心理由提出无罪主张，不背诵法条，不超过110字。",
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
        yield judge_line("法庭辩论结束。被告人陈明，你可以作最后陈述。")
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
        title, prompt, placeholder = ROLE_CHECKPOINTS[self.player_role][stage]
        options = ROLE_STATEMENT_OPTIONS[self.player_role][stage]
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
            if question and target in ROLES and target != "clerk":
                role = ROLES[target]
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
            trail.append(f"庭审中向{ROLES[q['target']]['name']}发问：“{q['question']}”")
        for item in self.contributions:
            when = "法庭调查后" if item["stage"] == "investigation" else "法庭辩论后"
            trail.append(f"{when}以{item['name']}身份发言：“{item['text']}”")
        if trail:
            parts.append("【用户庭审参与轨迹】\n" + "\n".join(trail))
        final_label = "人民陪审员评议" if self.player_role == "juror" else "用户最终裁判预测"
        parts.append(f"【{final_label}】\n{jury_text}")
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
