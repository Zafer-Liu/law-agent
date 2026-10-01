# -*- coding: utf-8 -*-
"""端到端测试：法庭式庭审 + 检查点续跑 + 评议轨迹。"""
import json
import sys
import requests

BASE = "http://127.0.0.1:5000"


def read_sse(res):
    """消费 SSE 流，返回 (events, last_event_type)。"""
    events = []
    for line in res.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data: "):
            continue
        try:
            events.append(json.loads(line[6:]))
        except ValueError:
            pass
    return events


def summarize(events):
    types = {}
    for e in events:
        types[e["type"]] = types.get(e["type"], 0) + 1
    return types


def main():
    errors = []

    # 1) 开始庭审 → 应停在 mid_review 检查点
    print("== 1. trial -> expect checkpoint mid_review")
    res = requests.post(f"{BASE}/api/court/cases/haicheng/trial", stream=True, timeout=600)
    assert res.status_code == 200, res.text
    events = read_sse(res)
    print("   events:", summarize(events))
    session_id = next(e["session_id"] for e in events if e["type"] == "session")
    cps = [e for e in events if e["type"] == "checkpoint"]
    assert len(cps) == 1 and cps[0]["kind"] == "mid_review", cps
    cp = cps[0]
    assert cp["next"] == "debate" and cp["allow_question"] and len(cp["options"]) == 3
    assert not any(e["type"] == "error" for e in events), [e for e in events if e["type"] == "error"]
    # 卷宗里应有举证质证发言
    assert summarize(events).get("turn_end", 0) >= 5, "investigation turns missing"
    print("   OK, session:", session_id)

    # 2) 检查点1：预判 + 发问（向公诉人）
    print("== 2. continue #1: poll C + question to prosecutor")
    res = requests.post(f"{BASE}/api/court/sessions/{session_id}/continue",
                        json={"poll": "C",
                              "question": "E4 监控显示夺刀到追刺只有11秒，你凭什么认定不法侵害已经结束？",
                              "target": "prosecutor"},
                        stream=True, timeout=600)
    assert res.status_code == 200, res.text
    events = read_sse(res)
    print("   events:", summarize(events))
    assert not any(e["type"] == "error" for e in events), [e for e in events if e["type"] == "error"]
    assert events[0]["type"] == "resume"
    cps = [e for e in events if e["type"] == "checkpoint"]
    assert len(cps) == 1 and cps[0]["kind"] == "poll", cps
    assert cps[0]["next"] == "statement" and cps[0]["previous_label"] == "正当防卫，不负刑事责任"
    # 公诉人应有一条作答回合
    roles = [e.get("role") for e in events if e["type"] == "turn_start"]
    assert "prosecutor" in roles, roles
    print("   OK, answered by:", roles)

    # 2.5) 检查点待决时：无效预判应 400
    print("== 2.5 invalid poll at pending checkpoint")
    r = requests.post(f"{BASE}/api/court/sessions/{session_id}/continue",
                      json={"poll": "Z"}, timeout=30)
    assert r.status_code == 400, r.status_code
    print("   OK")

    # 3) 检查点2：辩论后复核（改判为 B，测试变化提示）
    print("== 3. continue #2: poll B (changed)")
    res = requests.post(f"{BASE}/api/court/sessions/{session_id}/continue",
                        json={"poll": "B"}, stream=True, timeout=600)
    assert res.status_code == 200, res.text
    events = read_sse(res)
    print("   events:", summarize(events))
    assert not any(e["type"] == "error" for e in events)
    assert any(e["type"] == "await_deliberation" for e in events), "deliberation not reached"

    # 4) 评议：C + 关键证据标注
    print("== 4. deliberation: C + marks E4/E5")
    res = requests.post(f"{BASE}/api/court/sessions/{session_id}/deliberation",
                        json={"option": "C", "marks": ["E4", "E5"]},
                        stream=True, timeout=600)
    assert res.status_code == 200, res.text
    events = read_sse(res)
    print("   events:", summarize(events))
    assert not any(e["type"] == "error" for e in events)
    verdict = next(e for e in events if e["type"] == "verdict")
    assert len(verdict["trajectory"]) == 2, verdict["trajectory"]
    assert verdict["trajectory"][0]["label"].startswith("正当防卫")
    assert verdict["trajectory"][1]["label"].startswith("防卫过当")
    analysis = next(e for e in events if e["type"] == "result")["analysis"]
    print("   trajectory:", verdict["trajectory"])
    print("   analysis len:", len(analysis))

    # 5) 非法输入校验（会话已评议：继续/重复评议都应被拒）
    print("== 5. invalid inputs")
    r = requests.post(f"{BASE}/api/court/sessions/{session_id}/continue", json={"poll": "C"}, timeout=30)
    assert r.status_code == 409, r.status_code
    r = requests.post(f"{BASE}/api/court/sessions/{session_id}/deliberation",
                      json={"option": "C"}, timeout=30)
    assert r.status_code == 409, r.status_code
    print("   OK")

    print("\nALL TESTS PASSED")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("FAILED:", exc)
        sys.exit(1)
