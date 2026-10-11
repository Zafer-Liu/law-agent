"""法律常识（普法板块）回归：卡片索引、独立文章接口与内容结构。"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from legalweb import app as app_module
from legalweb.app import create_app


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


MODULE_KEYS = ["procedure", "knowledge", "loan", "help"]
BLOCK_TYPES = {"route", "table", "cards", "faq", "notes", "hotlines"}


class BasicsTests(unittest.TestCase):
    def setUp(self):
        app_module._auth_attempts.clear()
        self.addCleanup(app_module._auth_attempts.clear)
        self.app, self.directory = make_app()
        self.client = self.app.test_client()

    def index(self):
        res = self.client.get("/api/basics")
        self.assertEqual(res.status_code, 200)
        return res.get_json()

    def article(self, key):
        res = self.client.get(f"/api/basics/{key}")
        self.assertEqual(res.status_code, 200)
        return res.get_json()

    def test_index_public_without_login(self):
        """索引只给卡片所需的元数据：匿名可读，不含正文 blocks。"""
        data = self.index()
        self.assertEqual([m["key"] for m in data["modules"]], MODULE_KEYS)
        self.assertTrue(data["disclaimer"])
        self.assertTrue(data["updated_at"])
        for module in data["modules"]:
            self.assertNotIn("blocks", module)
            for field in ("key", "name", "sigil", "brief", "block_count"):
                self.assertIn(field, module)
            self.assertGreaterEqual(module["block_count"], 1)

    def test_article_shape_and_navigation(self):
        """文章接口返回单模块正文，并带上一篇/下一篇。"""
        first = self.article("procedure")
        self.assertIn("blocks", first["module"])
        self.assertIsNone(first["prev"])
        self.assertEqual(first["next"]["key"], "knowledge")
        last = self.article("help")
        self.assertIsNone(last["next"])
        self.assertEqual(last["prev"]["key"], "loan")
        middle = self.article("loan")
        self.assertEqual(middle["prev"]["key"], "knowledge")
        self.assertEqual(middle["next"]["key"], "help")

    def test_unknown_module_is_404(self):
        """未知模块：JSON 接口与文章页都拒绝。"""
        self.assertEqual(self.client.get("/api/basics/nope").status_code, 404)
        page = self.client.get("/basics/nope")
        self.assertEqual(page.status_code, 404)

    def test_article_page_served(self):
        """独立文章页：真实 URL 返回 basics.html。"""
        for key in MODULE_KEYS:
            res = self.client.get(f"/basics/{key}")
            self.assertEqual(res.status_code, 200)
            self.assertIn("text/html", res.headers["Content-Type"])
            self.assertIn(b"article-body", res.data)

    def test_route_block_structure(self):
        """诉讼流程路线图：站点、道路、图例结构完整，道路端点有效。"""
        module = self.article("procedure")["module"]
        route = next(b for b in module["blocks"] if b["type"] == "route")
        stations = {s["key"]: s for s in route["stations"]}
        self.assertGreaterEqual(len(stations), 7)
        # 主线四站 + 三条支线
        self.assertLessEqual({"jurisdiction", "trial", "appeal", "enforce",
                              "small", "retrial", "procuratorate"}, set(stations))
        for station in stations.values():
            self.assertTrue(station["title"])
            self.assertTrue(station["text"])
            self.assertGreaterEqual(station["col"], 1)
            self.assertGreaterEqual(station["row"], 1)
        # 布局契约：主线居中行（row 2）从左到右，支线挂下方行（row 3）
        for key in ("jurisdiction", "trial", "appeal", "enforce"):
            self.assertEqual(stations[key]["row"], 2)
        for key in ("small", "retrial", "procuratorate"):
            self.assertEqual(stations[key]["row"], 3)
        placed = [(s["col"], s["row"]) for s in stations.values()]
        self.assertEqual(len(placed), len(set(placed)))
        valid = set(stations) | {"start", "end"}
        for road in route["roads"]:
            self.assertIn(road["from"], valid)
            self.assertIn(road["to"], valid)
        # 主线/支线图例说明已并入 hint（不再有独立 legend 卡）
        self.assertIn("红色主线", route["hint"])
        self.assertIn("特殊通道", route["hint"])
        # 主线顺序：出发 → 管辖 → 一审 → 二审 → 生效执行 → 终点
        self.assertEqual(route["roads"][0]["from"], "start")
        self.assertEqual(route["roads"][-1]["to"], "end")

    def test_content_covers_core_topics(self):
        """核心主题仍在：三大诉讼对比、借贷红线、求助热线。"""
        procedure = self.article("procedure")["module"]
        table = next(b for b in procedure["blocks"] if b["type"] == "table")
        self.assertGreaterEqual(len(table["columns"]), 4)
        loan = self.article("loan")["module"]
        cards = next(b for b in loan["blocks"] if b["type"] == "cards")
        badges = {item.get("badge") for item in cards["items"]}
        self.assertIn("红线", badges)
        hotlines = next(b for b in self.article("help")["module"]["blocks"]
                        if b["type"] == "hotlines")
        self.assertTrue(any(item["number"] == "12348" for item in hotlines["items"]))

    def test_block_types_known(self):
        for key in MODULE_KEYS:
            for block in self.article(key)["module"]["blocks"]:
                self.assertIn(block["type"], BLOCK_TYPES)
                self.assertTrue(block["title"])

    def test_same_for_logged_in_user(self):
        """登录与否内容一致：普法不属于按账户隔离的数据。"""
        self.client.post("/api/auth/register", json={
            "username": "gamma", "password": "secret123"})
        anonymous = self.app.test_client().get("/api/basics").get_json()
        logged_in = self.client.get("/api/basics").get_json()
        self.assertEqual(anonymous, logged_in)
        anon_article = self.app.test_client().get("/api/basics/loan").get_json()
        login_article = self.client.get("/api/basics/loan").get_json()
        self.assertEqual(anon_article, login_article)


if __name__ == "__main__":
    unittest.main()
