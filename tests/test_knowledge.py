"""第三阶段回归：知识复习闭环（标记知识点、獬豸知识奖励与统计、账户数据覆盖）。"""

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from legalweb import app as app_module
from legalweb.app import create_app
from legalweb.database import Database, now_iso


class FakeLLM:
    configured = True

    def stream(self, *_args, **_kwargs):
        yield "delta", "unused"

    def complete(self, *_args, **_kwargs):
        return "{}"


def make_app():
    directory = tempfile.mkdtemp()
    patches = [patch.dict(os.environ, {"LAW_DATA_DIR": directory}),
               patch("legalweb.app.seed_sources"),
               patch("legalweb.app.AUTH_RATE_MAX", 10_000),
               patch("legalweb.app.LLMClient", return_value=FakeLLM())]
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


def make_court_session(directory, owner, case_id="haicheng", finished=True):
    """直接落库一条已完成评议的庭审会话（免模型端到端）。"""
    from legalweb.database import new_id
    session_id = new_id()
    with Database(directory).connect() as db:
        db.execute(
            "INSERT INTO court_sessions (id,case_id,owner_id,transcript,deliberation,"
            "analysis,state,created_at,finished_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (session_id, case_id, owner, "[]",
             json.dumps({"option_id": "C", "label": "正当防卫", "marks": []},
                        ensure_ascii=False) if finished else None,
             "复盘", "{}", now_iso(), now_iso() if finished else None))
    return session_id


class KnowledgeFlowTests(unittest.TestCase):
    def setUp(self):
        app_module._auth_attempts.clear()
        self.addCleanup(app_module._auth_attempts.clear)
        self.app, self.directory = make_app()
        self.client = self.app.test_client()
        self.user = self.client.post("/api/auth/register", json={
            "username": "alfa", "password": "secret123"}).get_json()["user"]
        self.identity = f"u:{self.user['id']}"

    def mark(self, ref="《刑法》第二十条", status="mastered", **extra):
        return self.client.post("/api/knowledge", json={
            "article_ref": ref, "title": "海城夜行案 · 正当防卫",
            "case_id": "haicheng", "status": status, **extra})

    def test_mark_status_roundtrip_and_pet_reward(self):
        first = self.mark().get_json()
        self.assertTrue(first["first_time"])
        self.assertEqual(first["pet"]["gained"], 6)      # 首次掌握：獬豸 +6
        pet = self.client.get("/api/pet").get_json()
        self.assertEqual(pet["exp"], 6)
        self.assertEqual(pet["knowledge"], {"mastered": 1, "review": 0})

        # 改判为待复习：不再发经验，统计变化
        switched = self.mark(status="review").get_json()
        self.assertFalse(switched["first_time"])
        self.assertEqual(self.client.get("/api/pet").get_json()["exp"], 6)
        summary = self.client.get("/api/knowledge").get_json()["summary"]
        self.assertEqual(summary, {"mastered": 0, "review": 1})

        # 再次掌握（同一条文第二次）：奖励按条文去重，不重复发
        again = self.mark().get_json()
        self.assertFalse(again["first_time"])
        self.assertEqual(again["pet"], None)
        self.assertEqual(self.client.get("/api/pet").get_json()["exp"], 6)

    def test_clear_and_delete(self):
        self.mark()
        removed = self.mark(status="").get_json()
        self.assertEqual(removed["removed"], 1)
        self.assertEqual(self.client.get("/api/knowledge").get_json()["points"], [])
        self.mark()
        listing = self.client.get("/api/knowledge").get_json()["points"]
        self.assertEqual(self.client.delete(
            f"/api/knowledge/{listing[0]['id']}").get_json()["deleted"], 1)

    def test_validation_and_isolation(self):
        self.assertEqual(self.mark(ref="短").status_code, 400)
        self.assertEqual(self.mark(status="weird").status_code, 400)
        self.assertEqual(self.mark(case_id="no-such-case").status_code, 400)
        anonymous = self.app.test_client()
        self.assertEqual(anonymous.get("/api/knowledge").status_code, 401)
        self.assertEqual(anonymous.post("/api/knowledge", json={
            "article_ref": "《刑法》第二十条", "status": "mastered"}).status_code, 401)
        # beta 看不到也删不掉 alfa 的知识点
        self.mark()
        self.client.post("/api/auth/register", json={"username": "beta", "password": "secret123"})
        self.assertEqual(self.client.get("/api/knowledge").get_json()["points"], [])
        alfa_point = None
        self.client.post("/api/auth/login", json={"username": "alfa", "password": "secret123"})
        alfa_point = self.client.get("/api/knowledge").get_json()["points"][0]
        self.client.post("/api/auth/login", json={"username": "beta", "password": "secret123"})
        self.assertEqual(self.client.delete(
            f"/api/knowledge/{alfa_point['id']}").get_json()["deleted"], 0)

    def test_review_endpoint_carries_knowledge(self):
        session_id = make_court_session(self.directory, self.identity)
        review = self.client.get(f"/api/court/sessions/{session_id}/review")
        self.assertEqual(review.status_code, 200)
        knowledge = review.get_json()["knowledge"]
        self.assertTrue(any("第二十条" in item["article_ref"] for item in knowledge))
        self.assertTrue(all(item["text"] for item in knowledge))


