"""Unit tests for the optional web-search MCP client (study/websearch.py).

All transport is faked: the client's requests.Session is swapped for an
in-memory handler, so these tests cover the JSON-RPC handshake, the SSE
response shape, session recovery and result normalisation without any
network access.
"""

import json
import os
import unittest
from unittest.mock import patch

import requests

from legalweb.websearch import TOOL_NAME, WebSearchClient, WebSearchError

# Chinese sample data kept as escapes so the file is pure ASCII on disk.
TITLE = "\u4fee\u6b63\u6848\u901a\u8fc7"          # 修正案通过
SITE = "\u793a\u4f8b\u7f51"                        # 示例网
SNIPPET = "\u6761\u6587\u5df2\u66f4\u65b0\u3002"   # 条文已更新。
QUERY = "\u73b0\u884c\u72b6\u6001"                 # 现行状态
ANY_QUERY = "\u95ee\u9898"                         # 问题
PLAIN_TEXT = "\u7eaf\u6587\u672c\u8bf4\u660e"      # 纯文本说明

RESULTS = [{"title": TITLE, "url": "https://example.com/law", "siteName": SITE,
            "snippet": SNIPPET, "icon": "https://example.com/icon.png"}]


class FakeResponse:
    def __init__(self, status_code=200, body="", content_type="application/json", lines=None):
        self.status_code = status_code
        self.headers = {"Content-Type": content_type}
        self.text = body
        self._lines = lines or []

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self):
        return json.loads(self.text)

    def iter_lines(self):
        yield from self._lines

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeTransport:
    def __init__(self, handler):
        self.handler = handler
        self.seen = []

    def post(self, url, json=None, headers=None, timeout=None, allow_redirects=None):
        self.seen.append({"url": url, "json": json, "headers": headers})
        return self.handler(json)


def rpc(result=None, error=None, rpc_id=1):
    body = {"jsonrpc": "2.0", "id": rpc_id}
    if error is not None:
        body["error"] = error
    else:
        body["result"] = result if result is not None else {}
    return json.dumps(body)


def sse(message):
    return ("data: " + json.dumps(message, ensure_ascii=False)).encode("utf-8")


class ConfigTests(unittest.TestCase):
    def test_without_key_the_channel_is_inert(self):
        with patch.dict(os.environ, {"LAW_WEBSEARCH_API_KEY": "", "LAW_WEBSEARCH_MCP_URL": ""}):
            client = WebSearchClient()
        self.assertFalse(client.configured)
        with self.assertRaises(WebSearchError):
            client.search(ANY_QUERY)

    def test_tools_mode_needs_only_a_key(self):
        # No MCP URL: the channel runs on the Zhipu tools API with a plain
        # API key — the same key the LLM providers use.
        with patch.dict(os.environ, {"LAW_WEBSEARCH_API_KEY": "k", "LAW_WEBSEARCH_MCP_URL": ""}):
            client = WebSearchClient()
        self.assertTrue(client.configured)
        self.assertFalse(client.mcp_mode)
        self.assertEqual(client.base, "https://open.bigmodel.cn/api/paas/v4")
        # A custom tools base overrides the default; HTTPS rules still apply.
        with patch.dict(os.environ, {"LAW_WEBSEARCH_API_KEY": "k", "LAW_WEBSEARCH_MCP_URL": "",
                                     "LAW_WEBSEARCH_BASE_URL": "http://127.0.0.1:9000/v4"}):
            self.assertTrue(WebSearchClient().configured)

    def test_remote_http_and_malformed_urls_are_rejected(self):
        cases = ["http://remote.example/mcp", "https://a.example/mcp?x=1",
                 "https://a.example/mcp#frag", "ftp://a.example/mcp", "not a url"]
        for url in cases:
            with patch.dict(os.environ, {"LAW_WEBSEARCH_API_KEY": "k", "LAW_WEBSEARCH_MCP_URL": url}):
                self.assertFalse(WebSearchClient().configured, url)
        # Local HTTP stays allowed, mirroring the LLM/embedding contract.
        with patch.dict(os.environ, {"LAW_WEBSEARCH_API_KEY": "k",
                                      "LAW_WEBSEARCH_MCP_URL": "http://127.0.0.1:9000/mcp"}):
            self.assertTrue(WebSearchClient().configured)


