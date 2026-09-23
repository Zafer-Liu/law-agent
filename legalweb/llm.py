"""OpenAI 兼容模型客户端：非流式 complete 与流式 stream，含备用模型切换。

从 Self_Learning_Agent tutor.py 的 provider 模式提取；保持同一纪律：
只有首个可见 delta 之前才允许切换备用模型，避免屏幕上出现重复文本。
"""

import json
import logging
import os
import re

import requests

from .rag import api_url

LOG = logging.getLogger("legalweb.llm")


class LLMError(Exception):
    pass


class LLMClient:
    def __init__(self):
        def provider(prefix, token_default):
            base = os.getenv(prefix + "_BASE_URL", "").strip()
            key = os.getenv(prefix + "_API_KEY", "").strip()
            model = os.getenv(prefix + "_MODEL", "").strip()
            if not (base and model):
                return None
            raw_tokens = os.getenv(prefix + "_MAX_TOKENS", "").strip()
            try:
                max_tokens = max(500, min(int(raw_tokens), 200000)) if raw_tokens else token_default
            except ValueError:
                max_tokens = token_default
            return {"base": base, "key": key, "model": model, "max_tokens": max_tokens, "env": prefix}

        primary = provider("LAW_LLM", 8000)
        fallback = provider("LAW_LLM_FALLBACK", primary["max_tokens"] if primary else 8000)
        self.providers = [item for item in (primary, fallback) if item]
        self.configured = bool(self.providers)

    def _headers(self, provider):
        return {"Authorization": "Bearer " + provider["key"]} if provider["key"] else {}

    @staticmethod
    def _content(message) -> str:
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            # 推理模型预算耗尽时 content 可能为空，答案落在 reasoning_content。
            content = message.get("reasoning_content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("empty model content")
        # 清理推理模型的 <think> 标签
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
        if not content:
            raise ValueError("empty model content after think filter")
        return content

    def complete(self, messages, temperature=0.2, max_tokens=2000) -> str:
        """一次普通非流式调用，带备用模型回退。"""
        if not self.providers:
            raise LLMError("尚未配置模型服务，请设置 LAW_LLM_BASE_URL、LAW_LLM_API_KEY 和 LAW_LLM_MODEL。")
        failure = None
        for provider in self.providers:
            payload = {"model": provider["model"], "messages": messages,
                       "temperature": temperature,
                       "max_tokens": min(max_tokens, provider["max_tokens"]),
                       "stream": False}
            try:
                with requests.post(api_url(provider["base"], "chat/completions"), json=payload,
                                   headers=self._headers(provider), timeout=(5, 180),
                                   allow_redirects=False) as response:
                    response.raise_for_status()
                    return self._content(response.json()["choices"][0]["message"])
            except (requests.RequestException, ValueError, KeyError, TypeError, IndexError) as exc:
                failure = exc
                LOG.warning("LLM %s complete failed: %s: %s", provider["env"], type(exc).__name__, exc)
        raise LLMError("模型调用失败，请检查模型配置后重试。") from failure

    def stream(self, messages, temperature=0.2, max_tokens=2000):
        """流式调用：先产出 ("delta", text) 增量，结束产出 ("content", 全文)。

        首个 delta 之前失败可切换备用模型；之后失败直接抛 LLMError。
        """
        if not self.providers:
            raise LLMError("尚未配置模型服务，请设置 LAW_LLM_BASE_URL、LAW_LLM_API_KEY 和 LAW_LLM_MODEL。")
        for index, provider in enumerate(self.providers):
            emitted = [False]
            try:
                content = yield from self._stream_once(provider, messages, temperature,
                                                       min(max_tokens, provider["max_tokens"]), emitted)
                yield ("content", content)
                return
            except LLMError as exc:
                if emitted[0] or index + 1 >= len(self.providers):
                    raise
                LOG.warning("LLM %s stream failed (%s); switching to fallback %s",
                            provider["env"], exc, self.providers[index + 1]["env"])

    def _stream_once(self, provider, messages, temperature, max_tokens, emitted) -> str:
        content = ""
        # 推理模型（如 MiniMax-M3）会在 content 里输出 <think>...</think>。
        # 策略：先缓冲，直到检测到 </think> 或确认没有 <think> 才开始流式输出。
        # ``None`` means the first token could still become ``<think>``.
        # Do not treat every string beginning with '<' as hidden reasoning:
        # ordinary Markdown/HTML such as '<br>' must remain visible.
        think_done: bool | None = None
        buffer = ""
        try:
            payload = {"model": provider["model"], "messages": messages,
                       "temperature": temperature, "max_tokens": max_tokens, "stream": True}
            with requests.post(api_url(provider["base"], "chat/completions"), json=payload,
                               headers=self._headers(provider), timeout=(5, 240),
                               allow_redirects=False, stream=True) as response:
                response.raise_for_status()
                for raw in response.iter_lines():
                    if not raw:
                        continue
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except ValueError:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    if choices[0].get("finish_reason") == "length":
                        # 如果还在 think 标签内被截断，think 内容本来就要丢弃，
                        # 不视为错误；只有在可见回答被截断时才报错。
                        if think_done:
                            raise ValueError("truncated generation")
                        break
                    delta = choices[0].get("delta") or {}
                    piece = delta.get("content")
                    if not isinstance(piece, str) or not piece:
                        continue
                    content += piece
                    if len(content) > 120000:
                        raise ValueError("oversized stream")

                    if think_done is None:
                        buffer += piece
                        if buffer.startswith("<think>"):
                            think_done = False
                        elif "<think>".startswith(buffer):
                            # The tag may be split across SSE chunks.
                            continue
                        else:
                            think_done = True
                            emitted[0] = True
                            yield ("delta", buffer)
                            buffer = ""
                            continue
                    elif think_done is False:
                        buffer += piece
                    elif think_done is True:
                        emitted[0] = True
                        yield ("delta", piece)
                        continue
                    if think_done is False:
                        # 检查是否包含完整的 <think>...</think>
                        if "</think>" in buffer:
                            after = buffer.split("</think>", 1)[1]
                            think_done = True
                            if after.strip():
                                emitted[0] = True
                                yield ("delta", after)
                            buffer = ""

                # EOF handling below distinguishes a harmless partial tag from
                # an unterminated hidden-thinking block.
            if think_done is None and buffer:
                # A harmless visible string can begin with a partial '<think>'
                # prefix (for example an incomplete HTML tag).  At EOF it is
                # no longer ambiguous, so do not silently lose it.
                emitted[0] = True
                yield ("delta", buffer)
            if not content.strip():
                raise ValueError("empty stream")
        except requests.RequestException as exc:
            raise LLMError("模型请求失败或超时。") from exc
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            raise LLMError("模型输出无效，请重试。") from exc
        # 清理最终内容中的 think 标签
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.S)
        content = re.sub(r"<think>.*$", "", content, flags=re.S).strip()
        if not content:
            raise LLMError("模型输出无有效正文，请重试。")
        return content
