"""第三阶段回归：学习地图（知识点/事项/庭审的主题聚合导航）。"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from legalweb import app as app_module
from legalweb.app import create_app
from legalweb.database import Database, new_id, now_iso


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


class LearnMapTests(unittest.TestCase):
    def setUp(self):
        app_module._auth_attempts.clear()
        self.addCleanup(app_module._auth_attempts.clear)
        self.app, self.directory = make_app()
        self.client = self.app.test_client()
        self.user = self.client.post("/api/auth/register", json={
            "username": "alfa", "password": "secret123"}).get_json()["user"]
        self.identity = f"u:{self.user['id']}"

    def learn(self, client=None):
        return (client or self.client).get("/api/learn/map").get_json()

    def test_new_user_sees_case_tracks_only(self):
        data = self.learn()
        self.assertEqual(data["summary"]["mastered"], 0)
        self.assertEqual(data["summary"]["courts_done"], 0)
        self.assertEqual(data["summary"]["cases_total"], 3)
        themes = {theme["key"]: theme for theme in data["themes"]}
        # 内置三案均归入刑事主题轨道，均未完成
        criminal = themes["criminal"]
        self.assertEqual({item["case_id"] for item in criminal["cases"]},
                         {"haicheng", "kuaidizhan", "shenduzhuanzhang"})
        self.assertTrue(all(not item["completed"] for item in criminal["cases"]))
        self.assertTrue(all(item["laws_count"] >= 1 for item in criminal["cases"]))

    def test_aggregates_knowledge_matters_and_courts(self):
        self.client.post("/api/knowledge", json={
            "article_ref": "《刑法》第二十条", "title": "正当防卫",
            "case_id": "haicheng", "status": "mastered"})
        self.client.post("/api/knowledge", json={
            "article_ref": "《民法典》第一千零七十六条", "title": "协议离婚",
            "status": "review"})
        with Database(self.directory).connect() as db:
            db.execute("INSERT INTO matters (id,owner_id,title,intake,result,status,"
                       "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                       (new_id(), self.identity, "工资事项", "{}",
                        json.dumps({"report": {"issue_type": "劳动争议"}}),
                        "ready", now_iso(), now_iso()))
            db.execute("INSERT INTO court_sessions (id,case_id,owner_id,transcript,"
                       "deliberation,finished_at,created_at) VALUES (?,?,?,?,?,?,?)",
                       (new_id(), "haicheng", self.identity, "[]",
                        json.dumps({"label": "正当防卫"}), now_iso(), now_iso()))
        data = self.learn()
        self.assertEqual(data["summary"],
                         {"mastered": 1, "review": 1, "matters": 1,
                          "courts_done": 1, "cases_total": 3})
        themes = {theme["key"]: theme for theme in data["themes"]}
        self.assertEqual(themes["criminal"]["mastered"], 1)
        self.assertIn("haicheng", [c["case_id"] for c in themes["criminal"]["cases"]
                                   if c["completed"]])
        self.assertEqual(themes["family"]["review"], 1)
        self.assertEqual(themes["labor"]["matters"], 1)

    def test_isolation_and_anonymous(self):
        anonymous = self.app.test_client()
        self.assertEqual(anonymous.get("/api/learn/map").status_code, 401)
        self.client.post("/api/knowledge", json={
            "article_ref": "《刑法》第二十条", "title": "x",
            "case_id": "haicheng", "status": "mastered"})
        self.client.post("/api/auth/register", json={"username": "beta", "password": "secret123"})
        data = self.learn()
        self.assertEqual(data["summary"]["mastered"], 0)


if __name__ == "__main__":
    unittest.main()