class TransportTests(unittest.TestCase):
    def make_client(self, handler, env=None):
        environ = {"LAW_WEBSEARCH_API_KEY": "sk-test",
                   "LAW_WEBSEARCH_MCP_URL": "https://mcp.test/prime/mcp"}
        environ.update(env or {})
        with patch.dict(os.environ, environ):
            client = WebSearchClient()
        client.session = FakeTransport(handler)
        return client

    @staticmethod
    def happy_handler(payload):
        method = payload.get("method")
        if method == "initialize":
            return FakeResponse(body=rpc({"sessionId": "sess-1", "protocolVersion": "2025-03-26"},
                                         rpc_id=payload["id"]))
        if method == "notifications/initialized":
            return FakeResponse(status_code=202)
        if method == "tools/list":
            schema = {"properties": {"search_query": {"type": "string"}, "count": {"type": "integer"}}}
            return FakeResponse(body=rpc({"tools": [{"name": TOOL_NAME, "inputSchema": schema}]},
                                         rpc_id=payload["id"]))
        if method == "tools/call":
            text = json.dumps({"search_result": RESULTS}, ensure_ascii=False)
            return FakeResponse(body=rpc({"content": [{"type": "text", "text": text}], "isError": False},
                                         rpc_id=payload["id"]))
        raise AssertionError(method)

    def test_full_json_flow_uses_discovered_schema(self):
        calls = []

        def handler(payload):
            if payload.get("method") == "tools/call":
                calls.append(payload)
            return self.happy_handler(payload)

        client = self.make_client(handler)
        rows = client.search(QUERY, 3)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], TITLE)
        self.assertEqual(rows[0]["url"], "https://example.com/law")
        self.assertEqual(rows[0]["site"], SITE)
        self.assertEqual(rows[0]["icon"], "https://example.com/icon.png")
        # The argument names come from tools/list, not a hard-coded guess.
        self.assertEqual(calls[0]["params"]["arguments"], {"search_query": QUERY, "count": 3})
        self.assertEqual(calls[0]["params"]["name"], TOOL_NAME)
        # Every request carries the deployment key and, after initialize,
        # the server-issued session id.
        first, later = client.session.seen[0], client.session.seen[-1]
        self.assertEqual(first["headers"]["Authorization"], "Bearer sk-test")
        self.assertEqual(later["headers"].get("Mcp-Session-Id"), "sess-1")

    def test_zhipu_double_encoded_text_and_header_session(self):
        """智谱真实行为：text 块是双重 JSON 编码的字符串数组，会话 ID 只在响应头。"""

        def handler(payload):
            method = payload.get("method")
            if method == "initialize":
                response = FakeResponse(body=rpc({"protocolVersion": "2025-03-26"},
                                                 rpc_id=payload["id"]))
                response.headers = {"Content-Type": "application/json",
                                    "Mcp-Session-Id": "header-sess-1"}
                return response
            if method == "notifications/initialized":
                return FakeResponse(status_code=202)
            if method == "tools/list":
                schema = {"properties": {"search_query": {"type": "string"}}}
                return FakeResponse(body=rpc({"tools": [{"name": "web_search_prime",
                                                         "inputSchema": schema}]},
                                             rpc_id=payload["id"]))
            if method == "tools/call":
                inner = json.dumps({"search_result": RESULTS}, ensure_ascii=False)
                text = json.dumps(inner, ensure_ascii=False)  # 第二层编码
                return FakeResponse(body=rpc({"content": [{"type": "text", "text": text}],
                                              "isError": False}, rpc_id=payload["id"]))
            raise AssertionError(method)

        client = self.make_client(handler)
        rows = client.search(QUERY, 3)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], TITLE)
        self.assertEqual(rows[0]["url"], "https://example.com/law")
        # 会话 ID 优先取响应头（响应体没有 sessionId）
        self.assertEqual(client._mcp_session, "header-sess-1")

    def test_sse_bodied_responses_are_parsed(self):
        def handler(payload):
            method = payload.get("method")
            if method == "notifications/initialized":
                return FakeResponse(status_code=202)
            if method == "tools/call":
                text = json.dumps({"results": RESULTS}, ensure_ascii=False)
                return FakeResponse(content_type="text/event-stream",
                                    lines=[sse({"jsonrpc": "2.0", "id": payload["id"],
                                                "result": {"content": [{"type": "text", "text": text}]}})])
            return FakeResponse(content_type="text/event-stream",
                                lines=[sse({"jsonrpc": "2.0", "id": payload["id"],
                                            "result": {"sessionId": "sse-sess"}})])

        client = self.make_client(handler)
        self.assertEqual(client.search(ANY_QUERY)[0]["url"], "https://example.com/law")

    def test_expired_session_reinitializes_once(self):
        state = {"initialize": 0, "failed_once": False}

        def handler(payload):
            method = payload.get("method")
            if method == "initialize":
                state["initialize"] += 1
                return FakeResponse(body=rpc({"sessionId": f"sess-{state['initialize']}"}, rpc_id=payload["id"]))
            if method == "notifications/initialized":
                return FakeResponse(status_code=202)
            if method == "tools/list":
                return FakeResponse(body=rpc({"tools": []}, rpc_id=payload["id"]))
            if not state["failed_once"]:
                state["failed_once"] = True
                return FakeResponse(status_code=404)
            return FakeResponse(body=rpc({"structuredContent": {"results": RESULTS}}, rpc_id=payload["id"]))

        client = self.make_client(handler)
        self.assertEqual(client.search(ANY_QUERY)[0]["site"], SITE)
        self.assertEqual(state["initialize"], 2)

    def test_protocol_version_falls_back_then_reports_errors(self):
        state = {"initialize": 0}

        def handler(payload):
            method = payload.get("method")
            if method == "initialize":
                state["initialize"] += 1
                if state["initialize"] == 1:
                    return FakeResponse(body=rpc(error={"code": -32000, "message": "unsupported version"},
                                                 rpc_id=payload["id"]))
                return FakeResponse(body=rpc({"sessionId": "s"}, rpc_id=payload["id"]))
            if method == "notifications/initialized":
                return FakeResponse(status_code=202)
            if method == "tools/list":
                return FakeResponse(status_code=202)
            return FakeResponse(body=rpc({"content": [{"type": "text", "text": json.dumps({"results": RESULTS})}]},
                                         rpc_id=payload["id"]))

        client = self.make_client(handler)
        self.assertEqual(len(client.search(ANY_QUERY)), 1)
        self.assertEqual(state["initialize"], 2)

    def test_rpc_and_tool_errors_raise(self):
        def error_handler(kind):
            def handler(payload):
                method = payload.get("method")
                if method == "initialize":
                    return FakeResponse(body=rpc({"sessionId": "s"}, rpc_id=payload["id"]))
                if method == "notifications/initialized":
                    return FakeResponse(status_code=202)
                if method == "tools/list":
                    return FakeResponse(status_code=202)
                if kind == "rpc":
                    return FakeResponse(body=rpc(error={"message": "boom"}, rpc_id=payload["id"]))
                return FakeResponse(body=rpc({"content": [{"type": "text", "text": "denied"}], "isError": True},
                                             rpc_id=payload["id"]))
            return handler

        for kind in ("rpc", "tool"):
            client = self.make_client(error_handler(kind))
            with self.assertRaises(WebSearchError) as caught:
                client.search(ANY_QUERY)
            if kind == "tool":
                # isError text blocks surface verbatim so misconfiguration
                # (quota, invalid key) is diagnosable from logs and replies.
                self.assertIn("denied", str(caught.exception))

    def test_tool_name_is_discovered_from_server(self):
        # The docs spell the tool webSearchPrime, but the live server
        # registers web_search_prime; the call must use the server's name.
        state = {"calls": []}

        def handler(payload):
            method = payload.get("method")
            if method == "initialize":
                return FakeResponse(body=rpc({"sessionId": "s"}, rpc_id=payload["id"]))
            if method == "notifications/initialized":
                return FakeResponse(status_code=202)
            if method == "tools/list":
                schema = {"properties": {"search_query": {"type": "string"}}}
                return FakeResponse(body=rpc({"tools": [
                    {"name": "unrelated_tool", "inputSchema": {"properties": {}}},
                    {"name": "web_search_prime", "inputSchema": schema}]}, rpc_id=payload["id"]))
            if method == "tools/call":
                state["calls"].append(payload)
                return FakeResponse(body=rpc({"content": [{"type": "text",
                                                          "text": json.dumps({"results": RESULTS})}]},
                                             rpc_id=payload["id"]))
            raise AssertionError(method)

        client = self.make_client(handler)
        self.assertEqual(len(client.search(QUERY)), 1)
        self.assertEqual(state["calls"][0]["params"]["name"], "web_search_prime")
        self.assertEqual(state["calls"][0]["params"]["arguments"], {"search_query": QUERY})

    def test_input_validation(self):
        client = self.make_client(self.happy_handler)
        with self.assertRaises(WebSearchError):
            client.search("   ")
        # The result count is clamped into the 1..8 window even if callers
        # pass something absurd.
        self.assertEqual(len(client.search(ANY_QUERY, 99)), 1)

    def test_result_shapes_fall_back_gracefully(self):
        def shape(text):
            return self.make_client(lambda payload: (
                FakeResponse(body=rpc({"sessionId": "s"}, rpc_id=payload["id"]))
                if payload.get("method") == "initialize" else
                FakeResponse(status_code=202)
                if payload.get("method") == "notifications/initialized" else
                FakeResponse(status_code=202)
                if payload.get("method") == "tools/list" else
                FakeResponse(body=rpc({"content": [{"type": "text", "text": text}]}, rpc_id=payload["id"]))
            ))

        # Plain text (not JSON) becomes one link-less snippet row.
        client = shape(PLAIN_TEXT)
        rows = client.search(ANY_QUERY)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0], {"title": "", "url": "", "site": "", "snippet": PLAIN_TEXT, "icon": ""})
        # Empty result sets stay empty instead of fabricating references.
        self.assertEqual(shape(json.dumps({"results": []})).search(ANY_QUERY), [])
        # Rows without any usable content are dropped.
        noisy = json.dumps({"results": [{"title": "  ", "snippet": None}, RESULTS[0]]})
        self.assertEqual(len(shape(noisy).search(ANY_QUERY)), 1)


