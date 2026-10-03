"""第一阶段（可信和可测）回归测试：回答反馈、事件埋点、来源效力标识、账户数据控制。"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from legalweb import app as app_module
from legalweb.app import AUTH_COOKIE, create_app
from legalweb.database import Database, new_id, now_iso
from legalweb.source_meta import build_validity, parse_source_meta


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
               patch("legalweb.app.AUTH_RATE_MAX", 10_000)]
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


def register(client, username, password="secret123"):
    return client.post("/api/auth/register", json={
        "username": username, "password": password})


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


def grounded_ask(client, request_id="request-fixed-1", **hit_extra):
    hit = {**GROUNDED_HIT, **hit_extra}
    with patch("legalweb.app.retrieve", return_value={
            "hits": [hit], "backend": "lexical+fts5", "degraded": True}):
        return result_event(client.post("/api/ask", json={
            "question": "劳动合同解除后经济补偿怎么算？",
            "request_id": request_id,
            "remember": False}))


class AnswerFeedbackTests(unittest.TestCase):
    def setUp(self):
        app_module._auth_attempts.clear()  # 前序套件可能已灌满认证限流桶
        self.addCleanup(app_module._auth_attempts.clear)
        self.app, self.directory = make_app(configured_llm=True)
        self.client = self.app.test_client()
        register(self.client, "alfa")

    def test_ask_result_carries_message_id_for_feedback(self):
        result = grounded_ask(self.client)
        self.assertTrue(result["message_id"])

    def test_feedback_roundtrip_and_update(self):
        message_id = grounded_ask(self.client)["message_id"]
        payload = {"message_id": message_id, "rating": "unhelpful",
                   "reason": "outdated", "comment": "引用的是旧版条文"}
        self.assertEqual(self.client.post("/api/feedback", json=payload).status_code, 200)
        # 改判为有帮助：upsert 覆盖，不产生第二条
        self.assertEqual(self.client.post(
            "/api/feedback", json={"message_id": message_id, "rating": "helpful"}).status_code, 200)
        with Database(self.directory).connect() as db:
            rows = db.execute("SELECT rating,reason FROM answer_feedback").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["rating"], "helpful")

    def test_feedback_validation_and_isolation(self):
        message_id = grounded_ask(self.client)["message_id"]
        self.assertEqual(self.client.post("/api/feedback", json={
            "message_id": message_id, "rating": "meh"}).status_code, 400)
        self.assertEqual(self.client.post("/api/feedback", json={
            "message_id": message_id, "rating": "helpful", "reason": "weird"}).status_code, 400)
        # 别的用户不能评价alfa的回答
        register(self.client, "beta")
        self.assertEqual(self.client.post("/api/feedback", json={
            "message_id": message_id, "rating": "helpful"}).status_code, 404)
        anonymous = self.app.test_client()
        self.assertEqual(anonymous.post("/api/feedback", json={
            "message_id": message_id, "rating": "helpful"}).status_code, 401)

    def test_feedback_visible_in_conversation_history(self):
        message_id = grounded_ask(self.client)["message_id"]
        self.client.post("/api/feedback", json={
            "message_id": message_id, "rating": "helpful"})
        conversation_id = self.client.get("/api/conversations").get_json()[
            "conversations"][0]["id"]
        messages = self.client.get(
            f"/api/conversations/{conversation_id}").get_json()["messages"]
        assistant = next(m for m in messages if m["role"] == "assistant")
        self.assertEqual(assistant["feedback"], "helpful")

    def test_admin_sees_feedback_with_question_context(self):
        message_id = grounded_ask(self.client)["message_id"]
        self.client.post("/api/feedback", json={
            "message_id": message_id, "rating": "unhelpful",
            "reason": "irrelevant", "comment": "答非所问"})
        register(self.client, "beta")
        self.assertEqual(self.client.get("/api/feedback").status_code, 403)
        # beta 注册后当前会话是 beta；切回管理员
        self.client.post("/api/auth/login", json={
            "username": "alfa", "password": "secret123"})
        data = self.client.get("/api/feedback").get_json()
        self.assertEqual(data["stats"], {"unhelpful": 1})
        self.assertEqual(data["reasons"], {"irrelevant": 1})
        item = data["recent"][0]
        self.assertIn("经济补偿", item["question"])
        self.assertTrue(item["answer"])


class ProductEventTests(unittest.TestCase):
    def setUp(self):
        app_module._auth_attempts.clear()
        self.addCleanup(app_module._auth_attempts.clear)
        self.app, _directory = make_app()
        self.client = self.app.test_client()
        register(self.client, "alfa")

    def test_events_whitelist_and_prop_sanitization(self):
        response = self.client.post("/api/events", json={"events": [
            {"name": "qa_ask", "props": {"new_conversation": True,
                                         "note": "x" * 500,
                                         "bad": ["list"]}},
            {"name": "arbitrary_things", "props": {"x": 1}},
        ]})
        self.assertEqual(response.get_json()["stored"], 1)

    def test_events_require_login_and_batch_limit(self):
        anonymous = self.app.test_client()
        self.assertEqual(anonymous.post("/api/events", json={
            "events": [{"name": "qa_ask"}]}).status_code, 401)
        self.assertEqual(self.client.post("/api/events", json={}).status_code, 400)
        # 超量批次只保留前 20 条
        batch = [{"name": "qa_ask"} for _ in range(30)]
        self.assertEqual(self.client.post(
            "/api/events", json={"events": batch}).get_json()["stored"], 20)

    def test_admin_summary_counts_events_and_users(self):
        register(self.client, "beta")
        for _ in range(3):
            self.client.post("/api/events", json={
                "events": [{"name": "court_start", "props": {"role": "juror"}}]})
        self.assertEqual(self.client.get("/api/events/summary").status_code, 403)
        self.client.post("/api/auth/login", json={
            "username": "alfa", "password": "secret123"})
        events = self.client.get("/api/events/summary").get_json()["events"]
        row = next(item for item in events if item["name"] == "court_start")
        self.assertEqual(row["count"], 3)
        self.assertEqual(row["users"], 1)


class SourceValidityTests(unittest.TestCase):
    LAW_DIR = Path("assset/law")

    def read_head(self, pattern, size=1500):
        path = next(self.LAW_DIR.glob(pattern))
        return path.read_text(encoding="utf-8", errors="ignore")[:size]

    def test_parsed_metadata_from_bundled_civil_code(self):
        meta = parse_source_meta(
            self.read_head("中华人民共和国民法典*.txt"), "法条 · 中华人民共和国民法典")
        self.assertEqual(meta["status"], "current")
        self.assertEqual(meta["published"], "2020.05.28")
        self.assertEqual(meta["effective"], "2021.01.01")
        self.assertEqual(meta["authority"], "全国人民代表大会")
        self.assertEqual(meta["region"], "全国")

    def test_repealed_regulation_carries_supersession(self):
        meta = parse_source_meta(
            self.read_head("借款合同条例*.txt"), "法条 · 借款合同条例")
        self.assertEqual(meta["status"], "repealed")
        self.assertIn("废止", meta["superseded_by"])
        self.assertIn("已废止或失效", build_validity(json.dumps(meta))["note"])

    def test_builtin_override_for_law_without_header(self):
        meta = parse_source_meta("", "法条 · 中华人民共和国刑法(2023修正)")
        self.assertEqual(meta["status"], "current")
        self.assertEqual(meta["effective"], "2024.03.01")

    def test_unknown_source_is_never_guessed(self):
        self.assertEqual(parse_source_meta("一些没有任何著录信息的正文。", "随便的资料"), {})
        validity = build_validity("")
        self.assertEqual(validity["status"], "unknown")
        self.assertTrue(validity["note"])

    def test_online_timeliness_maps_to_validity(self):
        self.assertEqual(build_validity(timeliness="现行有效")["status"], "current")
        self.assertEqual(build_validity(timeliness="已被修改")["status"], "amended")
        self.assertEqual(build_validity(timeliness="废止或失效")["status"], "repealed")

    def test_citations_and_kb_carry_validity(self):
        app, _directory = make_app()
        client = app.test_client()
        register(client, "alfa")
        kb = client.get("/api/kb").get_json()["sources"]
        self.assertTrue(all("validity" in source for source in kb))

    def test_ask_citations_include_validity(self):
        app, _directory = make_app(configured_llm=True)
        client = app.test_client()
        register(client, "alfa")
        meta = {"status": "current", "published": "2020.05.28",
                "effective": "2021.01.01", "authority": "全国人民代表大会"}
        result = grounded_ask(client, source_meta=json.dumps(meta, ensure_ascii=False),
                              source_indexed_at="2026-09-28T10:00:00.000Z")
        validity = result["citations"][0]["validity"]
        self.assertEqual(validity["status"], "current")
        self.assertEqual(validity["effective"], "2021.01.01")
        self.assertEqual(validity["indexed_at"], "2026-09-28")


class AccountControlTests(unittest.TestCase):
    def setUp(self):
        app_module._auth_attempts.clear()
        self.addCleanup(app_module._auth_attempts.clear)
        self.app, self.directory = make_app(configured_llm=True)
        self.client = self.app.test_client()
        register(self.client, "alfa")

    def test_password_change_revokes_other_sessions(self):
        second = self.app.test_client()
        register(self.client, "beta")
        self.client.post("/api/auth/logout")
        # alfa 在新客户端登录两次（模拟两台设备）
        self.client.post("/api/auth/login", json={
            "username": "alfa", "password": "secret123"})
        second.post("/api/auth/login", json={
            "username": "alfa", "password": "secret123"})
        self.assertEqual(second.get("/api/auth/me").get_json()["user"]["username"], "alfa")

        wrong = self.client.post("/api/auth/password", json={
            "current_password": "bad-pass", "new_password": "better123"})
        self.assertEqual(wrong.status_code, 403)
        ok = self.client.post("/api/auth/password", json={
            "current_password": "secret123", "new_password": "better123"})
        self.assertEqual(ok.status_code, 200)
        # 当前会话保留，另一台设备被吊销
        self.assertEqual(self.client.get("/api/auth/me").get_json()["user"]["username"], "alfa")
        self.assertIsNone(second.get("/api/auth/me").get_json()["user"])
        # 旧密码不能登录，新密码可以
        self.client.post("/api/auth/logout")
        self.assertEqual(self.client.post("/api/auth/login", json={
            "username": "alfa", "password": "secret123"}).status_code, 401)
        self.assertEqual(self.client.post("/api/auth/login", json={
            "username": "alfa", "password": "better123"}).status_code, 200)

    def test_export_contains_all_user_data(self):
        grounded_ask(self.client)
        response = self.client.get("/api/account/export")
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response.headers["Content-Disposition"])
        data = response.get_json()
        self.assertEqual(len(data["conversations"]), 1)
        self.assertEqual(len(data["conversations"][0]["messages"]), 2)
        self.assertIn("pet", data)
        self.assertIn("feedback", data)
        # 导出是账户隔离的：匿名不可用
        self.assertEqual(self.app.test_client().get(
            "/api/account/export").status_code, 401)

    def test_account_delete_requires_confirmation_and_purges_data(self):
        result = grounded_ask(self.client, "request-del-1")
        self.client.post("/api/feedback", json={
            "message_id": result["message_id"], "rating": "helpful"})
        # 首个用户是管理员：没有其他管理员时不允许注销
        denied = self.client.post("/api/account/delete", json={
            "confirm": "DELETE", "password": "secret123"})
        self.assertEqual(denied.status_code, 409)
        # 确认口令 / 密码校验
        beta = register(self.client, "beta").get_json()["user"]
        beta_identity = f"u:{beta['id']}"
        self.assertEqual(self.client.post("/api/account/delete", json={
            "confirm": "yes", "password": "secret123"}).status_code, 400)
        self.assertEqual(self.client.post("/api/account/delete", json={
            "confirm": "DELETE", "password": "wrong-pass"}).status_code, 403)
        # beta 造一份数据后注销：名下数据全部清空，alfa 的保留
        grounded_ask(self.client, "request-del-2")
        self.client.post("/api/events", json={"events": [{"name": "qa_ask"}]})
        ok = self.client.post("/api/account/delete", json={
            "confirm": "DELETE", "password": "secret123"})
        self.assertEqual(ok.status_code, 200)
        self.assertIsNone(self.client.get("/api/auth/me").get_json()["user"])
        with Database(self.directory).connect() as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) AS n FROM users WHERE id=?",
                (beta["id"],)).fetchone()["n"], 0)
            for table, column in (("conversations", "owner_id"),
                                  ("court_sessions", "owner_id"),
                                  ("pet_progress", "client_id"),
                                  ("pet_memories", "client_id"),
                                  ("pet_reward_events", "client_id")):
                self.assertEqual(db.execute(
                    f"SELECT COUNT(*) AS n FROM {table} WHERE {column}=?",
                    (beta_identity,)).fetchone()["n"], 0, table)
            for table in ("answer_feedback", "events"):
                self.assertEqual(db.execute(
                    f"SELECT COUNT(*) AS n FROM {table} WHERE user_id=?",
                    (beta["id"],)).fetchone()["n"], 0, table)
            alfa = db.execute("SELECT id FROM users WHERE username='alfa'").fetchone()
            self.assertEqual(db.execute(
                "SELECT COUNT(*) AS n FROM conversations WHERE owner_id=?",
                (f"u:{alfa['id']}",)).fetchone()["n"], 1)
        # 注销后 Cookie 失效
        self.assertEqual(self.client.get("/api/pet").status_code, 401)


if __name__ == "__main__":
    unittest.main()