class RewardMigrationTests(unittest.TestCase):
    def test_legacy_reward_table_accepts_knowledge_kind(self):
        """旧库（kind 只允许 qa/court）启动迁移后可写入 knowledge 奖励。"""
        directory = tempfile.mkdtemp()
        legacy = Path(directory) / "legacy.sqlite3"
        conn = sqlite3.connect(legacy)
        conn.executescript("""
            CREATE TABLE pet_reward_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL,
                client_id TEXT NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('qa','court')),
                gain INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(client_id, event_id)
            );
            INSERT INTO pet_reward_events (event_id,client_id,kind,gain,created_at)
                VALUES ('qa:old', 'u:legacy', 'qa', 2, '2026-01-01T00:00:00.000Z');
        """)
        conn.commit()
        conn.close()
        # 把旧库文件挪到 Database 期望的路径
        target = Path(directory) / "law.sqlite3"
        legacy.rename(target)

        with patch.dict(os.environ, {"LAW_DATA_DIR": directory}), \
                patch("legalweb.app.seed_sources"), \
                patch("legalweb.app.AUTH_RATE_MAX", 10_000):
            app = create_app()
        client = app.test_client()
        user = client.post("/api/auth/register", json={
            "username": "alfa", "password": "secret123"}).get_json()["user"]
        marked = client.post("/api/knowledge", json={
            "article_ref": "《刑法》第二十条", "title": "测试", "status": "mastered",
        }).get_json()
        self.assertTrue(marked["first_time"])   # 迁移后写入不再触发 CHECK 失败
        with Database(Path(directory)).connect() as db:
            kinds = [row["kind"] for row in db.execute(
                "SELECT kind FROM pet_reward_events ORDER BY id")]
        self.assertIn("qa", kinds)      # 旧数据保留
        self.assertIn("knowledge", kinds)


class AccountCoverageTests(unittest.TestCase):
    def test_export_and_delete_cover_knowledge(self):
        app_module._auth_attempts.clear()
        self.addCleanup(app_module._auth_attempts.clear)
        app, directory = make_app()
        client = app.test_client()
        # 第一个用户是管理员；被测的 alfa 是普通用户（注销不受管理员保护拦截）
        client.post("/api/auth/register", json={"username": "admin", "password": "secret123"})
        client.post("/api/auth/logout")
        user = client.post("/api/auth/register", json={
            "username": "alfa", "password": "secret123"}).get_json()["user"]
        identity = f"u:{user['id']}"
        client.post("/api/knowledge", json={
            "article_ref": "《刑法》第二十条", "title": "正当防卫", "status": "mastered"})
        exported = client.get("/api/account/export").get_json()
        self.assertEqual(len(exported["knowledge"]), 1)
        self.assertEqual(exported["knowledge"][0]["article_ref"], "《刑法》第二十条")

        client.post("/api/account/delete", json={"confirm": "DELETE", "password": "secret123"})
        with Database(directory).connect() as db:
            remaining = db.execute(
                "SELECT COUNT(*) AS n FROM knowledge_points WHERE owner_id=?",
                (identity,)).fetchone()["n"]
        self.assertEqual(remaining, 0)


if __name__ == "__main__":
    unittest.main()