class ToolsApiTests(unittest.TestCase):
    """Default transport: Zhipu paas/v4/tools with the web-search-pro model."""

    RESULT_ROW = {"title": TITLE, "link": "https://example.com/law", "media": SITE,
                  "content": SNIPPET, "icon": "https://example.com/icon.png"}

    @property
    def RESULT(self):
        # Live shape: results nest inside choices[].message.tool_calls[] as a
        # search_result call right after the search_intent call.
        return {"id": "rs-1", "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "tool", "tool_calls": [
                    {"id": "si", "type": "search_intent",
                     "search_intent": [{"index": 0, "intent": "SEARCH_ALL", "query": QUERY}]},
                    {"id": "sr", "search_result": [self.RESULT_ROW]}]}}],
                "usage": {"total_tokens": 1}}

    def make_client(self, handler, env=None):
        environ = {"LAW_WEBSEARCH_API_KEY": "sk-test", "LAW_WEBSEARCH_MCP_URL": ""}
        environ.update(env or {})
        with patch.dict(os.environ, environ):
            client = WebSearchClient()
        client.session = FakeTransport(handler)
        return client

    def test_tools_search_normalises_results(self):
        calls = []

        def handler(payload):
            calls.append(payload)
            return FakeResponse(body=json.dumps(self.RESULT, ensure_ascii=False))

        client = self.make_client(handler)
        rows = client.search(QUERY, 3)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0], {"title": TITLE, "url": "https://example.com/law",
                                   "site": SITE, "snippet": SNIPPET,
                                   "icon": "https://example.com/icon.png"})
        # The request targets the tools endpoint with the search model and key.
        seen = client.session.seen[0]
        self.assertEqual(seen["url"], "https://open.bigmodel.cn/api/paas/v4/tools")
        self.assertEqual(seen["headers"]["Authorization"], "Bearer sk-test")
        self.assertEqual(calls[0]["model"], "web-search-pro")
        self.assertEqual(calls[0]["messages"], [{"role": "user", "content": QUERY}])
        self.assertFalse(calls[0]["stream"])

    def test_tools_search_empty_and_invalid_shapes(self):
        # No nested search_result call anywhere: empty, not an error.
        empty = dict(self.RESULT, choices=[])
        client = self.make_client(lambda p: FakeResponse(body=json.dumps(empty)))
        self.assertEqual(client.search(QUERY), [])
        # Top-level search_result is also accepted (legacy/simple shape).
        flat = {"search_result": [self.RESULT_ROW]}
        client = self.make_client(lambda p: FakeResponse(body=json.dumps(flat)))
        self.assertEqual(len(client.search(QUERY)), 1)
        # Rows without usable content drop out; leftovers are trimmed to count.
        noisy = {"search_result": [{"title": " ", "content": None}, self.RESULT_ROW]}
        client = self.make_client(lambda p: FakeResponse(body=json.dumps(noisy)))
        self.assertEqual(len(client.search(QUERY)), 1)
        # A non-dict body is rejected loudly instead of fabricating results.
        client = self.make_client(lambda p: FakeResponse(body=json.dumps([1, 2])))
        with self.assertRaises(WebSearchError):
            client.search(QUERY)

    def test_tools_search_transport_errors_raise(self):
        def handler(payload):
            raise requests.ConnectionError("down")

        client = self.make_client(handler)
        with self.assertRaises(WebSearchError):
            client.search(QUERY)

    def test_error_text_from_server_surfaces(self):
        denied = {"error": {"code": "1211", "message": "tokens quota exhausted"}}
        client = self.make_client(lambda p: FakeResponse(status_code=429, body=json.dumps(denied)))
        # raise_for_status turns 429 into requests.HTTPError; the wrapper maps
        # every transport failure to a WebSearchError the tutor can relay.
        with self.assertRaises(WebSearchError):
            client.search(QUERY)


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# /api/ask 集成：联网搜索结果作为带 URL 引用编号的在线证据
# ---------------------------------------------------------------------------

