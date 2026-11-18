import json
import unittest
from unittest.mock import patch

from legalweb.qa import build_messages
from test_trust import make_app, register, result_event


CASE_HIT = {
    "case_id": "sample", "case_no": 12, "cause": "买卖合同纠纷", "score": 0.9,
    "facts": "买方签字确认欠付货款。", "reasoning": "法院核对了对账单。", "outcome": "支持货款请求。",
}


class CaseQaTests(unittest.TestCase):
    def setUp(self):
        self.app, _ = make_app(configured_llm=True)
        self.admin = self.app.test_client()
        self.member = self.app.test_client()
        self.assertEqual(register(self.admin, "case_admin").status_code, 200)
        self.assertEqual(register(self.member, "case_member").status_code, 200)
        self.retrieval = patch("legalweb.app.retrieve", return_value={"hits": [], "backend": "lexical", "degraded": False})
        self.retrieval.start()
        self.addCleanup(self.retrieval.stop)

    def ask(self, client, **options):
        return client.post("/api/ask", json={"question": "对账后欠付货款，可以怎么办？", **options})

    def test_admin_opt_in_adds_a_case_citation(self):
        with patch("legalweb.case_vectors.CaseSearch.search", return_value=[CASE_HIT]) as search, \
                patch("legalweb.app.qa.build_messages", wraps=build_messages) as build:
            response = result_event(self.ask(self.admin, case_search=True))
        search.assert_called_once_with("对账后欠付货款，可以怎么办？", 3)
        self.assertIn("+cases", response["backend"])
        self.assertIn("已检索课程案例", response["notice"])
        (citation,) = response["citations"]
        self.assertEqual((citation["source_kind"], citation["case_no"]), ("case", 12))
        self.assertIn("对账单", citation["excerpt"])
        messages = build_messages(*build.call_args.args, **build.call_args.kwargs)
        self.assertEqual(json.loads(messages[1]["content"])["evidence"][0]["source_kind"], "case")
        self.assertIn("类案比较", messages[0]["content"])

    def test_defaults_to_off_and_requires_admin(self):
        with patch("legalweb.case_vectors.CaseSearch.search") as search:
            self.assertEqual(self.ask(self.member, case_search=True).status_code, 403)
            self.assertNotIn("+cases", result_event(self.ask(self.admin))["backend"])
        search.assert_not_called()
