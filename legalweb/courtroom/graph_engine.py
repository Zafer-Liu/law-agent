"""GraphTrialEngine：LangGraph 庭审图对 legacy TrialEngine 的适配层。

对外保持 app.py 依赖的最小接口：start() / continue_() 产出 SSE 事件字典，
transcript 属性与 export_state() 形状兼容 save_court_session。
图检查点（SqliteSaver）是续跑的唯一事实来源：LLMError 在节点内抛出时
superstep 不提交，图自动回滚到上一次提交点——无需 legacy 的手工回滚引擎。
"""

import os
import sqlite3
import threading
import uuid
from pathlib import Path

from .engine import PLAYER_ROLES

_CHECKPOINT_LOCK = threading.Lock()
_CHECKPOINTER = None
_CHECKPOINTER_PATH = None


def _graph_db_path() -> Path:
    """图检查点库路径：LAW_COURT_GRAPH_DB 优先，否则与主库同目录（复刻 app.py 的数据目录推导）。"""
    override = (os.getenv("LAW_COURT_GRAPH_DB") or "").strip()
    if override:
        return Path(override).expanduser()
    raw = (os.getenv("LAW_DATA_DIR") or "").strip()
    root = Path(raw).expanduser() if raw else Path.cwd() / ".law-data"
    return root / "court_graph.db"


def _get_checkpointer():
    global _CHECKPOINTER, _CHECKPOINTER_PATH
    with _CHECKPOINT_LOCK:
        if _CHECKPOINTER is None:
            from langgraph.checkpoint.sqlite import SqliteSaver
            _CHECKPOINTER_PATH = _graph_db_path()
            _CHECKPOINTER_PATH.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(_CHECKPOINTER_PATH, check_same_thread=False)
            _CHECKPOINTER = SqliteSaver(conn)
        return _CHECKPOINTER


class GraphTrialEngine:
    """duck-type 兼容 TrialEngine（app.save_court_session 所需的子集）。"""

    def __init__(self, case: dict, llm, player_role: str = "juror", thread_id: str | None = None):
        self.case = case
        self.llm = llm
        self.player_role = player_role if player_role in PLAYER_ROLES else "juror"
        self.thread_id = thread_id or uuid.uuid4().hex
        self.pending_checkpoint = None
        self.pending_next_stage = ""
        from .graph import build_court_graph
        self._graph = build_court_graph(case, llm, self.player_role,
                                        checkpointer=_get_checkpointer())
        self._config = {"configurable": {"thread_id": self.thread_id}}

    # ------------------------------------------------------------------
    # SSE 事件流
    # ------------------------------------------------------------------
    def start(self):
        yield from self._run({"player_role": self.player_role})

    def continue_(self, payload: dict):
        from langgraph.types import Command
        yield from self._run(Command(resume=payload))

    def _run(self, input_):
        with _CHECKPOINT_LOCK:
            for mode, chunk in self._graph.stream(
                    input_, self._config, stream_mode=["custom", "updates"]):
                if mode == "custom":
                    yield chunk
                    continue
                # updates 模式：只关心 __interrupt__
                interrupts = chunk.get("__interrupt__") if isinstance(chunk, dict) else None
                if interrupts:
                    payload = interrupts[0].value
                    self.pending_checkpoint = payload
                    self.pending_next_stage = payload.get("next", "")
                    yield payload
                    return
            # 流自然结束 = 庭审走完（finish 节点已发出收尾法官台词）
            self.pending_checkpoint = None
            self.pending_next_stage = "await_deliberation"
            yield {"type": "await_deliberation", "options": self.case["options"],
                   "player_role": self.player_role}

    # ------------------------------------------------------------------
    # 状态（与 TrialEngine.export_state 同形，另附 langgraph 标记）
    # ------------------------------------------------------------------
    def _values(self) -> dict:
        snapshot = self._graph.get_state(self._config)
        return snapshot.values if snapshot else {}

    @property
    def transcript(self) -> list:
        return list(self._values().get("transcript") or [])

    def export_state(self) -> dict:
        vals = self._values()
        return {"summaries": list(vals.get("summaries") or []),
                "summarized_upto": int(vals.get("summarized_upto") or 0),
                "polls": list(vals.get("polls") or []),
                "questions": list(vals.get("questions") or []),
                "player_role": vals.get("player_role") or self.player_role,
                "contributions": list(vals.get("contributions") or []),
                "engine": "langgraph", "thread_id": self.thread_id,
                "checkpoint": self.pending_checkpoint,
                "next_stage": self.pending_next_stage or ""}
