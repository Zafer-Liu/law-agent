"""獬豸桌宠成长进度与记忆库 API 的回归测试。"""

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from legalweb.app import (
    apply_pet_reward,
    create_app,
    load_pet_rewards,
    pet_forget_old,
    pet_memory_content,
)
from legalweb.database import Database, now_iso


class FakeLLM:
    configured = True

    def stream(self, *_args, **_kwargs):
        yield "delta", "依据现有资料，可以依法处理。[C1]"

    def complete(self, *_args, **_kwargs):
        return "用户咨询劳动争议处理方式"


def make_client(*, configured_llm=False):
    directory = tempfile.mkdtemp()
    patches = [patch.dict(os.environ, {"LAW_DATA_DIR": directory}),
               patch("legalweb.app.seed_sources"),
               patch("legalweb.app.AUTH_RATE_MAX", 10_000)]  # 套件内注册频繁，放开限流
    if configured_llm:
        patches.append(patch("legalweb.app.LLMClient", return_value=FakeLLM()))
    for item in patches:
        item.start()
    try:
        app = create_app()
    finally:
        for item in reversed(patches):
            item.stop()
    return app.test_client(), Database(Path(directory))


def result_event(response):
    events = []
    for line in response.data.decode("utf-8").splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return next(event for event in reversed(events) if event["type"] == "result")


def register_user(client, username="alfa", *, password="secret123"):
    """注册并保持登录（test_client 自动携带会话 Cookie）；返回账户身份键。"""
    response = client.post("/api/auth/register", json={
        "username": username, "password": password})
    assert response.status_code == 200, response.get_json()
    return f"u:{response.get_json()['user']['id']}"


GROUNDED_HIT = {
    "id": 1,
    "source_title": "劳动合同法",
    "section": "第四十七条",
    "ordinal": 0,
    "text": "经济补偿按劳动者在本单位工作的年限计算。",
}


class PetProgressTests(unittest.TestCase):
    def test_new_user_gets_zero_cub_state(self):
        client, _ = make_client()
        register_user(client)
        state = client.get("/api/pet").get_json()
        self.assertEqual(state["exp"], 0)
        self.assertEqual(state["stage"], "cub")
        self.assertEqual(state["stage_name"], "幼獬")
        self.assertEqual(state["next_stage_name"], "启蒙")
        self.assertEqual(state["next_need"], 30)
        self.assertEqual(state["stage_appearance"], "幼角初生，浅色鬃毛")
        self.assertEqual(state["qa_count"], 0)
        self.assertEqual(len(state["stages"]), 6)
        self.assertEqual(state["recent_rewards"], [])

    def test_stage_milestones_and_progress(self):
        client, database = make_client()
        identity = register_user(client)
        with database.connect() as db:
            db.execute("INSERT INTO pet_progress (client_id,exp,updated_at)"
                       " VALUES (?,35,?)", (identity, now_iso()))
        state = client.get("/api/pet").get_json()
        # 35 经验：过启蒙(30)，未到明辨(100)
        self.assertEqual(state["stage"], "dawn")
        self.assertEqual(state["next_stage_name"], "明辨")
        self.assertEqual(state["next_need"], 65)
        # (35-30)/(100-30) ≈ 7%
        self.assertEqual(state["progress"], 7)

    def test_reward_events_are_idempotent(self):
        _, database = make_client()
        with database.connect() as db:
            self.assertTrue(apply_pet_reward(
                db, "u:user-a", "qa", 2, "qa:answer-1"))
            self.assertFalse(apply_pet_reward(
                db, "u:user-a", "qa", 2, "qa:answer-1"))
            self.assertTrue(apply_pet_reward(
                db, "u:user-a", "court", 10, "court:session-1"))
            row = db.execute("SELECT exp,qa_count,court_count FROM pet_progress"
                             " WHERE client_id='u:user-a'").fetchone()
        self.assertEqual(tuple(row), (12, 1, 1))

    def test_same_event_id_is_scoped_to_user(self):
        _, database = make_client()
        with database.connect() as db:
            self.assertTrue(apply_pet_reward(
                db, "u:user-a", "qa", 2, "qa:shared-event"))
            self.assertTrue(apply_pet_reward(
                db, "u:user-b", "qa", 2, "qa:shared-event"))
            rows = db.execute(
                "SELECT client_id,exp FROM pet_progress ORDER BY client_id").fetchall()
        self.assertEqual([tuple(row) for row in rows],
                         [("u:user-a", 2), ("u:user-b", 2)])

    def test_anonymous_access_requires_login(self):
        client, _ = make_client()
        self.assertEqual(client.get("/api/pet").status_code, 401)
        # client_id 查询参数已不再作为身份凭据
        self.assertEqual(client.get(
            "/api/pet?client_id=browser-0001").status_code, 401)

    def test_recent_rewards_are_scoped_and_newest_first(self):
        _, database = make_client()
        with database.connect() as db:
            apply_pet_reward(db, "u:user-a", "qa", 2, "qa:one")
            apply_pet_reward(db, "u:user-a", "court", 10, "court:two")
            apply_pet_reward(db, "u:user-b", "qa", 2, "qa:other")
            rows = load_pet_rewards(db, "u:user-a")
        self.assertEqual([(row["kind"], row["gain"]) for row in rows],
                         [("court", 10), ("qa", 2)])


