"""LangGraph 版庭审状态机：复刻 engine.TrialEngine 的对外行为。

拓扑与事件顺序与 legacy 引擎逐字对齐（engine.py 是行为的唯一事实来源）：
- 程序性台词 / 检查点文案 / Agent 任务串全部原样复制；
- SSE 事件只经 get_stream_writer() 旁路发出，绝不写入图状态；
- interrupt() 所在节点在其之前不产生任何副作用（writer 事件 / LLM 调用 /
  状态变更），保证 resume 重放节点时不重复输出；
- 每个 superstep 提交一次，节点返回"整份新列表"（last-write-wins 通道），
  与 legacy 的 _record_poll 幂等替换语义天然兼容。

状态字段镜像 TrialEngine：transcript / summaries / summarized_upto / polls /
questions / contributions / player_role（另加内部路由标记 pending_route）。
"""

import time
from typing import TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from ..llm import LLMError
from .agents import JUDGE_BADGE, build_roles
from .engine import MAX_JUROR_QUESTIONS, PLAYER_ROLES, _re_split


class CourtState(TypedDict, total=False):
    transcript: list        # [{"role","name","text", "player"?}]
    summaries: list         # 书记员阶段纪要
    summarized_upto: int
    polls: list             # [{"stage","option_id","label"}]
    questions: list         # [{"target","question"}]
    contributions: list     # 用户以诉讼角色提交的关键发言
    player_role: str
    pending_route: str      # juror_question 轮次的内部路由标记


def _judge_line(text: str) -> dict:
    return {"type": "line", "role": "judge", "name": "审判长", "badge": JUDGE_BADGE, "text": text}