import tempfile  # noqa: E402

from legalweb.app import create_app  # noqa: E402

GROUNDED_LAW_HIT = {
    "id": 1,
    "source_title": "劳动合同法",
    "section": "第四十七条",
    "ordinal": 0,
    "text": "经济补偿按劳动者在本单位工作的年限计算。",
}

SEARCH_ROW = {"title": TITLE, "url": "https://example.com/law", "site": SITE,
              "snippet": SNIPPET, "icon": ""}


class _FakeLLM:
    configured = True

    def stream(self, *_a, **_k):
        yield "delta", "依据如下。[C1][C2]"

    def complete(self, *_a, **_k):
        return "摘要"


class _FakeSearcher:
    configured = True

    def search(self, query, count=6):
        assert QUERY in query or "问题" in query
        return [SEARCH_ROW]


def _make_app():
    directory = tempfile.mkdtemp()
    with patch.dict(os.environ, {"LAW_DATA_DIR": directory}), \
            patch("legalweb.app.seed_sources"), \
            patch("legalweb.app.AUTH_RATE_MAX", 10_000), \
            patch("legalweb.app.LLMClient", return_value=_FakeLLM()), \
            patch("legalweb.app.WebSearchClient", return_value=_FakeSearcher()):
        return create_app()


def _ask(client, question, web_search):
    body = {"question": question, "request_id": "req-search-0001", "remember": False,
            "web_search": web_search}
    with patch("legalweb.app.retrieve", return_value={
            "hits": [GROUNDED_LAW_HIT], "backend": "lexical+fts5", "degraded": True}):
        response = client.post("/api/ask", json=body)
    events = [json.loads(line[6:]) for line in response.data.decode("utf-8").splitlines()
              if line.startswith("data: ")]
    result = next(e for e in reversed(events) if e["type"] == "result")
    return result, [e for e in events if e["type"] == "status"]


class AskWebSearchTests(unittest.TestCase):
    def setUp(self):
        self.client = _make_app().test_client()
        self.client.post("/api/auth/register",
                         json={"username": "u1", "password": "secret123"})

    def test_web_search_injects_cited_url_evidence(self):
        result, statuses = _ask(self.client, QUERY + " 最新条文是什么", web_search=True)
        urls = [c.get("url") for c in result["citations"]]
        self.assertIn("https://example.com/law", urls)
        self.assertTrue(any("正在联网搜索" in s.get("text", "") for s in statuses))
        self.assertIn("联网搜索", result["notice"])

    def test_disabled_by_default(self):
        result, _ = _ask(self.client, QUERY + " 最新条文是什么", web_search=False)
        urls = [c.get("url") for c in result["citations"]]
        self.assertNotIn("https://example.com/law", urls)

    def test_config_reports_flag(self):
        data = self.client.get("/api/config").get_json()
        self.assertIn("web_search_configured", data)


if __name__ == "__main__":
    unittest.main()
