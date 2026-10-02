"""模拟法庭案例资产加载与公开视图。"""

import json
import re
from pathlib import Path

from .agents import build_roles

CASE_DIR = Path(__file__).resolve().parents[2] / "assset" / "moot-court"
CASE_ID_RE = re.compile(r"^[a-z0-9_-]{1,64}$")
REQUIRED_FIELDS = frozenset({
    "id", "title", "subtitle", "theme", "duration", "entry_note",
    "learning_goal", "disclaimer", "parties", "charge", "disputes",
    "facts", "dossier", "key_evidence", "laws", "real_verdict", "options", "trial",
})


def load_cases() -> dict[str, dict]:
    """Load and minimally validate bundled moot-court JSON assets."""
    cases = {}
    for path in sorted(CASE_DIR.glob("*.json")):
        try:
            case = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"模拟法庭案例文件无法读取：{path.name}") from exc
        if not isinstance(case, dict):
            raise TypeError(f"模拟法庭案例必须是 JSON 对象：{path.name}")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not CASE_ID_RE.fullmatch(case_id):
            raise RuntimeError(f"模拟法庭案例 ID 无效：{path.name}")
        missing = REQUIRED_FIELDS.difference(case)
        if missing:
            names = ", ".join(sorted(missing))
            raise RuntimeError(f"模拟法庭案例缺少字段：{path.name} · {names}")
        try:
            build_roles(case)
        except RuntimeError as exc:
            raise RuntimeError(f"模拟法庭案例 trial 块无效：{path.name} · {exc}") from exc
        if case_id in cases:
            raise RuntimeError(f"模拟法庭案例 ID 重复：{case_id}")
        cases[case_id] = case
    if not cases:
        raise RuntimeError("assset/moot-court 中没有可用的模拟法庭案例")
    return cases


CASES = load_cases()


def video_path(case: dict) -> Path | None:
    """案件介绍视频路径（assset/moot-court/<case_id>.mp4，可选存在）。"""
    path = CASE_DIR / f"{case['id']}.mp4"
    return path if path.is_file() else None


def public_case(case: dict, with_dossier: bool = True) -> dict:
    """面向前端的案例视图：隐藏真实判决，避免提前剧透。"""
    view = {key: case[key] for key in
            ("id", "title", "subtitle", "theme", "duration", "disclaimer",
             "parties", "charge", "disputes", "facts", "entry_note", "learning_goal")}
    if with_dossier:
        view["dossier"] = case["dossier"]
        view["options"] = case["options"]
    return view