def build_court_graph(case: dict, llm, player_role: str, checkpointer=None):
    """编译庭审图。player_role 决定条件路由（与 TrialEngine.run 相同的分支）。"""
    roles = build_roles(case)
    trial = case["trial"]
    pr = player_role if player_role in PLAYER_ROLES else "juror"

    # ------------------------------------------------------------------
    # 上下文组装（与 engine.TrialEngine 逐字段一致）
    # ------------------------------------------------------------------
    def dossier_text() -> str:
        return "\n".join(f"{e['id']} {e['type']}：{e['content']}" for e in case["dossier"])

    def laws_text() -> str:
        return "\n\n".join(f"{name}：{text}" for name, text in case["laws"].items())

    def recent_text(state: dict, limit: int = 6) -> str:
        transcript = state.get("transcript") or []
        if not transcript:
            return "（庭审刚开始）"
        lines = [f"{item['name']}：{item['text']}" for item in transcript[-limit:]]
        if state.get("summaries"):
            lines.insert(0, "前序阶段纪要：" + "；".join(state["summaries"]))
        return "\n".join(lines)

    def agent_messages(state: dict, role_key: str, task: str, with_laws: bool = False) -> list[dict]:
        role = roles[role_key]
        parts = [f"【案情摘要（起诉指控）】\n{case['facts']}",
                 f"【争议焦点】\n" + "\n".join(f"{i}. {d}" for i, d in enumerate(case["disputes"], 1)),
                 f"【卷宗】\n{dossier_text()}"]
        if with_laws:
            parts.append(f"【相关法条（仅可引用这些原文）】\n{laws_text()}")
        parts.append(f"【庭审记录】\n{recent_text(state)}")
        parts.append(f"【你的任务】\n{task}")
        return [{"role": "system", "content": role["system"]},
                {"role": "user", "content": "\n\n".join(parts)}]

    # ------------------------------------------------------------------
    # Agent 回合（engine.turn 的 writer 版）
    # ------------------------------------------------------------------
    def run_turn(w, tr: list, state: dict, role_key: str, task: str, with_laws: bool = False):
        """一个 Agent 回合：writer 发 turn_start/delta*/turn_end，文本就地落入 tr。"""
        role = roles[role_key]
        # 与 legacy 一致：上下文使用"实时"庭审记录（含本节点先前回合）
        state = {**state, "transcript": tr}
        messages = agent_messages(state, role_key, task, with_laws)
        w({"type": "turn_start", "role": role_key, "name": role["name"], "badge": role["badge"]})
        text = ""
        last_error = None
        for attempt in range(3):
            try:
                text = llm.complete(messages, temperature=role["temperature"], max_tokens=8000)
                break
            except LLMError as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(2)
                    w({"type": "delta", "text": "（正在重新组织发言…）"})
        else:
            raise last_error
        for seg in _re_split(text):
            if seg:
                w({"type": "delta", "text": seg})
        tr.append({"role": role_key, "name": role["name"], "text": text})
        w({"type": "turn_end", "role": role_key})

    def clerk_note(w, state: dict) -> dict:
        """阶段纪要：短调用；失败静默跳过。返回状态更新（可能为空 dict）。"""
        transcript = state.get("transcript") or []
        fresh = transcript[state.get("summarized_upto") or 0:]
        update = {}
        if not fresh:
            return update
        content = "\n".join(f"{item['name']}：{item['text']}" for item in fresh)
        role = roles["clerk"]
        messages = [{"role": "system", "content": role["system"]},
                    {"role": "user", "content": f"【需要纪要的庭审内容】\n{content}"}]
        try:
            note = llm.complete(messages, temperature=role["temperature"], max_tokens=500)
        except LLMError:
            note = ""
        update["summarized_upto"] = len(transcript)
        if note:
            update["summaries"] = [*(state.get("summaries") or []), note]
            w({"type": "summary", "text": note})
        return update

    def record_poll(state: dict, stage: str, data: dict) -> tuple[str, list]:
        polls = list(state.get("polls") or [])
        option_id = (data.get("poll") or "").strip()
        option = next((o for o in case["options"] if o["id"] == option_id), None)
        if not option:
            raise ValueError("无效的预判选项")
        # 幂等：同一阶段的预判只保留最新一次
        polls = [p for p in polls if p["stage"] != stage]
        polls.append({"stage": stage, "option_id": option_id, "label": option["label"]})
        return option["label"], polls

    # ------------------------------------------------------------------
    # 阶段节点（engine.stage_* 的 writer 版；文本逐字复制）
    # ------------------------------------------------------------------
    def node_opening(state: dict) -> dict:
        w = get_stream_writer()
        tr = list(state.get("transcript") or [])
        w({"type": "phase", "phase": "opening", "title": "开庭"})
        w(_judge_line(trial["lines"]["opening"]))
        w(_judge_line(trial["lines"]["ask_defendant"]))
        run_turn(w, tr, state, "defendant",
                 "审判长问你对起诉书指控的犯罪事实有无异议。请当庭回答，"
                 "并按卷宗中你向侦查机关所作的供述或陈述简短说明理由，不超过60字。")
        return {"transcript": tr}

    def node_investigation(state: dict) -> dict:
        w = get_stream_writer()
        tr = list(state.get("transcript") or [])
        w({"type": "phase", "phase": "investigation", "title": "法庭调查"})
        w(_judge_line("现在进行法庭调查。请公诉人宣读起诉书指控的事实与罪名要点。"))
        run_turn(w, tr, state, "prosecutor",
                 "用不超过80字宣读指控要点：发生了什么、指控什么罪、双方争议在哪里。"
                 "不要照念起诉书。")
        w(_judge_line("被告人，你现在就起诉书指控的事实向法庭陈述。"))
        run_turn(w, tr, state, "defendant",
                 "用普通人的话说清事情经过，与卷宗中你的陈述一致，不超过65字。")
        w(_judge_line("下面进行举证质证。请公诉人集中说明关键证据，辩护人随后回应。"))
        evidence_names = "、".join(
            f"{item['type']}（{item['id']}）"
            for item in case["dossier"] if item["id"] in case["key_evidence"]
        )
        run_turn(w, tr, state, "prosecutor",
                 f"集中说明这组关键证据：{evidence_names}。只挑两项最重要的，"
                 "用不超过90字说明它们证明什么，不要逐项宣读。")
        run_turn(w, tr, state, "defender",
                 "回应公诉人刚才的举证。只指出一个最关键的证明局限或对被告人有利的细节，"
                 "不超过90字，不要重复证据全文。")
        update = clerk_note(w, {**state, "transcript": tr})
        update["transcript"] = tr
        return update

    def node_debate(state: dict) -> dict:
        w = get_stream_writer()
        tr = list(state.get("transcript") or [])
        hints = trial["debate_hints"]
        w({"type": "phase", "phase": "debate", "title": "法庭辩论"})
        w(_judge_line("法庭调查结束，现在进行法庭辩论。先由公诉人发表公诉意见。"))
        run_turn(w, tr, state, "prosecutor",
                 f"使用规范的公诉语言发表意见：{hints['prosecutor']}。"
                 "围绕一个核心理由作出法律评价，不背诵法条，不超过110字。",
                 with_laws=True)
        run_turn(w, tr, state, "defender",
                 f"使用专业的辩护语言回应公诉意见：{hints['defender']}。"
                 "围绕一个核心理由提出辩护主张，不背诵法条，不超过110字。",
                 with_laws=True)
        run_turn(w, tr, state, "prosecutor",
                 "只回应辩护人刚才最关键的一句话，不重复前文，不超过60字。", with_laws=True)
        run_turn(w, tr, state, "defender",
                 "只回应公诉人刚才最关键的一句话，并用一句话收束辩护，不超过60字。",
                 with_laws=True)
        update = clerk_note(w, {**state, "transcript": tr})
        update["transcript"] = tr
        return update

    def node_statement(state: dict) -> dict:
        w = get_stream_writer()
        tr = list(state.get("transcript") or [])
        w({"type": "phase", "phase": "statement", "title": "最后陈述"})
        w(_judge_line(trial["lines"]["statement_invite"]))
        if state.get("player_role") == "defendant":
            w(_judge_line("被告人刚才提交的最后陈述已记录在案。"))
            return {}
        run_turn(w, tr, state, "defendant",
                 "像普通人一样作最后陈述：说清你的心情和对法庭的请求，不使用法律术语，"
                 "不超过65字。")
        return {"transcript": tr}

    def node_finish(state: dict) -> dict:
        w = get_stream_writer()
        role = state.get("player_role") or pr
        if role == "juror":
            text = ("现在休庭，合议庭评议。作为今天的人民陪审员，请你根据庭审查明的事实"
                    "与法律，独立作出评议。")
        elif role == "audience":
            text = ("庭审已经结束。作为旁听观众，你可以根据刚才听到的事实与法律，"
                    "作出你的裁判预测。")
        else:
            text = ("你的角色任务已经完成。现在请暂时跳出诉讼立场，根据庭审查明的事实与法律，"
                    "作出最终裁判预测。")
        w(_judge_line(text))
        # await_deliberation 事件由 graph_engine 在流结束时补发（保证恰好一次且居末）
        return {}

    # ------------------------------------------------------------------
    # 陪审员发问轮（intro 与 interrupt 分节点，避免 resume 重放重复输出）
    # ------------------------------------------------------------------
    def node_q_intro(state: dict) -> dict:
        w = get_stream_writer()
        w(_judge_line("法庭调查结束。在记录阶段性意见之前，人民陪审员可先就卷宗证据"
                      "与庭审疑点，向公诉人、辩护人或被告人发问，发问将被当庭作答；"
                      "也可以不发问，直接记录意见。"))
        return {}

    def node_juror_question(state: dict) -> dict:
        questions = list(state.get("questions") or [])
        data = interrupt({"type": "checkpoint", "kind": "juror_question",
                          "title": "陪审员发问", "next": "mid_review",
                          "remaining": MAX_JUROR_QUESTIONS - len(questions)})
        question = (data.get("question") or "").strip()
        target = (data.get("target") or "").strip()
        if not question:
            get_stream_writer()(_judge_line("人民陪审员表示不再发问，庭审继续。"))
            return {"pending_route": ""}
        if len(questions) >= MAX_JUROR_QUESTIONS:
            raise ValueError("发问次数已达上限")
        if target not in roles or target == "clerk":
            raise ValueError("发问对象需为公诉人、辩护人或被告人")
        return {"questions": [*questions, {"target": target, "question": question[:200]}],
                "pending_route": "answer"}

    def route_after_question(state: dict) -> str:
        return "q_answer" if state.get("pending_route") == "answer" else "mid_review_intro"

    def node_q_answer(state: dict) -> dict:
        w = get_stream_writer()
        tr = list(state.get("transcript") or [])
        q = state["questions"][-1]
        target, question = q["target"], q["question"]
        role = roles[target]
        w(_judge_line(f"人民陪审员向{role['name']}发问：“{question}”请当庭作答。"))
        run_turn(w, tr, state, target,
                 f"人民陪审员在法庭调查结束后向你发问：“{question}”\n"
                 f"请当庭正面作答：只说结论和一个理由，使用证据名称并保留内部编号，"
                 f"不超过70字。")
        w(_judge_line("陪审员的发问与回答已记录在案。"))
        remaining = MAX_JUROR_QUESTIONS - len(state["questions"])
        update = {"transcript": tr}
        if remaining > 0:
            w(_judge_line(f"如仍有疑问，陪审员可继续发问（还可发问{remaining}次），"
                          "也可以直接记录阶段性意见。"))
            update["pending_route"] = "question"
        else:
            update["pending_route"] = ""
        return update

    def route_after_answer(state: dict) -> str:
        return "juror_question" if state.get("pending_route") == "question" else "mid_review_intro"

    # ------------------------------------------------------------------
    # 阶段性预判（mid_review）
    # ------------------------------------------------------------------
    def node_mid_review_intro(state: dict) -> dict:
        w = get_stream_writer()
        w(_judge_line("发问环节结束。请人民陪审员记录阶段性意见，"
                      "合议庭评议时将参考你的判断。"))
        return {}

    def node_mid_review(state: dict) -> dict:
        w = get_stream_writer()
        data = interrupt({"type": "checkpoint", "kind": "mid_review",
                          "title": "阶段性预判", "next": "debate",
                          "options": case["options"], "allow_question": False})
        label, polls = record_poll(state, "investigation", data)
        w(_judge_line(f"书记员记录：人民陪审员在法庭调查后的阶段意见为——{label}。"))
        update = {"polls": polls}
        # 与 legacy 一致：mid_review 提交可附带一次发问（allow_question=False 时前端不出现）
        question = (data.get("question") or "").strip()
        target = (data.get("target") or "").strip()
        if question and target in roles and target != "clerk":
            tr = list(state.get("transcript") or [])
            role = roles[target]
            update["questions"] = [*(state.get("questions") or []),
                                   {"target": target, "question": question[:200]}]
            w(_judge_line(f"人民陪审员向{role['name']}发问：“{question}”请当庭作答。"))
            run_turn(w, tr, {**state, "questions": update["questions"]}, target,
                     f"人民陪审员在法庭调查结束后向你发问：“{question}”\n"
                     f"请当庭正面作答：只说结论和一个理由，使用证据名称并保留内部编号，"
                     f"不超过70字。")
            w(_judge_line("陪审员的发问与回答已记录在案。"))
            update["transcript"] = tr
        return update

    # ------------------------------------------------------------------
    # 诉讼角色发言（intro 与 interrupt 分节点）
    # ------------------------------------------------------------------
    def make_role_input_nodes(stage: str, next_stage: str):
        def node_intro(state: dict) -> dict:
            w = get_stream_writer()
            role = PLAYER_ROLES[state.get("player_role") or pr]
            title, prompt, placeholder = trial["checkpoints"][state.get("player_role") or pr][stage]
            w(_judge_line(f"现在请{role['name']}提交“{title}”，发言将直接记入庭审记录。"))
            return {}

        def node_wait(state: dict) -> dict:
            role_pr = state.get("player_role") or pr
            role = PLAYER_ROLES[role_pr]
            title, prompt, placeholder = trial["checkpoints"][role_pr][stage]
            options = trial["statement_options"][role_pr][stage]
            data = interrupt({"type": "checkpoint", "kind": "role_input", "stage": stage,
                              "role": role_pr, "role_name": role["name"], "badge": role["badge"],
                              "title": title, "prompt": prompt, "placeholder": placeholder,
                              "options": options, "next": next_stage})
            stage_in = (data.get("stage") or "").strip()
            statement = (data.get("statement") or "").strip()
            if role_pr == "juror" or stage_in not in ("investigation", "debate"):
                raise ValueError("无效的角色发言环节")
            if not 10 <= len(statement) <= 500:
                raise ValueError("角色发言需在 10-500 字之间")
            contributions = [item for item in state.get("contributions") or []
                             if item.get("stage") != stage_in]
            contributions.append({"stage": stage_in, "role": role_pr,
                                  "name": role["name"], "text": statement})
            tr = [*(state.get("transcript") or []),
                  {"role": role_pr, "name": role["name"], "text": statement, "player": True}]
            w = get_stream_writer()
            w({"type": "line", "role": role_pr, "name": role["name"],
               "badge": role["badge"], "text": statement, "player": True})
            w(_judge_line(f"{role['name']}的发言已记录在案，庭审继续。"))
            return {"contributions": contributions, "transcript": tr}

        return node_intro, node_wait

    role_in_intro, role_in_wait = make_role_input_nodes("investigation", "debate")
    role_de_intro, role_de_wait = make_role_input_nodes("debate", "statement")

    # ------------------------------------------------------------------
    # 辩论后复核（poll2）
    # ------------------------------------------------------------------
    def node_poll2_intro(state: dict) -> dict:
        w = get_stream_writer()
        w(_judge_line("法庭辩论结束。请人民陪审员再次记录意见——听完控辩双方的对抗，"
                      "你的判断是否发生了变化？"))
        return {}

    def node_poll2(state: dict) -> dict:
        w = get_stream_writer()
        prev = next((p["label"] for p in reversed(state.get("polls") or [])
                     if p["stage"] == "investigation"), None)
        data = interrupt({"type": "checkpoint", "kind": "poll",
                          "title": "辩论后的复核", "next": "statement",
                          "options": case["options"], "allow_question": False,
                          "previous_label": prev})
        label, polls = record_poll(state, "debate", data)
        prev_obj = next((p for p in polls if p["stage"] == "investigation"), None)
        if prev_obj and prev_obj["label"] != label:
            w(_judge_line(f"书记员记录：人民陪审员的意见由“{prev_obj['label']}”调整为"
                          f"“{label}”，合议庭评议时将重点关注这一变化。"))
        else:
            w(_judge_line(f"书记员记录：人民陪审员在法庭辩论后的阶段意见为——{label}。"))
        return {"polls": polls}

    # ------------------------------------------------------------------
    # 条件路由（与 TrialEngine.run 的角色分支一致）
    # ------------------------------------------------------------------
    def route_after_investigation(state: dict) -> str:
        role = state.get("player_role") or pr
        if role == "audience":
            return "debate"
        if role == "juror":
            return "q_intro"
        return "role_input_investigation_intro"

    def route_after_debate(state: dict) -> str:
        role = state.get("player_role") or pr
        if role == "audience":
            return "statement"
        if role == "juror":
            return "poll2_intro"
        return "role_input_debate_intro"

    builder = StateGraph(CourtState)
    builder.add_node("opening", node_opening)
    builder.add_node("investigation", node_investigation)
    builder.add_node("q_intro", node_q_intro)
    builder.add_node("juror_question", node_juror_question)
    builder.add_node("q_answer", node_q_answer)
    builder.add_node("mid_review_intro", node_mid_review_intro)
    builder.add_node("mid_review", node_mid_review)
    builder.add_node("role_input_investigation_intro", role_in_intro)
    builder.add_node("role_input_investigation", role_in_wait)
    builder.add_node("role_input_debate_intro", role_de_intro)
    builder.add_node("role_input_debate", role_de_wait)
    builder.add_node("debate", node_debate)
    builder.add_node("poll2_intro", node_poll2_intro)
    builder.add_node("poll2", node_poll2)
    builder.add_node("statement", node_statement)
    builder.add_node("finish", node_finish)

    builder.add_edge(START, "opening")
    builder.add_edge("opening", "investigation")
    builder.add_conditional_edges("investigation", route_after_investigation,
                                  ["debate", "q_intro", "role_input_investigation_intro"])
    builder.add_edge("q_intro", "juror_question")
    builder.add_conditional_edges("juror_question", route_after_question,
                                  ["q_answer", "mid_review_intro"])
    builder.add_conditional_edges("q_answer", route_after_answer,
                                  ["juror_question", "mid_review_intro"])
    builder.add_edge("mid_review_intro", "mid_review")
    builder.add_edge("mid_review", "debate")
    builder.add_edge("role_input_investigation_intro", "role_input_investigation")
    builder.add_edge("role_input_investigation", "debate")
    builder.add_conditional_edges("debate", route_after_debate,
                                  ["statement", "poll2_intro", "role_input_debate_intro"])
    builder.add_edge("role_input_debate_intro", "role_input_debate")
    builder.add_edge("role_input_debate", "statement")
    builder.add_edge("poll2_intro", "poll2")
    builder.add_edge("poll2", "statement")
    builder.add_edge("statement", "finish")
    builder.add_edge("finish", END)

    if checkpointer is not None:
        return builder.compile(checkpointer=checkpointer)
    return builder.compile()
