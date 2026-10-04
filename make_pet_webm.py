# -*- coding: utf-8 -*-
"""阶段2：raw MP4 → 逐帧抠透明 → 回文循环 → 无透明动画 WebP（一次性脚本）

pipeline: ffmpeg 抽帧(512px, 12fps) → rembg(共享 session, 断点续跑) →
          帧序列 + 逆序（palindrome，天然无缝循环）→ Pillow 无损 WebP
"""
import os
import subprocess
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from PIL import Image
from rembg import remove, new_session

session = new_session("bria-rmbg")
WORK = "web/pet"
SIZE = 256          # 徽章 96px 展示，256px 足够清晰且文件小


def process(stage):
    raw = f"{WORK}/stage-{stage}-raw.mp4"
    out = f"{WORK}/stage-{stage}.webp"
    if not os.path.exists(raw):
        print(f"stage-{stage}: raw mp4 缺失，跳过")
        return
    framedir = f"{WORK}/_frames-{stage}"
    os.makedirs(framedir, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", raw,
                    "-vf", "fps=12,scale=512:512", f"{framedir}/f%03d.png"], check=True)
    frames = sorted(f for f in os.listdir(framedir)
                    if f.endswith(".png") and not f.startswith("cut_"))
    if len(frames) < 8:
        print(f"stage-{stage}: 帧数不足（{len(frames)}）")
        return
    loop = frames[:-1] + frames[-2:0:-1]          # 回文：正序 + 逆序（去首尾重复）

    def load_cut(name):
        cut = os.path.join(framedir, "cut_" + name)
        if not os.path.exists(cut):
            img = Image.open(os.path.join(framedir, name))
            remove(img, session=session).save(cut)
        return Image.open(cut).convert("RGBA").resize((SIZE, SIZE), Image.LANCZOS)

    images = [load_cut(n) for n in loop]
    images[0].save(out, save_all=True, append_images=images[1:],
                   duration=83, loop=0, method=6, quality=82)
    print(f"stage-{stage}: {out} 完成（{len(loop)} 帧回文，{os.path.getsize(out)//1024}KB）")
    for name in os.listdir(framedir):
        os.remove(os.path.join(framedir, name))
    os.rmdir(framedir)


if __name__ == "__main__":
    for s in (sys.argv[1:] or [str(i) for i in range(6)]):
        process(int(s))
