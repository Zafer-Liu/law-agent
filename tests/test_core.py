"""Fast regression tests for reliability boundaries that do not need an LLM."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.exceptions import BadRequest

from legalweb.app import create_app, text_value
from legalweb.courtroom.agents import ROLES
from legalweb.courtroom.cases import CASES
from legalweb.courtroom.engine import PLAYER_ROLES, TrialEngine
from legalweb.database import Database, new_id, now_iso
from legalweb.documents import parse_document, split_sections
from legalweb.llm import LLMClient
from legalweb.qa import Evidence, sanitize_citations
from legalweb.rag import retrieve


class FakeResponse:
    def __init__(self, lines):
        self.lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def raise_for_status(self):
        return None

    def iter_lines(self):
        yield from self.lines


def sse_chunk(content):
    return ("data: " + json.dumps({"choices": [{"delta": {"content": content}}]})).encode()


class CoreTests(unittest.TestCase):
    class _CourtLLM:
        def complete(self, _messages, **_kwargs):
            return "依据卷宗证据，本方完成本轮庭审发言。"

    class _NoEmbedding:
        configured = False

    def test_ids_are_unique_in_a_tight_loop(self):
        ids = {new_id() for _ in range(10_000)}
        self.assertEqual(len(ids), 10_000)

    def test_json_text_rejects_non_string_values(self):
        from flask import Flask

        with Flask(__name__).test_request_context():
            with self.assertRaises(BadRequest):
                text_value({"question": ["not text"]}, "question")

    def test_stream_keeps_non_think_tags_visible(self):
        client = LLMClient()
        provider = {"base": "http://127.0.0.1:8000/v1", "model": "test", "key": ""}
        response = FakeResponse([sse_chunk("<br>"), b"data: [DONE]"])
        with patch("legalweb.llm.requests.post", return_value=response):
            emitted = [False]
            stream = client._stream_once(provider, [], 0, 100, emitted)
            output = []
            with self.assertRaises(StopIteration) as stopped:
                while True:
                    output.append(next(stream))
        self.assertEqual(output, [("delta", "<br>")])
        self.assertEqual(stopped.exception.value, "<br>")

    def test_stream_removes_think_content_even_when_tag_is_split(self):
        client = LLMClient()
        provider = {"base": "http://127.0.0.1:8000/v1", "model": "test", "key": ""}
        response = FakeResponse([
            sse_chunk("<thi"), sse_chunk("nk>hidden"),
            sse_chunk("</think>answer"), b"data: [DONE]",
        ])
        with patch("legalweb.llm.requests.post", return_value=response):
            emitted = [False]
            stream = client._stream_once(provider, [], 0, 100, emitted)
            output = []
            with self.assertRaises(StopIteration) as stopped:
                while True:
                    output.append(next(stream))
        self.assertEqual(output, [("delta", "answer")])
        self.assertEqual(stopped.exception.value, "answer")

    def test_stream_does_not_drop_a_partial_visible_tag_at_eof(self):
        client = LLMClient()
        provider = {"base": "http://127.0.0.1:8000/v1", "model": "test", "key": ""}
        response = FakeResponse([sse_chunk("<t"), b"data: [DONE]"])
        with patch("legalweb.llm.requests.post", return_value=response):
            emitted = [False]
            stream = client._stream_once(provider, [], 0, 100, emitted)
            output = []
            with self.assertRaises(StopIteration) as stopped:
                while True:
                    output.append(next(stream))
        self.assertEqual(output, [("delta", "<t")])
        self.assertEqual(stopped.exception.value, "<t")

    def test_citations_normalize_grouped_and_fullwidth_forms(self):
        result = sanitize_citations("依据[C1, C2]，并参照【C3】。", {"C1", "C2", "C3"})
        self.assertEqual(result["content"], "依据[C1][C2]，并参照[C3]。")
        self.assertEqual(result["used"], ["C1", "C2", "C3"])
        self.assertEqual(result["removed"], 0)

    def test_citations_remove_only_invalid_labels_after_normalizing(self):
        result = sanitize_citations("依据[C1、C9]。", {"C1"})
        self.assertEqual(result["content"], "依据[C1]。")
        self.assertEqual(result["used"], ["C1"])
        self.assertEqual(result["removed"], 1)

    def test_evidence_indexes_each_legal_article_separately(self):
        hits = [{
            "id": 7, "ordinal": 3, "section": "正文",
            "source_title": "法条 · 中华人民共和国测试法(FBMCLI.1.123)",
            "text": "第一条 普通规则。\n第二条 关键合同规则。\n第三条 其他规则。",
        }]
        refs = Evidence(hits, "关键合同").refs
        self.assertEqual([ref["label"] for ref in refs], ["C1", "C2", "C3"])
        self.assertEqual(refs[0]["article"], "第二条")
        self.assertEqual(refs[0]["source"], "中华人民共和国测试法")
        self.assertEqual(refs[0]["excerpt"], "关键合同规则。")

    def test_civil_code_marriage_book_is_bundled_and_retrievable(self):
        path = next(Path("assset/law").glob("中华人民共和国民法典*.txt"))
        chunks = split_sections(parse_document(path, path.name))
        corpus = [{**chunk, "id": index, "source_title": "法条 · 中华人民共和国民法典",
                   "embedding": None, "embedding_space": None}
                  for index, chunk in enumerate(chunks, 1)]
        with patch("legalweb.rag.jev_gate.apply", side_effect=lambda _query, hits, **_kwargs: hits):
            result = retrieve("夫妻双方自愿离婚需要什么手续", corpus, [],
                              self._NoEmbedding(), limit=6)
        self.assertTrue(any("第一千零七十六条" in hit["text"] for hit in result["hits"]))

    def test_case_evidence_remains_one_citation(self):
        hits = [{
            "id": 8, "ordinal": 1, "section": "正文",
            "source_title": "判例 · 某合同纠纷案",
            "text": "裁判理由提到民法典第一条，但这里不应把判例拆成法条。",
        }]
        refs = Evidence(hits, "合同纠纷").refs
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["source"], "某合同纠纷案")
        self.assertEqual(refs[0]["article"], "")

    def test_audience_role_watches_full_trial_without_checkpoint(self):
        self.assertIn("audience", PLAYER_ROLES)
        engine = TrialEngine(CASES["haicheng"], self._CourtLLM(), player_role="audience")
        events = list(engine.run())
        self.assertFalse(any(event["type"] == "checkpoint" for event in events))
        self.assertLessEqual(sum(event["type"] == "turn_start" for event in events), 10)
        ending = next(event for event in events if event["type"] == "await_deliberation")
        self.assertEqual(ending["player_role"], "audience")

    def test_court_roles_use_distinct_language_registers(self):
        self.assertIn("规范、严谨、克制的公诉语言", ROLES["prosecutor"]["system"])
        self.assertIn("专业、准确、有针对性的辩护语言", ROLES["defender"]["system"])
        self.assertIn("日常短句", ROLES["defendant"]["system"])
        self.assertIn("规范、克制的裁判语言", ROLES["judge_review"]["system"])

    def test_role_checkpoint_offers_two_presets_and_custom_can_be_supplied(self):
        for role in ("prosecutor", "defender", "defendant"):
            engine = TrialEngine(CASES["haicheng"], self._CourtLLM(), player_role=role)
            events = list(engine._role_checkpoint("investigation", "debate"))
            checkpoint = events[-1]
            self.assertEqual(checkpoint["kind"], "role_input")
            self.assertEqual(len(checkpoint["options"]), 2)
            for option in checkpoint["options"]:
                self.assertTrue(option["id"])
                self.assertTrue(option["label"])
                self.assertGreaterEqual(len(option["statement"]), 10)
                self.assertLessEqual(len(option["statement"]), 500)

    def test_conversation_history_is_scoped_to_account(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"LAW_DATA_DIR": directory}), \
                    patch("legalweb.app.seed_sources"), \
                    patch("legalweb.app.AUTH_RATE_MAX", 10_000):
                app = create_app()
            client = app.test_client()
            client.post("/api/auth/register", json={
                "username": "beta", "password": "secret123"})
            mine = client.post("/api/auth/register", json={
                "username": "alfa", "password": "secret123"}).get_json()["user"]
            identity = f"u:{mine['id']}"
            database = Database(Path(directory))
            stamp = now_iso()
            with database.connect() as db:
                for owner, conversation_id in ((identity, "conv-a"), ("u:someone-else", "conv-b")):
                    db.execute("INSERT INTO conversations(id,title,owner_id,created_at)"
                               " VALUES (?,?,?,?)", (conversation_id, owner, owner, stamp))
                    db.execute("INSERT INTO messages(id,conversation_id,role,content,payload,created_at)"
                               " VALUES (?,?,?,?,?,?)",
                               (new_id(), conversation_id, "user", f"question-{conversation_id}", "{}", stamp))
            listing = client.get("/api/conversations").get_json()
            self.assertEqual([item["id"] for item in listing["conversations"]], ["conv-a"])
            self.assertIsNone(client.get(
                "/api/conversations/conv-b").get_json()["conversation"])
            # 匿名一律 401
            anonymous = app.test_client()
            self.assertEqual(anonymous.get("/api/conversations").status_code, 401)


if __name__ == "__main__":
    unittest.main()
