# -*- coding: utf-8 -*-
"""阶段1：用 MiniMax Hailuo 图生视频，把 6 张立绘变成待机动画 MP4（一次性脚本）"""
import base64
import io
import json
import os
import sys
import time
import requests

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
KEY = os.environ["MM_KEY"]
HEAD = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
PROMPT = ("可爱的Q版白色独角神兽待机循环动画：身体轻轻呼吸起伏，偶尔眨眼，耳朵微微抖动，"
          "镜头完全固定，主体位置保持正中不变，动作轻柔缓慢，画面稳定无闪烁，无缝循环")

def submit(path):
    with open(path, "rb") as f:
        b64 = "data:image/png;base64," + base64.b64encode(f.read()).decode()
    r = requests.post("https://api.minimax.cn/v1/video_generation", headers=HEAD, json={
        "model": "MiniMax-Hailuo-02",
        "prompt": PROMPT,
        "first_frame_image": b64,
        "prompt_optimizer": True,
    }, timeout=120)
    d = r.json()
    tid = d.get("task_id")
    print(path, "submit:", tid or json.dumps(d, ensure_ascii=False)[:200])
    return tid

def poll(tid, timeout=900):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = requests.get(f"https://api.minimax.cn/v1/query/video_generation?task_id={tid}",
                         headers=HEAD, timeout=30)
        d = r.json()
        status = d.get("status")
        if status == "Success":
            fid = d.get("file_id")
            fr = requests.get(f"https://api.minimax.cn/v1/files/retrieve?file_id={fid}",
                              headers=HEAD, timeout=60)
            return fr.json().get("file", {}).get("download_url")
        if status in ("Fail", "FailOutput"):
            print("  FAIL:", json.dumps(d, ensure_ascii=False)[:300])
            return None
        time.sleep(8)
    return None

if __name__ == "__main__":
    only = sys.argv[1] if len(sys.argv) > 1 else None
    tasks = {}
    for i in range(6):
        p = f"web/pet/stage-{i}.png"
        if only is not None and str(i) != only:
            continue
        tid = submit(p)
        if tid:
            tasks[i] = tid
    for i, tid in tasks.items():
        url = poll(tid)
        print(f"stage-{i} video:", (url or "FAILED")[:90])
        if url:
            mp4 = requests.get(url, timeout=300).content
            with open(f"web/pet/stage-{i}-raw.mp4", "wb") as f:
                f.write(mp4)
            print(f"  saved web/pet/stage-{i}-raw.mp4 ({len(mp4)//1024}KB)")
