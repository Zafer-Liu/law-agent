"""检索引擎：词法 + FTS5 + 可选向量，倒数排名融合（RRF）。

移植自 Self_Learning_Agent 的 rag.py；向量服务未配置或不可用时
自动降级为 词法 + FTS5，调用方通过 retrieve()["degraded"] 向界面透出。
"""

import hashlib
import json
import math
import os
import re
from collections import defaultdict
from functools import lru_cache
from urllib.parse import urlsplit

import jieba
import requests

from . import jev_gate

STOP_WORDS = frozenset("请 请问 帮我 帮 帮助 解释 讲解 梳理 总结 咨询 一下 这个 那个 什么 如何 为什么 一个 一些 进行 关于 根据 内容 知识点 的 了 是 在 和 与 或 就 都 吗 呢 把 将 用 以及 并且 可以 我 你 它 继续 详细 简单 通俗 学习 当前 范围 现在 比如 说说 告诉 谢谢 麻烦".split())


@lru_cache(maxsize=256)
def terms(text: str) -> list[str]:
    words = jieba.cut_for_search(text[:6000].lower())
    return list(dict.fromkeys(word.strip() for word in words
                             if re.search(r"[\w㐀-鿿]", word)
                             and word.strip() not in STOP_WORDS and len(word.strip()) > 1))[:64]


def tokenize(text: str) -> str:
    return " ".join(word.strip() for word in jieba.cut_for_search(text.lower())
                    if word.strip() and re.search(r"\w", word))



def api_url(base: str, resource: str) -> str:
    parts = urlsplit(base)
    local = parts.hostname in {"localhost", "127.0.0.1", "::1"}
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("模型服务地址必须是有效的 HTTP(S) 地址。")
    if parts.scheme != "https" and not local:
        raise ValueError("非本机模型服务必须使用 HTTPS。")
    if parts.query or parts.fragment:
        raise ValueError("模型服务地址不能包含查询参数或片段。")
    root = base.rstrip("/")
    if parts.path.strip("/") == "":
        root += "/v1"
    return root + "/" + resource


def normalize(vector) -> list[float]:
    if not isinstance(vector, list) or not 16 <= len(vector) <= 8192:
        raise ValueError("向量服务返回了无效维度。")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in vector):
        raise ValueError("向量服务返回了无效数值。")
    norm = math.sqrt(sum(v * v for v in vector))
    if not math.isfinite(norm) or norm == 0:
        raise ValueError("向量服务返回了空向量。")
    return [v / norm for v in vector]


class EmbeddingClient:
    def __init__(self):
        self.base = os.getenv("LAW_EMBED_BASE_URL", "").strip()
        self.key = os.getenv("LAW_EMBED_API_KEY", "").strip()
        self.model = (os.getenv("LAW_EMBED_MODEL") or "bge-large-zh").strip()
        self.configured = bool(self.base and self.model)
        # 复用连接池：远端 TLS 握手动辄数秒，热连接毫秒级返回。
        self.session = requests.Session()

    def embed_texts(self, texts: list[str]) -> tuple[list[list[float]], str]:
        if not self.configured:
            raise ValueError("未配置向量模型，使用关键词检索。")
        url = api_url(self.base, "embeddings")
        vectors, dimension = [], None
        try:
            for offset in range(0, len(texts), 16):
                batch = texts[offset:offset + 16]
                headers = {"Authorization": "Bearer " + self.key} if self.key else {}
                with self.session.post(url, json={"model": self.model, "input": batch},
                                       headers=headers, timeout=(15, 30), allow_redirects=False) as response:
                    response.raise_for_status()
                    data = response.json()["data"]
                if not isinstance(data, list) or len(data) != len(batch):
                    raise ValueError("向量返回数量不匹配。")
                mapping = {item["index"]: normalize(item["embedding"]) for item in data}
                if set(mapping) != set(range(len(batch))):
                    raise ValueError("向量索引不完整。")
                for index in range(len(batch)):
                    vector = mapping[index]
                    dimension = dimension or len(vector)
                    if len(vector) != dimension:
                        raise ValueError("向量模型维度发生变化，请重新索引。")
                    vectors.append(vector)
        except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
            raise ValueError("向量服务暂不可用，已降级为关键词检索。") from exc
        fingerprint = hashlib.sha256(f"{url}|{self.model}|{dimension}".encode()).hexdigest()[:24]
        return vectors, fingerprint


