"""LangGraph 庭审引擎的 HTTP 端到端集成测试。

用 Flask test_client 走真实端点（/trial → /continue → /deliberation →
/sessions → /review），仅把法庭 LLM 换成确定性桩，验证：
1. langgraph 模式下 SSE 事件流、检查点落库与续跑接线正确；
2. 与 legacy 模式（LAW_COURT_ENGINE=legacy）在相同输入下的 SSE 事件
   序列完全一致——「不影响原功能」的端到端保证；
3. 旧会话（state 无 engine 标记）在 langgraph 模式下仍走 legacy 引擎续跑。
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from legalweb.app import create_app  # noqa: E402
from legalweb.courtroom import cases as court_cases  # noqa: E402


class StubCourtLLM:
    """确定性法庭 LLM：从消息摘要里选句子，保证新旧引擎输出一致。"""

    POOL = [
        "被告人明知他人实施犯罪，仍提供支付结算帮助。",
        "在案证据足以证明指控事实，情节严重。",
        "现有证据不能排除合理怀疑，依法应从轻认定。",
        "我承认转账，但当时确实不知道是犯罪的钱。",
        "审判长，我请求法庭从轻处理。",
        "综上，建议合议庭采纳控方意见。",
    ]

    def __init__(self):
        self.configured = True

    def complete(self, messages, temperature=0.2, max_tokens=2000) -> str:
        blob = json.dumps(messages, ensure_ascii=False)
        return self.POOL[len(blob) % len(self.POOL)]


def parse_sse(text: str) -> list[dict]:
    events = []
    for line in (text or "").splitlines():
        if line.startswith("data:"):
            events.append(json.loads(line[5:].strip()))
    return events


def sse_signature(events: list[dict]) -> list[tuple]:
    """事件的形状签名：类型 + 角色/检查点等关键字段，忽略正文文本。"""
    signature = []
    for event in events:
        kind = event.get("type")
        if kind == "line":
            signature.append((kind, event.get("role"), event.get("text")))
        elif kind == "phase":
            signature.append((kind, event.get("phase")))
        elif kind == "checkpoint":
            signature.append((kind, event.get("kind"), event.get("next"),
                              bool(event.get("options"))))
        elif kind == "turn_start" or kind == "turn_end":
            signature.append((kind, event.get("role")))
        else:
            signature.append((kind,))
    return signature


class CourtGraphHTTPTests(unittest.TestCase):
    """两个模式各起一个 app 实例，同一套输入走完整流程后比对。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.env = {
            "LAW_DATA_DIR": self._tmp.name,
            "LAW_COURT_GRAPH_DB": str(Path(self._tmp.name) / "court_graph.db"),
            "LAW_COURT_LLM_BASE_URL": "http://stub.local/v1",
            "LAW_COURT_LLM_MODEL": "stub",
            "AUTH_RATE_MAX": "10000",
        }
        self.case_id = next(iter(court_cases.CASES))

    def _app(self, engine_mode: str):
        env = dict(self.env)
        if engine_mode == "legacy":
            env["LAW_COURT_ENGINE"] = "legacy"
        else:
            env.pop("LAW_COURT_ENGINE", None)
        with patch.dict(os.environ, env), \
                patch("legalweb.app.seed_sources"), \
                patch("legalweb.app.LLMClient", lambda *a, **k: StubCourtLLM()):
            app = create_app()
        app.config["TESTING"] = True
        return app

    @staticmethod
    def _login(client, username: str):
        """注册或登录（同一数据目录重复注册会 409，此时改为登录）。"""
        res = client.post("/api/auth/register",
                          json={"username": username, "password": "secret123"})
        if res.status_code != 200:
            res = client.post("/api/auth/login",
                              json={"username": username, "password": "secret123"})
        assert res.status_code == 200, f"登录失败: {res.status_code}"

    def _run_trial(self, engine_mode: str, role: str = "juror",
                   username: str = "") -> dict:
        """完整跑一场庭审，返回 SSE 事件、会话状态与落库的 state。"""
        app = self._app(engine_mode)
        client = app.test_client()
        self._login(client, username or f"user-{engine_mode}")

        start = client.post(f"/api/court/cases/{self.case_id}/trial",
                            json={"role": role})
        self.assertEqual(start.status_code, 200)
        start_events = parse_sse(start.get_data(as_text=True))
        session_id = next(e["session_id"] for e in start_events if e["type"] == "session")

        all_events = list(start_events)
        # 只根据「最近一次响应」里的检查点续跑：await_deliberation 到达后
        # 不能再拿上一次响应的旧检查点重复提交。
        pending = next((e for e in reversed(start_events) if e["type"] == "checkpoint"),
                       None)
        guard = 0
        while pending is not None and guard < 20:
            guard += 1
            if pending["kind"] == "juror_question":
                if guard == 1:
                    payload = {"question": "你转账时是否核对过对方身份？",
                               "target": "defendant"}
                else:
                    payload = {}  # 第二次发问轮：结束发问
            elif pending["kind"] in ("mid_review", "poll"):
                payload = {"poll": court_cases.CASES[self.case_id]["options"][0]["id"]}
            else:  # role_input
                payload = {"statement": "本案证据足以证明指控事实成立，请法庭依法裁判。",
                           "stage": pending["stage"]}
            res = client.post(f"/api/court/sessions/{session_id}/continue", json=payload)
            self.assertEqual(res.status_code, 200)
            events = parse_sse(res.get_data(as_text=True))
            self.assertFalse(any(e["type"] == "error" for e in events),
                             f"continue 返回错误事件: {events[-1]}")
            all_events.extend(events)
            pending = next((e for e in reversed(events) if e["type"] == "checkpoint"),
                           None)

        from legalweb.database import Database
        database = Database(Path(self._tmp.name))
        with database.connect() as db:
            row = db.execute("SELECT * FROM court_sessions WHERE id=?",
                             (session_id,)).fetchone()
        state = json.loads(row["state"] or "null") or {}
        return {"events": all_events, "session_id": session_id,
                "state": state, "transcript": json.loads(row["transcript"] or "[]"),
                "client": client, "app": app}

    # ------------------------------------------------------------------
    def test_langgraph_http_flow_end_to_end(self):
        run = self._run_trial("langgraph")
        events = run["events"]
        # 首个检查点是发问轮
        first_cp = next(e for e in events if e["type"] == "checkpoint")
        self.assertEqual(first_cp["kind"], "juror_question")
        self.assertEqual(first_cp["remaining"], 3)
        # 流程终点
        self.assertTrue(any(e["type"] == "await_deliberation" for e in events))
        # 引擎标记与状态
        self.assertEqual(run["state"].get("engine"), "langgraph")
        self.assertTrue(run["state"].get("thread_id"))
        self.assertIsNone(run["state"].get("checkpoint"))

        # 评议端点：走完最后一步
        res = run["client"].post(f"/api/court/sessions/{run['session_id']}/deliberation",
                                 json={"option": court_cases.CASES[self.case_id]["options"][0]["id"],
                                       "years": 1,
                                       "reasoning": "证据链完整，被告人明知他人犯罪仍提供帮助，符合构成要件。"})
        self.assertEqual(res.status_code, 200)
        delib_events = parse_sse(res.get_data(as_text=True))
        self.assertTrue(any(e["type"] == "verdict" for e in delib_events))
        self.assertTrue(any(e["type"] == "result" for e in delib_events))

        # 列表与复盘端点
        listing = run["client"].get("/api/court/sessions").get_json()
        self.assertEqual(len(listing["sessions"]), 1)
        review = run["client"].get(
            f"/api/court/sessions/{run['session_id']}/review").get_json()
        self.assertEqual(review["session_id"], run["session_id"])
        self.assertEqual(review["deliberation"]["option_id"],
                         court_cases.CASES[self.case_id]["options"][0]["id"])
        self.assertTrue(review["transcript"])

    def test_langgraph_matches_legacy_event_for_event(self):
        """同一桩 LLM、同一输入：langgraph 与 legacy 的 SSE 事件签名与
        法官/书记员台词逐条一致。"""
        graph_run = self._run_trial("langgraph")
        legacy_run = self._run_trial("legacy")

        graph_sig = sse_signature([e for e in graph_run["events"] if e["type"] != "session"])
        legacy_sig = sse_signature([e for e in legacy_run["events"] if e["type"] != "session"])
        if graph_sig != legacy_sig:
            for index, (a, b) in enumerate(zip(graph_sig, legacy_sig)):
                if a != b:
                    self.fail(f"事件序列第 {index} 项不一致: langgraph={a!r} legacy={b!r}")
            self.fail(f"事件数量不一致: langgraph={len(graph_sig)} legacy={len(legacy_sig)}")

        # 落库 state 的核心字段一致
        for key in ("polls", "questions"):
            self.assertEqual(graph_run["state"].get(key), legacy_run["state"].get(key),
                             f"state[{key!r}] 不一致")
        self.assertEqual(len(graph_run["transcript"]), len(legacy_run["transcript"]),
                         "庭审记录条数不一致")

    def test_legacy_session_resumes_on_legacy_engine(self):
        """旧会话（无 engine 标记）在 langgraph 模式下仍由 legacy 引擎续跑。"""
        app = self._app("langgraph")
        client = app.test_client()
        self._login(client, "tester")

        # 先用 legacy 模式建会话（同一数据目录，同一账号）
        legacy_app = self._app("legacy")
        legacy_client = legacy_app.test_client()
        self._login(legacy_client, "tester")
        start = legacy_client.post(f"/api/court/cases/{self.case_id}/trial",
                                   json={"role": "juror"})
        events = parse_sse(start.get_data(as_text=True))
        session_id = next(e["session_id"] for e in events if e["type"] == "session")
        self.assertNotEqual(next((e for e in events if e["type"] == "checkpoint"),
                                 {}).get("kind"), None)

        # 换 langgraph 模式的同一数据目录实例继续：state 无 engine 标记 → legacy 路径
        res = client.post(f"/api/court/sessions/{session_id}/continue",
                          json={"question": "为何认定明知？", "target": "prosecutor"})
        self.assertEqual(res.status_code, 200)
        continued = parse_sse(res.get_data(as_text=True))
        self.assertFalse(any(e["type"] == "error" for e in continued))
        self.assertTrue(any(e["type"] == "line" for e in continued))


if __name__ == "__main__":
    unittest.main(verbosity=2)
