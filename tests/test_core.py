"""Fast regression tests for reliability boundaries that do not need an LLM."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.exceptions import BadRequest

from legalweb.app import create_app, text_value
from legalweb.database import Database, new_id, now_iso
from legalweb.llm import LLMClient
from legalweb.qa import sanitize_citations


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

    def test_conversation_history_is_scoped_to_browser_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"LAW_DATA_DIR": directory}), \
                    patch("legalweb.app.seed_sources"):
                app = create_app()
            database = Database(Path(directory))
            stamp = now_iso()
            with database.connect() as db:
                for owner, conversation_id in (("browser-a", "conv-a"), ("browser-b", "conv-b")):
                    db.execute("INSERT INTO conversations(id,title,owner_id,created_at)"
                               " VALUES (?,?,?,?)", (conversation_id, owner, owner, stamp))
                    db.execute("INSERT INTO messages(id,conversation_id,role,content,payload,created_at)"
                               " VALUES (?,?,?,?,?,?)",
                               (new_id(), conversation_id, "user", f"question-{owner}", "{}", stamp))
            client = app.test_client()
            listing = client.get("/api/conversations?client_id=browser-a").get_json()
            self.assertEqual([item["id"] for item in listing["conversations"]], ["conv-a"])
            self.assertIsNone(client.get(
                "/api/conversations/conv-b?client_id=browser-a").get_json()["conversation"])


if __name__ == "__main__":
    unittest.main()
