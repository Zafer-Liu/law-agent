"""第二阶段补充回归：知识库效力治理（下架废止文本）、对话蒸馏、会话分类。"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from legalweb import app as app_module
from legalweb.app import create_app
from legalweb.database import Database, new_id, now_iso
from legalweb.matter import parse_distilled_intake


class FakeDistillLLM:
    configured = True

    def stream(self, *_args, **_kwargs):
        yield "delta", "unused"

    def __init__(self, complete_output):
        self.complete_output = complete_output

    def complete(self, *_args, **_kwargs):
        return self.complete_output


DISTILLED = json.dumps({
    "what": "某公司拖欠两个月的劳动报酬，未提前通知即解除劳动合同。",
    "when_where": "2026 年 8 月 · 某市",
    "parties": "用户与用人单位",
    "amount": "18000 元",
    "evidence": "工资流水",
    "goal": "拿回工资并确认经济补偿",
    "urgent": "时效还剩一个月",
    "手机号": "13800000000",   # 多余字段应被丢弃
}, ensure_ascii=False)


def make_app(complete_output=DISTILLED):
    directory = tempfile.mkdtemp()
    patches = [patch.dict(os.environ, {"LAW_DATA_DIR": directory}),
               patch("legalweb.app.seed_sources"),
               patch("legalweb.app.AUTH_RATE_MAX", 10_000),
               patch("legalweb.app.LLMClient",
                     return_value=FakeDistillLLM(complete_output))]
    for item in patches:
        item.start()
    try:
        app = create_app()
    finally:
        for item in reversed(patches):
            item.stop()
    return app, Path(directory)


class KbGovernanceTests(unittest.TestCase):
    """真实导入内置知识库，验证废止/已被修改文本不再进入检索。"""

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.mkdtemp()
        with patch.dict(os.environ, {"LAW_DATA_DIR": cls.directory}):
            cls.app = create_app()  # 不 patch seed_sources：真实导入
        cls.client = cls.app.test_client()
        cls.client.post("/api/auth/register", json={
            "username": "admin", "password": "secret123"})

    def test_retired_sources_are_not_indexed(self):
        titles = {item["title"] for item in
                  self.client.get("/api/kb").get_json()["sources"]}
        # 现行有效版本保留
        self.assertIn("法条 · 中华人民共和国民法典", titles)
        self.assertIn("法条 · 中华人民共和国刑法(2023修正)", titles)
        self.assertIn("法条 · 最高人民法院关于审理民间借贷案件适用法律若干问题的规定(2020第二次修正)", titles)
        # 废止 / 已被修改的版本下架
        self.assertNotIn("法条 · 中华人民共和国合同法", titles)
        self.assertNotIn("法条 · 借款合同条例", titles)
        self.assertNotIn("法条 · 最高人民法院关于审理民间借贷案件适用法律若干问题的规定", titles)
        self.assertNotIn("法条 · 最高人民法院关于审理民间借贷案件适用法律若干问题的规定(2020修正)", titles)

    def test_startup_purges_retired_rows_from_existing_db(self):
        """旧库中残留的下架行会在启动 seed_sources 时被删除。"""
        stamp = now_iso()
        with Database(Path(self.directory)).connect() as db:
            for title in ("法条 · 中华人民共和国合同法",
                          "法条 · 借款合同条例"):
                db.execute("INSERT INTO sources(id,title,filename,chunk_count,created_at)"
                           " VALUES (?,?,?,?,?)", (new_id(), title, "x.txt", 1, stamp))
        with patch.dict(os.environ, {"LAW_DATA_DIR": self.directory}), \
                patch("legalweb.app.LLMClient", return_value=FakeDistillLLM(DISTILLED)):
            create_app()
        titles = {item["title"] for item in
                  self.client.get("/api/kb").get_json()["sources"]}
        self.assertNotIn("法条 · 中华人民共和国合同法", titles)
        self.assertNotIn("法条 · 借款合同条例", titles)


class IntakeDistillTests(unittest.TestCase):
    def setUp(self):
        app_module._auth_attempts.clear()
        self.addCleanup(app_module._auth_attempts.clear)
        self.app, self.directory = make_app()
        self.client = self.app.test_client()
        self.user = self.client.post("/api/auth/register", json={
            "username": "alfa", "password": "secret123"}).get_json()["user"]
        stamp = now_iso()
        with Database(self.directory).connect() as db:
            db.execute("INSERT INTO conversations(id,title,owner_id,created_at)"
                       " VALUES ('conv-1','工资咨询',?,?)", (f"u:{self.user['id']}", stamp))
            for role, content in (("user", "公司拖欠我两个月工资共一万八，怎么办？"),
                                  ("assistant", "可以主张劳动报酬并考虑经济补偿 [C1]")):
                db.execute("INSERT INTO messages(id,conversation_id,role,content,payload,created_at)"
                           " VALUES (?,?,?,?,?,?)",
                           (new_id(), "conv-1", role, content, "{}", stamp))

    def test_distill_roundtrip_and_sanitization(self):
        response = self.client.post("/api/conversations/conv-1/intake")
        self.assertEqual(response.status_code, 200)
        intake = response.get_json()["intake"]
        self.assertIn("拖欠", intake["what"])
        self.assertEqual(intake["goal"], "拿回工资并确认经济补偿")
        self.assertEqual(intake["urgent"], "时效还剩一个月")
        self.assertNotIn("手机号", intake)  # schema 外字段丢弃

    def test_distill_requires_substantive_conversation(self):
        empty = json.dumps({"what": "", "goal": ""}, ensure_ascii=False)
        app, directory = make_app(empty)
        client = app.test_client()
        user = client.post("/api/auth/register", json={
            "username": "alfa", "password": "secret123"}).get_json()["user"]
        with Database(directory).connect() as db:
            db.execute("INSERT INTO conversations(id,title,owner_id,created_at)"
                       " VALUES ('conv-1','x',?,?)", (f"u:{user['id']}", now_iso()))
            db.execute("INSERT INTO messages(id,conversation_id,role,content,payload,created_at)"
                       " VALUES (?,?,?,?,?,?)",
                       (new_id(), "conv-1", "user", "随便聊聊", "{}", now_iso()))
        self.assertEqual(client.post(
            "/api/conversations/none/intake").status_code, 404)
        self.assertEqual(client.post(
            "/api/conversations/conv-1/intake").status_code, 422)

    def test_distill_isolation_and_anonymous(self):
        anonymous = self.app.test_client()
        self.assertEqual(anonymous.post(
            "/api/conversations/conv-1/intake").status_code, 401)
        self.client.post("/api/auth/register", json={"username": "beta", "password": "secret123"})
        self.assertEqual(self.client.post(
            "/api/conversations/conv-1/intake").status_code, 404)

    def test_parse_distilled_intake_clamps_lengths(self):
        intake = parse_distilled_intake("```json\n" + DISTILLED + "\n```")
        self.assertEqual(intake["amount"], "18000 元")
        long_text = json.dumps({"what": "长" * 5000, "goal": "g" * 1000})
        clamped = parse_distilled_intake(long_text)
        self.assertEqual(len(clamped["what"]), 2000)
        self.assertEqual(len(clamped["goal"]), 500)


class ConversationCategoryTests(unittest.TestCase):
    def setUp(self):
        app_module._auth_attempts.clear()
        self.addCleanup(app_module._auth_attempts.clear)
        self.app, self.directory = make_app()
        self.client = self.app.test_client()
        self.user = self.client.post("/api/auth/register", json={
            "username": "alfa", "password": "secret123"}).get_json()["user"]
        stamp = now_iso()
        with Database(self.directory).connect() as db:
            db.execute("INSERT INTO conversations(id,title,owner_id,created_at,category)"
                       " VALUES ('conv-1','劳动咨询',?,?, '劳动')",
                       (f"u:{self.user['id']}", stamp))
            db.execute("INSERT INTO messages(id,conversation_id,role,content,payload,created_at)"
                       " VALUES (?,?,?,?,?,?)",
                       (new_id(), "conv-1", "user", "问题", "{}", stamp))

    def test_category_roundtrip_and_validation(self):
        listing = self.client.get("/api/conversations").get_json()["conversations"]
        self.assertEqual(listing[0]["category"], "劳动")
        self.assertEqual(self.client.patch(
            "/api/conversations/conv-1", json={"category": "不存在的分类"}).status_code, 400)
        updated = self.client.patch(
            "/api/conversations/conv-1", json={"category": "刑事"})
        self.assertEqual(updated.get_json()["category"], "刑事")
        cleared = self.client.patch(
            "/api/conversations/conv-1", json={"category": ""})
        self.assertEqual(cleared.get_json()["updated"], 1)
        listing = self.client.get("/api/conversations").get_json()["conversations"]
        self.assertEqual(listing[0]["category"], "")
        # 标题和分类可一起更新；空请求 400
        both = self.client.patch("/api/conversations/conv-1",
                                 json={"title": "新标题", "category": "合同"})
        self.assertEqual(both.get_json()["title"], "新标题")
        self.assertEqual(self.client.patch(
            "/api/conversations/conv-1", json={}).status_code, 400)
        # 他人不可改
        self.client.post("/api/auth/register", json={"username": "beta", "password": "secret123"})
        self.assertEqual(self.client.patch(
            "/api/conversations/conv-1", json={"category": "消费"}).get_json()["updated"], 0)


if __name__ == "__main__":
    unittest.main()
