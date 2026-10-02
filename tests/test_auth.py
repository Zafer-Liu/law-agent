"""账户体系回归测试：注册/登录/会话、匿名拦截、权限分级与数据隔离。"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from legalweb import app as app_module
from legalweb.app import AUTH_COOKIE, create_app
from legalweb.database import Database, new_id, now_iso


class FakeLLM:
    configured = True

    def stream(self, *_args, **_kwargs):
        yield "delta", "依据现有资料，可以依法处理。[C1]"

    def complete(self, *_args, **_kwargs):
        return "用户咨询劳动争议处理方式"


def make_app(*, configured_llm=False):
    directory = tempfile.mkdtemp()
    patches = [patch.dict(os.environ, {"LAW_DATA_DIR": directory}),
               patch("legalweb.app.seed_sources"),
               patch("legalweb.app.AUTH_RATE_MAX", 10_000)]  # 默认放开限流
    if configured_llm:
        patches.append(patch("legalweb.app.LLMClient", return_value=FakeLLM()))
    for item in patches:
        item.start()
    try:
        app = create_app()
    finally:
        for item in reversed(patches):
            item.stop()
    return app, Path(directory)


def register(client, username, password="secret123", *, client_id=""):
    return client.post("/api/auth/register", json={
        "username": username, "password": password, "client_id": client_id})


def login(client, username, password, *, client_id=""):
    return client.post("/api/auth/login", json={
        "username": username, "password": password, "client_id": client_id})


def result_event(response):
    events = []
    for line in response.data.decode("utf-8").splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return next(event for event in reversed(events) if event["type"] == "result")


GROUNDED_HIT = {
    "id": 1,
    "source_title": "劳动合同法",
    "section": "第四十七条",
    "ordinal": 0,
    "text": "经济补偿按劳动者在本单位工作的年限计算。",
}


def grounded_ask(client, request_id):
    # 注意：SSE 响应体在补丁上下文内就要读完（result_event 内访问 response.data）
    with patch("legalweb.app.retrieve", return_value={
            "hits": [GROUNDED_HIT], "backend": "lexical+fts5", "degraded": True}):
        return result_event(client.post("/api/ask", json={
            "question": "劳动合同解除后经济补偿怎么算？",
            "request_id": request_id,
            "remember": False}))


class AuthFlowTests(unittest.TestCase):
    def test_register_login_logout_and_me(self):
        app, _ = make_app()
        client = app.test_client()
        response = register(client, "alfa")
        self.assertEqual(response.status_code, 200)
        user = response.get_json()["user"]
        self.assertEqual(user["username"], "alfa")
        self.assertTrue(user["is_admin"])  # 首个注册用户即管理员
        self.assertEqual(client.get("/api/auth/me").get_json()["user"]["username"], "alfa")

        self.assertEqual(client.post("/api/auth/logout").status_code, 200)
        self.assertIsNone(client.get("/api/auth/me").get_json()["user"])

        self.assertEqual(login(client, "alfa", "wrong-password").status_code, 401)
        self.assertEqual(login(client, "alfa", "secret123").status_code, 200)
        self.assertEqual(client.get("/api/auth/me").get_json()["user"]["username"], "alfa")

    def test_second_user_is_not_admin_and_usernames_are_case_insensitive(self):
        app, _ = make_app()
        client = app.test_client()
        self.assertTrue(register(client, "alfa").get_json()["user"]["is_admin"])
        self.assertFalse(register(client, "beta").get_json()["user"]["is_admin"])
        # COLLATE NOCASE：大小写不同仍视为同名
        self.assertEqual(register(client, "ALFA").status_code, 409)

    def test_register_validates_credentials(self):
        app, _ = make_app()
        client = app.test_client()
        self.assertEqual(register(client, "a").status_code, 400)                # 太短
        self.assertEqual(register(client, "带 空格").status_code, 400)           # 非法字符
        self.assertEqual(register(client, "alfa", "12345").status_code, 400)    # 密码太短
        self.assertEqual(register(client, "alfa", "x" * 129).status_code, 400)  # 密码太长
        self.assertEqual(register(client, "汉字用户名").status_code, 200)

    def test_session_token_survives_app_restart(self):
        """会话落在 SQLite：换一个应用实例（模拟 Railway 重新部署）仍有效。"""
        app, directory = make_app()
        client = app.test_client()
        register(client, "alfa")
        token = next(part.split("=", 1)[1].split(";")[0]
                     for part in register(client, "beta").headers.getlist("Set-Cookie")
                     if part.startswith(f"{AUTH_COOKIE}="))
        # 用同一数据目录重建应用（等价于重新部署后挂载同一卷）
        with patch.dict(os.environ, {"LAW_DATA_DIR": str(directory)}), \
                patch("legalweb.app.seed_sources"), \
                patch("legalweb.app.AUTH_RATE_MAX", 10_000):
            same_data_app = create_app()
        fresh_client = same_data_app.test_client()
        fresh_client.set_cookie(AUTH_COOKIE, token)
        self.assertEqual(fresh_client.get("/api/auth/me").get_json()["user"]["username"], "beta")


class AccessControlTests(unittest.TestCase):
    def test_anonymous_write_apis_are_rejected(self):
        app, _ = make_app(configured_llm=True)
        client = app.test_client()
        self.assertEqual(client.post("/api/ask", json={
            "question": "劳动合同解除后经济补偿怎么算？",
            "request_id": "request-anon"}).status_code, 401)
        self.assertEqual(client.get("/api/pet").status_code, 401)
        self.assertEqual(client.delete("/api/pet/memories").status_code, 401)
        self.assertEqual(client.get("/api/conversations").status_code, 401)
        self.assertEqual(client.post("/api/court/cases/haicheng/trial",
                                     json={"role": "juror"}).status_code, 401)

    def test_public_read_apis_stay_open(self):
        app, _ = make_app()
        client = app.test_client()
        self.assertEqual(client.get("/health").status_code, 200)
        self.assertEqual(client.get("/").status_code, 200)
        self.assertEqual(client.get("/api/config").status_code, 200)
        self.assertEqual(client.get("/api/kb").status_code, 200)
        self.assertEqual(client.get("/api/court/cases").status_code, 200)
        self.assertEqual(client.get("/api/auth/me").status_code, 200)

    def test_settings_logs_and_kb_upload_are_admin_only(self):
        app, _ = make_app()
        client = app.test_client()
        # 匿名：先挡 401
        self.assertEqual(client.get("/api/settings").status_code, 401)
        self.assertEqual(client.get("/api/logs").status_code, 401)
        self.assertEqual(client.post("/api/kb").status_code, 401)
        # 第一个注册用户是管理员
        register(client, "admin")
        self.assertEqual(client.get("/api/settings").status_code, 200)
        self.assertEqual(client.get("/api/logs").status_code, 200)
        # 第二个用户只有普通权限
        register(client, "beta")
        self.assertEqual(client.get("/api/settings").status_code, 403)
        self.assertEqual(client.post("/api/settings", json={"vector": False}).status_code, 403)
        self.assertEqual(client.get("/api/logs").status_code, 403)
        self.assertEqual(client.post("/api/kb").status_code, 403)


class DataIsolationTests(unittest.TestCase):
    def test_conversations_and_pet_are_isolated_between_users(self):
        app, _ = make_app(configured_llm=True)
        client = app.test_client()
        register(client, "alfa")
        result = grounded_ask(client, "request-alpha-1")
        conversation_id = result["conversation_id"]
        self.assertEqual(client.get("/api/pet").get_json()["exp"], 2)

        register(client, "beta")  # 会话切到 beta
        self.assertEqual(client.get("/api/conversations").get_json()["conversations"], [])
        self.assertEqual(client.get("/api/pet").get_json()["exp"], 0)
        self.assertIsNone(client.get(
            f"/api/conversations/{conversation_id}").get_json()["conversation"])

    def test_anonymous_data_is_claimed_on_register(self):
        app, directory = make_app()
        client = app.test_client()
        anon = "browser-anon-1"
        stamp = now_iso()
        with Database(directory).connect() as db:
            db.execute("INSERT INTO conversations(id,title,owner_id,created_at)"
                       " VALUES ('conv-anon','匿名期对话',?,?)", (anon, stamp))
            db.execute("INSERT INTO messages(id,conversation_id,role,content,payload,created_at)"
                       " VALUES (?,?,?,?,?,?)",
                       (new_id(), "conv-anon", "user", "匿名提问", "{}", stamp))
            db.execute("INSERT INTO pet_progress (client_id,exp,updated_at)"
                       " VALUES (?,35,?)", (anon, stamp))
            db.execute("INSERT INTO pet_memories (client_id,content,created_at)"
                       " VALUES (?,?,?)", (anon, "匿名期记忆", stamp))
        register(client, "alfa", client_id=anon)
        listing = client.get("/api/conversations").get_json()["conversations"]
        self.assertEqual([item["id"] for item in listing], ["conv-anon"])
        pet = client.get("/api/pet").get_json()
        self.assertEqual(pet["exp"], 35)
        self.assertEqual(pet["memory_count"], 1)

    def test_anonymous_data_is_claimed_on_login(self):
        app, directory = make_app()
        client = app.test_client()
        register(client, "alfa")  # 注册时不带 client_id
        client.post("/api/auth/logout")
        anon = "browser-anon-2"
        with Database(directory).connect() as db:
            db.execute("INSERT INTO pet_progress (client_id,exp,updated_at)"
                       " VALUES (?,20,?)", (anon, now_iso()))
        login(client, "alfa", "secret123", client_id=anon)
        self.assertEqual(client.get("/api/pet").get_json()["exp"], 20)

    def test_claim_skips_rows_that_would_collide(self):
        """目标账户已有同键数据时，匿名期数据不覆盖、并入被跳过。"""
        app, directory = make_app()
        client = app.test_client()
        register(client, "alfa")
        identity = f"u:{client.get('/api/auth/me').get_json()['user']['id']}"
        anon = "browser-anon-3"
        with Database(directory).connect() as db:
            db.execute("INSERT INTO pet_progress (client_id,exp,updated_at)"
                       " VALUES (?,5,?)", (identity, now_iso()))
            db.execute("INSERT INTO pet_progress (client_id,exp,updated_at)"
                       " VALUES (?,99,?)", (anon, now_iso()))
        client.post("/api/auth/logout")
        login(client, "alfa", "secret123", client_id=anon)
        # 匿名期的 99 点不并入：目标已有进度行（唯一键），保留 5 点
        self.assertEqual(client.get("/api/pet").get_json()["exp"], 5)


class RateLimitTests(unittest.TestCase):
    def test_login_brute_force_is_throttled(self):
        with patch.dict(os.environ, {"LAW_DATA_DIR": tempfile.mkdtemp()}), \
                patch("legalweb.app.seed_sources"):
            app = create_app()  # 不放开限流，验证真实阈值
        self.addCleanup(app_module._auth_attempts.clear)
        client = app.test_client()
        statuses = [login(client, "ghost", "wrong-pass").status_code
                    for _ in range(35)]
        self.assertEqual(statuses[0], 401)
        self.assertIn(429, statuses)
        self.assertEqual(statuses[-1], 429)


if __name__ == "__main__":
    unittest.main()
