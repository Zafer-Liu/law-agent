"""第二阶段（问题处理闭环）回归测试：法律问题工作台与会话管理。"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from legalweb import app as app_module
from legalweb.app import create_app
from legalweb.database import Database, new_id, now_iso
from legalweb.matter import match_suggested_cases, parse_matter_result


GOOD_REPORT = json.dumps({
    "issue_type": "劳动争议",
    "fact_summary": "用人单位拖欠两个月工资，劳动者可主张工资与经济补偿。",
    "applicable_laws": [{
        "title": "劳动合同法", "article": "第四十七条",
        "reason": "经济补偿按工作年限计算",
        "excerpt": "经济补偿按劳动者在本单位工作的年限计算。",
    }],
    "missing_info": ["劳动合同签订时间"],
    "evidence_checklist": ["工资流水", "劳动合同"],
    "action_steps": [{"step": "保留证据", "detail": "导出工资流水与考勤记录"}],
    "risks": ["仲裁时效为一年"],
    "disclaimer": "本分析不构成法律意见。",
}, ensure_ascii=False)

BAD_REPORT_LAW = [{
    "title": "编造的法律", "article": "第九十九条", "reason": "x", "excerpt": "y",
}]


class FakeMatterLLM:
    configured = True

    def __init__(self, report=GOOD_REPORT):
        self.report = report

    def stream(self, *_args, **_kwargs):
        yield "delta", "unused"

    def complete(self, *_args, **_kwargs):
        return self.report


GROUNDED_HIT = {
    "id": 1,
    "source_title": "劳动合同法",
    "section": "第四十七条",
    "ordinal": 0,
    "text": "第四十七条 经济补偿按劳动者在本单位工作的年限计算。",
}


def make_app(llm):
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


def register(client, username, password="secret123"):
    return client.post("/api/auth/register", json={
        "username": username, "password": password})


def submit_matter(client, **overrides):
    """提交事项并在 retrieve 补丁上下文内读完 SSE（生成器惰性执行）。"""
    payload = {
        "what": "公司拖欠我两个月工资共一万八千元，未提前通知就解除劳动合同。",
        "goal": "拿回被拖欠的工资，并确认是否有经济补偿",
        "parties": "我和用人单位",
        "amount": "18000 元",
    }
    payload.update(overrides)
    with patch("legalweb.app.retrieve", return_value={
            "hits": [GROUNDED_HIT], "backend": "lexical+fts5", "degraded": True}):
        response = client.post("/api/matters", json=payload)
        events = [json.loads(line[6:]) for line in
                  response.data.decode("utf-8").splitlines()
                  if line.startswith("data: ")]
    return events


def result_of(events):
    return next((event for event in reversed(events) if event["type"] == "result"), None)


class MatterFlowTests(unittest.TestCase):
    def setUp(self):
        app_module._auth_attempts.clear()
        self.addCleanup(app_module._auth_attempts.clear)
        self.app, self.directory = make_app(FakeMatterLLM())
        self.client = self.app.test_client()
        self.client.post("/api/auth/register", json={
            "username": "alfa", "password": "secret123"})

    def test_matter_full_flow_with_whitelist_enforcement(self):
        events = submit_matter(self.client)
        result = result_of(events)
        self.assertTrue(result)
        report = result["report"]
        self.assertEqual(report["issue_type"], "劳动争议")
        self.assertEqual(len(report["applicable_laws"]), 1)
        law = report["applicable_laws"][0]
        # 适用依据只可能来自检索白名单
        self.assertEqual(law["title"], "劳动合同法")
        self.assertEqual(law["article"], "第四十七条")
        self.assertIn("仲裁时效", "".join(report["risks"]))
        # 落库为 ready，列表与详情可取回
        listing = self.client.get("/api/matters").get_json()["matters"]
        self.assertEqual(listing[0]["status"], "ready")
        detail = self.client.get(f"/api/matters/{result['matter_id']}").get_json()
        self.assertEqual(detail["result"]["report"]["issue_type"], "劳动争议")
        self.assertEqual(detail["intake"]["amount"], "18000 元")

    def test_fabricated_laws_are_dropped(self):
        report = json.loads(GOOD_REPORT)
        report["applicable_laws"] = BAD_REPORT_LAW
        app, _directory = make_app(FakeMatterLLM(json.dumps(report, ensure_ascii=False)))
        client = app.test_client()
        client.post("/api/auth/register", json={"username": "alfa", "password": "secret123"})
        result = result_of(submit_matter(client))
        self.assertEqual(result["report"]["applicable_laws"], [])

    def test_invalid_json_marks_failed_and_regenerate_recovers(self):
        app, directory = make_app(FakeMatterLLM("这不是 JSON"))
        client = app.test_client()
        client.post("/api/auth/register", json={"username": "alfa", "password": "secret123"})
        events = submit_matter(client)
        self.assertIsNone(result_of(events))
        self.assertTrue(any(event["type"] == "error" for event in events))
        matter_id = client.get("/api/matters").get_json()["matters"][0]["id"]
        detail = client.get(f"/api/matters/{matter_id}").get_json()
        self.assertEqual(detail["status"], "failed")
        # 同一数据目录重建应用（模型恢复正常），重试会话令牌仍有效
        with patch.dict(os.environ, {"LAW_DATA_DIR": str(directory)}), \
                patch("legalweb.app.seed_sources"), \
                patch("legalweb.app.AUTH_RATE_MAX", 10_000), \
                patch("legalweb.app.LLMClient", return_value=FakeMatterLLM(GOOD_REPORT)):
            recovered_app = create_app()
        recovered = recovered_app.test_client()
        # 同一数据目录：用户已存在，重新登录拿会话
        self.assertEqual(recovered.post("/api/auth/login", json={
            "username": "alfa", "password": "secret123"}).status_code, 200)
        with patch("legalweb.app.retrieve", return_value={
                "hits": [GROUNDED_HIT], "backend": "lexical+fts5", "degraded": True}):
            response = recovered.post(f"/api/matters/{matter_id}/regenerate")
            regenerate_events = [json.loads(line[6:]) for line in
                                 response.data.decode("utf-8").splitlines()
                                 if line.startswith("data: ")]
        self.assertTrue(result_of(regenerate_events))
        self.assertEqual(recovered.get(
            f"/api/matters/{matter_id}").get_json()["status"], "ready")

    def test_validation_and_isolation(self):
        # 参数校验在检索/生成前触发
        self.assertEqual(self.client.post("/api/matters", json={
            "what": "太短", "goal": "诉求"}).status_code, 400)
        self.assertEqual(self.client.post("/api/matters", json={
            "what": "足够长的问题描述内容", "goal": ""}).status_code, 400)
        anonymous = self.app.test_client()
        self.assertEqual(anonymous.post("/api/matters", json={
            "what": "足够长的问题描述内容", "goal": "诉求"}).status_code, 401)
        # 另一用户不可见、不可删
        result = result_of(submit_matter(self.client))
        self.client.post("/api/auth/register", json={"username": "beta", "password": "secret123"})
        self.assertEqual(self.client.get("/api/matters").get_json()["matters"], [])
        self.assertEqual(self.client.get(
            f"/api/matters/{result['matter_id']}").status_code, 404)
        self.assertEqual(self.client.delete(
            f"/api/matters/{result['matter_id']}").get_json()["deleted"], 0)

    def test_suggested_cases_match(self):
        matched = match_suggested_cases("刑事风险", "对方持刀行凶后我还击", "正当防卫争议")
        self.assertEqual([item["case_id"] for item in matched][:1], ["haicheng"])
        self.assertEqual(match_suggested_cases("完全无关的文本"), [])
        bank = match_suggested_cases("帮信", "出借银行卡给他人跑分", "")
        self.assertTrue(any(item["case_id"] == "shenduzhuanzhang" for item in bank))


class ConversationManagementTests(unittest.TestCase):
    def setUp(self):
        app_module._auth_attempts.clear()
        self.addCleanup(app_module._auth_attempts.clear)
        self.app, self.directory = make_app(FakeMatterLLM())
        self.client = self.app.test_client()
        self.client.post("/api/auth/register", json={
            "username": "alfa", "password": "secret123"})
        with Database(self.directory).connect() as db:
            for index, title in enumerate(("普通对话", "置顶对话")):
                db.execute("INSERT INTO conversations(id,title,owner_id,created_at,pinned)"
                           " VALUES (?,?,?,?,?)",
                           (f"conv-{index}", title,
                            f"u:{self.user_id()}", now_iso(), index))
                db.execute("INSERT INTO messages(id,conversation_id,role,content,payload,created_at)"
                           " VALUES (?,?,?,?,?,?)",
                           (new_id(), f"conv-{index}", "user", f"问题{index}", "{}", now_iso()))
                db.execute("INSERT INTO messages(id,conversation_id,role,content,payload,created_at)"
                           " VALUES (?,?,?,?,?,?)",
                           (new_id(), f"conv-{index}", "assistant", f"回答{index} [C1]",
                            json.dumps({"citations": [{"label": "C1", "source": "测试法",
                                                       "article": "第一条"}]}), now_iso()))

    def user_id(self):
        return self.client.get("/api/auth/me").get_json()["user"]["id"]

    def test_rename_pin_and_ordering(self):
        self.assertEqual(self.client.patch(
            "/api/conversations/conv-0", json={"title": ""}).status_code, 400)
        renamed = self.client.patch(
            "/api/conversations/conv-0", json={"title": "新名字"})
        self.assertEqual(renamed.get_json()["updated"], 1)
        pinned = self.client.post("/api/conversations/conv-0/pin", json={"pinned": True})
        self.assertEqual(pinned.get_json()["pinned"], True)
        listing = self.client.get("/api/conversations").get_json()["conversations"]
        # 两条置顶对话排在前（同为置顶时次序不依赖时钟），未置顶排最后
        self.assertEqual({item["title"] for item in listing[:2]}, {"新名字", "置顶对话"})
        self.assertTrue(all(item["pinned"] for item in listing[:2]))
        unpinned = self.client.post("/api/conversations/conv-0/pin", json={"pinned": False})
        self.assertEqual(unpinned.get_json()["pinned"], False)
        listing = self.client.get("/api/conversations").get_json()["conversations"]
        self.assertEqual(listing[0]["title"], "置顶对话")
        self.assertFalse(listing[1]["pinned"])
        # 非本人对话不可重命名
        self.client.post("/api/auth/register", json={"username": "beta", "password": "secret123"})
        self.assertEqual(self.client.patch(
            "/api/conversations/conv-1", json={"title": "x"}).get_json()["updated"], 0)

    def test_markdown_export_contains_dialogue_and_citations(self):
        response = self.client.get("/api/conversations/conv-0/export")
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertIn(".md", response.headers["Content-Disposition"])
        body = response.get_data(as_text=True)
        self.assertIn("普通对话", body)
        self.assertIn("问题0", body)
        self.assertIn("回答0", body)
        self.assertIn("依据：测试法 / 第一条", body)
        self.assertIn("不构成法律意见", body)
        # 非本人对话 404
        self.client.post("/api/auth/register", json={"username": "beta", "password": "secret123"})
        self.assertEqual(self.client.get("/api/conversations/conv-1/export").status_code, 404)


if __name__ == "__main__":
    unittest.main()
