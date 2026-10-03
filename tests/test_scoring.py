"""第三阶段回归：问答→庭审直连推荐、评议说理评分。"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from legalweb import app as app_module
from legalweb.app import create_app
from legalweb.courtroom import scoring
from legalweb.courtroom.cases import CASES
from legalweb.courtroom.engine import TrialEngine
from legalweb.database import Database, new_id, now_iso

SCORE_JSON = json.dumps({
    "evidence": 8, "procedure": 6, "reasoning": 7,
    "overall": "抓住了防卫时间连续性这个关键。",
    "improvement": "应进一步讨论追击行为是否超出必要限度。",
}, ensure_ascii=False)


class FakeCourtLLM:
    """verdict_events 的复盘与说理评分共用 complete；输出评分 JSON。"""
    configured = True

    def stream(self, *_args, **_kwargs):
        yield "delta", "unused"

    def complete(self, *_args, **_kwargs):
        return SCORE_JSON


def make_app(llm=FakeCourtLLM()):
    directory = tempfile.mkdtemp()
    patches = [patch.dict(os.environ, {"LAW_DATA_DIR": directory}),
               patch("legalweb.app.seed_sources"),
               patch("legalweb.app.AUTH_RATE_MAX", 10_000),
               patch("legalweb.app.LLMClient", return_value=llm)]
    for item in patches:
        item.start()
    try:
        app = create_app()
    finally:
        for item in reversed(patches):
            item.stop()
    return app, Path(directory)


def sse_events(response):
    return [json.loads(line[6:]) for line in
            response.data.decode("utf-8").splitlines()
            if line.startswith("data: ")]


def make_session(directory, owner, case_id="haicheng"):
    session_id = new_id()
    engine_state = TrialEngine(CASES[case_id], FakeCourtLLM()).export_state()
    with Database(directory).connect() as db:
        db.execute(
            "INSERT INTO court_sessions (id,case_id,owner_id,transcript,deliberation,"
            "state,created_at) VALUES (?,?,?,?,?,?,?)",
            (session_id, case_id, owner, "[]", None,
             json.dumps({"next_stage": "await_deliberation"}, ensure_ascii=False),
             now_iso()))
    return session_id


class CourtSuggestTests(unittest.TestCase):
    def setUp(self):
        app_module._auth_attempts.clear()
        self.addCleanup(app_module._auth_attempts.clear)
        self.app, _directory = make_app()
        self.client = self.app.test_client()
        self.client.post("/api/auth/register", json={
            "username": "alfa", "password": "secret123"})

    def suggest(self, text):
        return self.client.post("/api/court/suggest", json={"text": text})

    def test_suggest_matches_and_isolation(self):
        matched = self.suggest("对方持刀行凶，我夺刀反击，这算正当防卫吗？").get_json()["cases"]
        self.assertEqual(matched[0]["case_id"], "haicheng")
        bank = self.suggest("把银行卡出借给别人跑分有什么风险？").get_json()["cases"]
        self.assertTrue(any(item["case_id"] == "shenduzhuanzhang" for item in bank))
        self.assertEqual(self.suggest("婚姻财产怎么分割").get_json()["cases"], [])
        self.assertEqual(self.suggest("短").get_json()["cases"], [])
        anonymous = self.app.test_client()
        self.assertEqual(anonymous.post("/api/court/suggest", json={
            "text": "正当防卫"}).status_code, 401)


class ParseScoreTests(unittest.TestCase):
    def test_parse_plain_and_fenced(self):
        score = scoring.parse_score(SCORE_JSON)
        self.assertEqual(score["evidence"], 8)
        self.assertEqual(score["procedure"], 6)
        score2 = scoring.parse_score("```json\n" + SCORE_JSON + "\n```")
        self.assertEqual(score2["reasoning"], 7)

    def test_clamps_and_rejects_garbage(self):
        weird = scoring.parse_score(json.dumps({
            "evidence": 99, "procedure": -3, "reasoning": "7",
            "overall": "o" * 500, "improvement": None}))
        self.assertEqual(weird["evidence"], 10)
        self.assertEqual(weird["procedure"], 0)
        self.assertEqual(len(weird["overall"]), 120)
        self.assertIsNone(scoring.parse_score("完全不是 JSON"))
        self.assertIsNone(scoring.parse_score('["数组"]'))
        self.assertIsNone(scoring.parse_score(
            json.dumps({"evidence": 0, "procedure": 0, "reasoning": 0})))


class ReasoningScoreFlowTests(unittest.TestCase):
    def setUp(self):
        app_module._auth_attempts.clear()
        self.addCleanup(app_module._auth_attempts.clear)
        self.app, self.directory = make_app()
        self.client = self.app.test_client()
        self.user = self.client.post("/api/auth/register", json={
            "username": "alfa", "password": "secret123"}).get_json()["user"]
        self.session_id = make_session(self.directory, f"u:{self.user['id']}")

    def deliberate(self, **extra):
        return self.client.post(
            f"/api/court/sessions/{self.session_id}/deliberation",
            json={"option": "C", **extra})

    def test_reasoning_scores_persisted_and_served_in_review(self):
        events = sse_events(self.deliberate(
            reasoning="我认为构成正当防卫：监控显示侵害持续仅 11 秒，夺刀与追刺是连续的防卫行为，"
                      "对方持刀行凶属于严重危及人身安全的暴力犯罪。"))
        types = [event["type"] for event in events]
        self.assertIn("verdict", types)
        self.assertIn("reasoning_score", types)
        score = next(event for event in events
                     if event["type"] == "reasoning_score")["score"]
        self.assertEqual(score["evidence"], 8)
        self.assertIn("防卫", score["overall"])
        # 复盘接口回放评分
        review = self.client.get(
            f"/api/court/sessions/{self.session_id}/review").get_json()
        self.assertEqual(review["reasoning_score"]["evidence"], 8)
        # 重复评议 409
        self.assertEqual(self.deliberate().status_code, 409)

    def test_without_reasoning_no_score_event(self):
        events = sse_events(self.deliberate(marks=["E4"]))
        self.assertNotIn("reasoning_score", [event["type"] for event in events])
        review = self.client.get(
            f"/api/court/sessions/{self.session_id}/review").get_json()
        self.assertIsNone(review["reasoning_score"])

    def test_bad_score_output_does_not_break_verdict(self):
        class BrokenScoreLLM(FakeCourtLLM):
            def complete(self, *_args, **_kwargs):
                return "评分模型输出损坏"

        app, directory = make_app(BrokenScoreLLM())
        client = app.test_client()
        user = client.post("/api/auth/register", json={
            "username": "alfa", "password": "secret123"}).get_json()["user"]
        session_id = make_session(directory, f"u:{user['id']}")
        events = sse_events(client.post(
            f"/api/court/sessions/{session_id}/deliberation",
            json={"option": "C",
                  "reasoning": "足够长的说理内容，用于触发评分链路但模型会输出无法解析的内容。"}))
        types = [event["type"] for event in events]
        self.assertIn("verdict", types)                      # 主链路完好
        self.assertNotIn("reasoning_score", types)           # 评分静默跳过
        self.assertIn("pet", types)                          # 庭审奖励照常


if __name__ == "__main__":
    unittest.main()