class PetMemoryTests(unittest.TestCase):
    def test_new_user_gets_empty_memory_bank(self):
        client, _ = make_client()
        register_user(client)
        state = client.get("/api/pet").get_json()
        self.assertEqual(state["memories"], [])
        self.assertEqual(state["memory_count"], 0)

    def test_memories_returned_newest_first_with_count(self):
        client, database = make_client()
        identity = register_user(client)
        with database.connect() as db:
            for i in range(3):
                db.execute("INSERT INTO pet_memories (client_id,content,created_at)"
                           " VALUES (?,?,?)", (identity, f"记忆{i}", now_iso()))
        state = client.get("/api/pet").get_json()
        self.assertEqual(state["memory_count"], 3)
        self.assertEqual([m["content"] for m in state["memories"]],
                         ["记忆2", "记忆1", "记忆0"])
        self.assertTrue(all(isinstance(m["id"], int) for m in state["memories"]))

    def test_memory_bank_can_be_cleared(self):
        client, database = make_client()
        identity = register_user(client)
        with database.connect() as db:
            db.execute("INSERT INTO pet_memories (client_id,content,created_at)"
                       " VALUES (?,?,?)", (identity, "一条记忆", now_iso()))
        response = client.delete("/api/pet/memories")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["deleted"], 1)
        state = client.get("/api/pet").get_json()
        self.assertEqual(state["memory_count"], 0)

    def test_single_memory_delete_is_scoped_to_user(self):
        client, database = make_client()
        other = register_user(client, "beta")
        mine = register_user(client, "alfa")  # 当前会话切到 alfa
        with database.connect() as db:
            own_id = db.execute(
                "INSERT INTO pet_memories (client_id,content,created_at) VALUES (?,?,?)",
                (mine, "自己的记忆", now_iso())).lastrowid
            other_id = db.execute(
                "INSERT INTO pet_memories (client_id,content,created_at) VALUES (?,?,?)",
                (other, "别人的记忆", now_iso())).lastrowid
        denied = client.delete(f"/api/pet/memories/{other_id}")
        deleted = client.delete(f"/api/pet/memories/{own_id}")
        self.assertEqual(denied.get_json()["deleted"], 0)
        self.assertEqual(deleted.get_json()["deleted"], 1)
        with database.connect() as db:
            remaining = db.execute(
                "SELECT client_id FROM pet_memories ORDER BY id").fetchall()
        self.assertEqual([row["client_id"] for row in remaining], [other])

    def test_memory_event_deduplication_is_scoped_to_user(self):
        _, database = make_client()
        with database.connect() as db:
            first = db.execute(
                "INSERT OR IGNORE INTO pet_memories"
                " (client_id,content,source_event_id,created_at) VALUES (?,?,?,?)",
                ("u:user-a", "记忆一", "qa:shared", now_iso())).rowcount
            second = db.execute(
                "INSERT OR IGNORE INTO pet_memories"
                " (client_id,content,source_event_id,created_at) VALUES (?,?,?,?)",
                ("u:user-b", "记忆二", "qa:shared", now_iso())).rowcount
        self.assertEqual((first, second), (1, 1))

    def test_forget_old_keeps_only_recent_limit(self):
        _, database = make_client()
        with database.connect() as db:
            for i in range(55):
                db.execute("INSERT INTO pet_memories (client_id,content,created_at)"
                           " VALUES (?,?,?)", ("u:user-a", f"记忆{i}", now_iso()))
            pet_forget_old(db, "u:user-a")
            rows = db.execute("SELECT content FROM pet_memories WHERE client_id=?"
                              " ORDER BY id DESC", ("u:user-a",)).fetchall()
        self.assertEqual(len(rows), 50)
        self.assertEqual(rows[0]["content"], "记忆54")   # 最新的留下
        self.assertEqual(rows[-1]["content"], "记忆5")   # 最旧的被裁掉

    def test_memory_content_uses_model_output(self):
        captured = {}

        def fake_complete(messages, temperature, max_tokens):
            captured["messages"] = messages
            return ' “用户咨询劳动合同纠纷，关注赔偿标准” \n'

        content = pet_memory_content("被公司辞退怎么赔？", "长回答…", fake_complete)
        self.assertEqual(content, "用户咨询劳动合同纠纷，关注赔偿标准")
        self.assertIn("獬豸", captured["messages"][0]["content"])

    def test_memory_content_falls_back_when_model_fails(self):
        def broken_complete(*args, **kwargs):
            raise RuntimeError("model down")

        content = pet_memory_content("被公司辞退怎么赔？", "长回答…", broken_complete)
        self.assertEqual(content, "曾就「被公司辞退怎么赔？」咨询法律问题")

    def test_memory_content_caps_length(self):
        content = pet_memory_content("问" * 200, "答", lambda *a, **k: "记" * 500)
        self.assertLessEqual(len(content), 60)


