"""北大法宝 MCP 在线法条检索（可选增强）。

走 streamable HTTP MCP（服务端无状态：单次 POST 即一次工具调用，无需
initialize 握手与 session）。检索结果转换为与本地 RAG 完全同构的 hit
结构并入 Evidence，引用校验、前端引用面板与对话持久化链路零特判；
在线条目额外携带 url / timeliness 字段供前端外链展示。

未配置 LAW_PKULAW_TOKEN 时自动停用，全部调用静默返回空列表。
"""

import json
import logging
import os
import re

import requests

LOG = logging.getLogger("legalweb.pkulaw")

DEFAULT_BASE = "https://apim-gateway.pkulaw.com"
SEARCH_PATH = "/mcp-law-search-service"
_FIRST_ARTICLE_RE = re.compile(r"(第[一二三四五六七八九十百千万零〇两\d]+条"
                               r"(?:之[一二三四五六七八九十百千万零〇两\d]+)?)")
_MAX_QUERY_CHARS = 200


class PkulawClient:
    """北大法宝「检索法律法规-语义」服务的最小 MCP 客户端。"""

    def __init__(self):
        self._token = (os.getenv("LAW_PKULAW_TOKEN") or "").strip()
        self.base = (os.getenv("LAW_PKULAW_BASE_URL") or DEFAULT_BASE).rstrip("/")
        try:
            self.size = max(1, min(int(os.getenv("LAW_PKULAW_SIZE") or 4), 10))
        except ValueError:
            self.size = 4
        try:
            self.timeout = float(os.getenv("LAW_PKULAW_TIMEOUT") or 12)
        except ValueError:
            self.timeout = 12.0
        self.configured = bool(self._token)

    def search(self, question: str) -> list:
        """语义检索法条，返回本地 RAG 同构的 hit 列表；任何失败都降级为空列表。"""
        if not self.configured or not (question or "").strip():
            return []
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "search_article",
                "arguments": {"text": question[:_MAX_QUERY_CHARS], "size": self.size},
            },
        }
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json, text/event-stream",
        }
        try:
            response = requests.post(self.base + SEARCH_PATH, json=payload,
                                     headers=headers, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            LOG.warning("pkulaw online search failed: %s", exc)
            return []
        if "error" in data:
            LOG.warning("pkulaw online search error: %s", data["error"])
            return []
        hits = []
        try:
            for item in self._result_items(data):
                hit = self._to_hit(item)
                if hit:
                    hits.append(hit)
        except (KeyError, TypeError, ValueError) as exc:
            LOG.warning("pkulaw online search parse failed: %s", exc)
            return []
        LOG.info("pkulaw online search returned %d usable articles", len(hits))
        return hits

    @staticmethod
    def _result_items(data: dict) -> list:
        content = data["result"]["content"]
        text = next(piece["text"] for piece in content if piece.get("type") == "text")
        items = json.loads(text)
        if not isinstance(items, list):
            raise ValueError("unexpected search payload")
        return items

    @staticmethod
    def _to_hit(item: dict):
        """单条检索结果 → 本地 hit；无正文的条目（如纯通知标题）跳过。"""
        article_text = (item.get("article") or "").strip()
        if len(article_text) < 20:
            return None
        title = (item.get("title") or "北大法宝检索结果").strip()
        first_article = _FIRST_ARTICLE_RE.search(article_text[:40])
        section = f"{title} / {first_article.group(1)}" if first_article else title
        return {
            "id": f"pkulaw:{item.get('gid') or title[:48]}",
            "source_title": f"北大法宝 · {title}",
            "section": section,
            "ordinal": 0,
            "text": article_text,
            "url": item.get("url") or "",
            "timeliness": item.get("timeliness") or "",
        }
