# -*- coding: utf-8 -*-
"""用北大法宝 MCP 网关逐条拉取法律全文，按知识库格式写入 assset/law/（断点续拉）"""
import io
import json
import re
import sys
import time
import requests

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
KEY = "1b7782e8-a198-3a43-ae6f-800dfd75508e"
URL = "https://apim-gateway.pkulaw.com/mcp-law-search-service"
HEAD = {"Authorization": f"Bearer {KEY}", "Accept": "application/json, text/event-stream"}

DIGITS = "零一二三四五六七八九十"


def cn_num(n: int) -> str:
    if n <= 10:
        return DIGITS[n] if n != 10 else "十"
    if n < 20:
        return "十" + DIGITS[n - 10]
    if n < 100:
        tens, ones = divmod(n, 10)
        return DIGITS[tens] + "十" + (DIGITS[ones] if ones else "")
    hundreds, rest = divmod(n, 100)
    return DIGITS[hundreds] + "百" + (("零" if rest < 10 else "") + cn_num(rest) if rest else "")


def get_article(title: str, number: str) -> str:
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "get_article",
                          "arguments": {"title": title, "number": number}}}
    r = requests.post(URL, json=payload, headers=HEAD, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:80]}")
    data = json.loads(r.json()["result"]["content"][0]["text"])
    return (data.get("article") or "").strip()


LAWS = [
    ("中华人民共和国劳动合同法", 98),
    ("中华人民共和国劳动争议调解仲裁法", 53),
]

for title, count in LAWS:
    out = f"assset/law/{title}(FBM).txt"
    have = {}
    try:
        existing = open(out, encoding="utf-8").read()
        for m in re.finditer(r"^(第[零一二三四五六七八九十百]+条) (.+?)(?=\n第[零一二三四五六七八九十百]+条 |\Z)", existing, re.S | re.M):
            have[m.group(1)] = m.group(2).strip()
    except FileNotFoundError:
        pass
    for n in range(1, count + 1):
        num = f"第{cn_num(n)}条"
        if num in have:
            continue
        try:
            body = get_article(title, num)
        except Exception as exc:
            print(f"  {num} ERR {str(exc)[:80]}")
            if "90001" in str(exc) or "401" in str(exc):
                print("积分不足，中止")
                break
            time.sleep(0.6)
            continue
        body = re.sub(rf"^{num}[\s　]*", "", body)
        if body:
            have[num] = body
            print(f"  {num} ok")
        time.sleep(0.35)
    ordered = [f"{num} {have[num]}" for n2 in range(1, count + 1)
               for num in [f"第{cn_num(n2)}条"] if num in have]
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"{title}\n\n现行有效 · 经北大法宝 MCP 逐条获取\n\n" + "\n".join(ordered) + "\n")
    print(f"{title}: {len(ordered)}/{count} 条 → {out}")