class PetAskIntegrationTests(unittest.TestCase):
    def grounded_ask(self, client, **overrides):
        payload = {
            "question": "劳动合同解除后经济补偿怎么算？",
            "request_id": "request-0001",
            "remember": False,
            **overrides,
        }
        with patch("legalweb.app.retrieve", return_value={
                "hits": [GROUNDED_HIT], "backend": "lexical+fts5", "degraded": True}):
            return result_event(client.post("/api/ask", json=payload))

    def test_grounded_retry_rewards_only_once(self):
        client, database = make_client(configured_llm=True)
        register_user(client)
        first = self.grounded_ask(client)
        second = self.grounded_ask(client)
        self.assertEqual(first["pet"]["gained"], 2)
        self.assertEqual(second["pet"]["gained"], 0)
        self.assertEqual(second["pet"]["exp"], 2)
        with database.connect() as db:
            count = db.execute("SELECT COUNT(*) AS n FROM pet_reward_events").fetchone()["n"]
        self.assertEqual(count, 1)

    def test_invalid_request_id_is_rejected(self):
        client, _ = make_client(configured_llm=True)
        # 参数校验先于登录检查：匿名请求同样得到 400
        response = client.post("/api/ask", json={
            "question": "劳动合同解除后经济补偿怎么算？",
            "request_id": "bad request id",
        })
        self.assertEqual(response.status_code, 400)

    def test_memory_requires_explicit_opt_in(self):
        client, database = make_client(configured_llm=True)
        register_user(client)
        self.grounded_ask(client, request_id="request-no-memory", remember=False)
        with patch("legalweb.app.submit_pet_memory",
                   side_effect=lambda work: (work(), True)[1]):
            self.grounded_ask(client, request_id="request-with-memory", remember=True)
        with database.connect() as db:
            rows = db.execute("SELECT source_event_id FROM pet_memories").fetchall()
        self.assertEqual([row["source_event_id"] for row in rows],
                         ["qa:request-with-memory"])

    def test_ungrounded_answer_does_not_reward_pet(self):
        client, database = make_client(configured_llm=True)
        register_user(client)
        with patch("legalweb.app.retrieve", return_value={
                "hits": [], "backend": "lexical+fts5", "degraded": True}):
            result = result_event(client.post("/api/ask", json={
                "question": "一个无法检索到依据的问题",
                "request_id": "request-ungrounded",
                "remember": True,
            }))
        self.assertFalse(result["grounded"])
        # 检索 0 命中也照常生成回答；无引用不得发放宠物奖励（pet 为 None）
        self.assertIsNone(result.get("pet"))
        with database.connect() as db:
            reward_count = db.execute("SELECT COUNT(*) AS n FROM pet_reward_events").fetchone()["n"]
            memory_count = db.execute("SELECT COUNT(*) AS n FROM pet_memories").fetchone()["n"]
        self.assertEqual((reward_count, memory_count), (0, 0))


class PetDatabaseMigrationTests(unittest.TestCase):
    def test_global_reward_primary_key_migrates_without_data_loss(self):
        root = Path(tempfile.mkdtemp())
        path = root / "law.sqlite3"
        db = sqlite3.connect(path)
        try:
            db.execute(
                "CREATE TABLE pet_reward_events ("
                "event_id TEXT PRIMARY KEY, client_id TEXT NOT NULL,"
                "kind TEXT NOT NULL, gain INTEGER NOT NULL, created_at TEXT NOT NULL)")
            db.execute(
                "INSERT INTO pet_reward_events VALUES (?,?,?,?,?)",
                ("qa:legacy", "u:user-a", "qa", 2, now_iso()))
            db.commit()
        finally:
            db.close()
        database = Database(root)
        with database.connect() as db:
            columns = {row["name"]: row for row in db.execute(
                "PRAGMA table_info(pet_reward_events)").fetchall()}
            legacy = db.execute(
                "SELECT client_id,event_id FROM pet_reward_events").fetchone()
            inserted = apply_pet_reward(
                db, "u:user-b", "qa", 2, "qa:legacy")
        self.assertEqual(columns["event_id"]["pk"], 0)
        self.assertEqual(tuple(legacy), ("u:user-a", "qa:legacy"))
        self.assertTrue(inserted)


if __name__ == "__main__":
    unittest.main()