def lexical_score(query: str, text: str) -> float:
    query_terms = terms(query)
    if not query_terms:
        return 0.0
    haystack = text.lower()
    meaningful = "".join(query_terms)
    phrase = 1.2 if len(meaningful) >= 2 and meaningful in haystack else 0.0
    words = [word for word in query_terms if re.search(r"[a-z0-9]", word)]
    english = 0.45 * sum(bool(re.search(r"\b" + re.escape(w) + r"\b", haystack)) for w in words) / max(1, len(words))
    grams = set()
    for word in query_terms:
        for run in re.findall(r"[㐀-鿿]+", word):
            for size in (2, 3):
                grams.update(run[i:i + size] for i in range(len(run) - size + 1))
    chinese = 0.9 * sum(gram in haystack for gram in grams) / max(1, len(grams))
    return phrase + english + chinese


def retrieve(query: str, chunks: list[dict], fts_ids: list[int], embedder: EmbeddingClient,
             limit=6, gate_hook=None) -> dict:
    if not chunks:
        return {"hits": [], "backend": "lexical+fts5", "degraded": True}
    allowed = {chunk["id"]: chunk for chunk in chunks}
    lexical = {key: lexical_score(query, chunk["text"]) for key, chunk in allowed.items()}
    vector_scores, vector_active = {}, False
    try:
        spaces = {chunk.get("embedding_space") for chunk in chunks if chunk.get("embedding")}
        if embedder.configured and spaces:
            vectors, space = embedder.embed_texts([query[:4000]])
            q = vectors[0]
            for key, chunk in allowed.items():
                value = chunk.get("embedding")
                if value and chunk.get("embedding_space") == space and len(value) == len(q):
                    score = sum(a * b for a, b in zip(q, value))
                    if math.isfinite(score):
                        vector_scores[key] = score
            vector_active = bool(vector_scores)
    except ValueError:
        pass
    lexical_rank = sorted((key for key in allowed if lexical[key] >= 0.12), key=lambda k: (-lexical[k], k))[:limit * 3]
    vector_rank = sorted((key for key in vector_scores if vector_scores[key] >= 0.55), key=lambda k: (-vector_scores[k], k))[:limit * 3]
    fts_rank = list(dict.fromkeys(key for key in fts_ids if key in allowed and lexical[key] >= 0.12))[:limit * 4]
    scores = defaultdict(float)
    for ranking in (lexical_rank, vector_rank, fts_rank):
        for rank, key in enumerate(ranking, 1):
            scores[key] += 1 / (60 + rank)
    threshold = 0.02 if vector_active else 0.015
    candidates = [key for key, score in scores.items()
                  if score >= threshold or vector_scores.get(key, 0) >= 0.72]
    candidates.sort(key=lambda key: (-scores[key], -vector_scores.get(key, 0), key))
    hits = []
    for key in candidates[:limit]:
        hit = {k: v for k, v in allowed[key].items() if k not in {"embedding", "embedding_space"}}
        hit.update(rrf_score=round(scores[key], 4), lexical_score=round(lexical[key], 4),
                   vector_score=round(vector_scores.get(key, 0), 4))
        hits.append(hit)
    hits = jev_gate.apply(query, hits, log_fn=gate_hook)
    return {"hits": hits, "backend": "vector+lexical+fts5" if vector_active else "lexical+fts5",
            "degraded": not vector_active}
