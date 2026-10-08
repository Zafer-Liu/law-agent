# -*- coding: utf-8 -*-
"""落地页獬豸追视动画素材（一次性脚本，断点续跑）：
1) stage-0.png 图生视频「转头看左/右并保持」→ 抽保持姿态帧
2) 以朝向帧为首帧生成各朝向 idle 动画
3) rembg 透明 → 回文循环 → web/pet/hero-look-{left,right}.webp
"""
import base64
import io as _io
import os
import subprocess
import sys
import time
import requests

sys.stdout = _io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
KEY = "sk-api-8TkPQwrkZip3QXVd-431tmLlcfAid8brpR2Oy8NG9LTK7jJMApQT2_lEA2mdN1nuqcXEoJF3Ku_L9u5Hyztd2AcNii1oFxHa-Wg0l8Ox-WMOq4LrD2z3IhI"
HEAD = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
WORK = "web/pet"
from PIL import Image
from rembg import remove, new_session

session = new_session("bria-rmbg")
SIZE = 256


def submit(first_frame_path, prompt):
    with open(first_frame_path, "rb") as f:
        b64 = "data:image/png;base64," + base64.b64encode(f.read()).decode()
    r = requests.post("https://api.minimax.cn/v1/video_generation", headers=HEAD, json={
        "model": "MiniMax-Hailuo-02", "prompt": prompt,
        "first_frame_image": b64, "prompt_optimizer": True,
    }, timeout=120)
    d = r.json()
    print("submit:", os.path.basename(first_frame_path), d.get("task_id") or str(d)[:150])
    return d.get("task_id")


def poll(tid, timeout=900):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = requests.get(f"https://api.minimax.cn/v1/query/video_generation?task_id={tid}",
                         headers=HEAD, timeout=30)
        d = r.json()
        if d.get("status") == "Success":
            fr = requests.get(f"https://api.minimax.cn/v1/files/retrieve?file_id={d['file_id']}",
                              headers=HEAD, timeout=60)
            return fr.json()["file"]["download_url"]
        if d.get("status") in ("Fail", "FailOutput"):
            print("  FAIL:", str(d)[:200])
            return None
        time.sleep(8)
    return None


def download(url, path):
    mp4 = requests.get(url, timeout=300).content
    with open(path, "wb") as f:
        f.write(mp4)
    print("saved", path, len(mp4) // 1024, "KB")


TURN_PROMPT = ("可爱的Q版白色独角神兽缓慢将头转向画面{dir}侧约45度，目光看向{dir}方并保持该姿势不动，"
               "身体保持正面朝向不动，镜头完全固定，主体位置不变，动作缓慢平稳，画面稳定无闪烁")
IDLE_PROMPT = ("可爱的Q版白色独角神兽保持头部转向{dir}方约45度的姿势不动，身体轻微呼吸起伏，偶尔眨眼，"
               "头部始终看向{dir}方不回正，镜头完全固定，主体位置不变，画面稳定，动作轻柔")


def to_loop_webp(raw, out):
    """raw mp4 → 抠透明帧 → 回文循环 → 无损透明 webp"""
    framedir = out.replace(".webp", "-frames")
    os.makedirs(framedir, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", raw,
                    "-vf", "fps=12,scale=512:512", f"{framedir}/f%03d.png"], check=True)
    frames = sorted(f for f in os.listdir(framedir)
                    if f.endswith(".png") and not f.startswith("cut_"))
    if len(frames) < 8:
        raise RuntimeError(f"帧数不足 {len(frames)}")
    loop = frames[:-1] + frames[-2:0:-1]

    def load_cut(name):
        cut = os.path.join(framedir, "cut_" + name)
        if not os.path.exists(cut):
            remove(Image.open(os.path.join(framedir, name)), session=session).save(cut)
        return Image.open(cut).convert("RGBA").resize((SIZE, SIZE), Image.LANCZOS)

    images = [load_cut(n) for n in loop]
    images[0].save(out, save_all=True, append_images=images[1:],
                   duration=83, loop=0, method=6, quality=82)
    for f in os.listdir(framedir):
        os.remove(os.path.join(framedir, f))
    os.rmdir(framedir)
    print(f"{out}: {len(loop)} 帧回文, {os.path.getsize(out)//1024}KB")


for side in ("left", "right"):
    zh = "左" if side == "left" else "右"
    look_png = f"{WORK}/hero-{side}.png"
    turn_raw = f"{WORK}/hero-{side}-turn-raw.mp4"
    idle_raw = f"{WORK}/hero-{side}-idle-raw.mp4"
    webp = f"{WORK}/hero-look-{side}.webp"

    if not os.path.exists(look_png):
        tid = submit(f"{WORK}/stage-0.png", TURN_PROMPT.format(dir=zh))
        if not tid:
            continue
        url = poll(tid)
        if not url:
            continue
        download(url, turn_raw)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", turn_raw,
                        "-vf", "fps=12,scale=512:512", f"{WORK}/_hb-{side}-%03d.png"], check=True)
        frames = sorted(f for f in os.listdir(WORK) if f.startswith(f"_hb-{side}-"))
        held = frames[int(len(frames) * 0.75)]
        Image.open(os.path.join(WORK, held)).save(look_png)
        for f in frames:
            os.remove(os.path.join(WORK, f))
        print(f"hero-{side}.png <- {held}")

    if os.path.exists(look_png) and not os.path.exists(idle_raw):
        tid = submit(look_png, IDLE_PROMPT.format(dir=zh))
        if tid:
            url = poll(tid)
            if url:
                download(url, idle_raw)

    if os.path.exists(idle_raw):
        to_loop_webp(idle_raw, webp)
print("done")
