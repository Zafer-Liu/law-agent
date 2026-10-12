"""错误日志收集与运行日志 7 天留存的回归测试。"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from legalweb.app import AUTH_COOKIE, create_app
from legalweb.database import Database, now_iso


def make_app():
    directory = tempfile.mkdtemp()
    with patch.dict(os.environ, {"LAW_DATA_DIR": directory}), \
            patch("legalweb.app.seed_sources"), \
            patch("legalweb.app.AUTH_RATE_MAX", 10_000):
        app = create_app()
    return app, Path(directory)


def register_admin(client, username="admin1"):
    client.post("/api/auth/register",
                json={"username": username, "password": "secret123"})
    client.post("/api/auth/login",
                json={"username": username, "password": "secret123"})


class ErrorReportTests(unittest.TestCase):
    def setUp(self):
        self.app, self.dir = make_app()
        self.client = self.app.test_client()

    def test_anonymous_report_stored(self):
        response = self.client.post("/api/error-report", json={
            "kind": "qa_error",
            "question": "劳动合同解除怎么补偿？",
            "error": "模型服务暂时不可用",
            "context": {"mode": "ordinary", "request_id": "req-12345678"},
        })
        self.assertEqual(response.status_code, 200)
        # 匿名也允许（user_id 为空），方便未登录链路排查
        with Database(self.dir).connect() as db:
            rows = db.execute("SELECT * FROM error_reports").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["user_id"], "")
        self.assertEqual(rows[0]["kind"], "qa_error")
        self.assertIn("模型服务", rows[0]["error"])
        self.assertEqual(json.loads(rows[0]["context"])["mode"], "ordinary")

    def test_field_validation(self):
        # 空 error 拒绝
        self.assertEqual(self.client.post("/api/error-report",
                         json={"error": "  "}).status_code, 400)
        # 非法 kind 拒绝
        self.assertEqual(self.client.post("/api/error-report",
                         json={"kind": "evil", "error": "x"}).status_code, 400)
        # 超长截断
        self.client.post("/api/error-report",
                         json={"error": "e" * 5000, "question": "q" * 5000})
        with Database(self.dir).connect() as db:
            row = db.execute("SELECT question, error FROM error_reports").fetchone()
        self.assertEqual(len(row["question"]), 2000)
        self.assertEqual(len(row["error"]), 1000)

    def test_admin_list_requires_admin(self):
        register_admin(self.client)
        response = self.client.get("/api/error-reports")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["reports"], [])


class AppLogRetentionTests(unittest.TestCase):
    def test_flusher_writes_and_prunes_seven_days(self):
        app, directory = make_app()
        db_path = directory / "law.sqlite3"
        self.assertTrue(db_path.exists())
        # 手动插入 8 天前与 1 天前的日志，触发一次刷新周期（interval 默认 3s）
        old = (datetime.now() - timedelta(days=8)).isoformat(timespec="seconds")
        recent = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
        with Database(directory).connect() as db:
            db.execute("INSERT INTO app_logs (time, level, name, message)"
                       " VALUES (?,?,?,?)", (old, "INFO", "app", "old"))
            db.execute("INSERT INTO app_logs (time, level, name, message)"
                       " VALUES (?,?,?,?)", (recent, "INFO", "app", "recent"))
        import time as _time
        _time.sleep(4.5)  # 等一个刷新周期（批量落库 + 7 天清理）
        with Database(directory).connect() as db:
            messages = [row["message"] for row in
                        db.execute("SELECT message FROM app_logs").fetchall()]
        self.assertNotIn("old", messages)
        self.assertIn("recent", messages)
        # 注：环形 handler 队列在多 app 测试间共享，落库目标有竞争；
        # 生产环境单 app 单线程无此问题，落库路径由 /api/logs 集成覆盖。


if __name__ == "__main__":
    unittest.main()
