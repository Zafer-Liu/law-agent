"""法律常识（普法内容）资产加载与公开视图。

内容来源 assset/legal-basics/basics.json，由前端消费：
四个模块（诉讼流程 / 诉讼基本知识 / 贷款与借钱 / 求助渠道）各自是
一篇独立文章（/basics/<key>），「法律常识」Tab 只渲染文章卡片索引。
块类型由 BLOCK_SPECS 约定，手改 JSON 出现结构错误时在启动期直接暴露。
"""

import json
import re
from pathlib import Path

BASICS_FILE = Path(__file__).resolve().parent.parent / "assset" / "legal-basics" / "basics.json"
MODULE_KEY_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")

# 块类型 -> {容器字段: 条目必填字段}；notes 的条目是纯字符串，无必填子字段
BLOCK_SPECS = {
    "route": {"stations": ("key", "col", "row", "title", "text"), "roads": ("from", "to")},
    "timeline": {"steps": ("title", "text")},
    "table": {"columns": (), "rows": ()},
    "cards": {"items": ("title", "text")},
    "faq": {"items": ("q", "a")},
    "notes": {"items": ()},
    "hotlines": {"items": ("number", "name", "text")},
}

# route 块的支线端点（非站点）：起点 pin 与终点旗
ROUTE_PSEUDO_NODES = {"start", "end"}


def load_basics() -> dict:
    """加载并结构校验内置普法内容，坏结构在启动期直接失败。"""
    try:
        data = json.loads(BASICS_FILE.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"法律常识内容无法读取：{BASICS_FILE.name}") from exc
    for field in ("updated_at", "disclaimer"):
        if not data.get(field):
            raise RuntimeError(f"法律常识缺少字段：{field}")
    modules = data.get("modules")
    if not isinstance(modules, list) or not modules:
        raise RuntimeError("法律常识缺少 modules 内容")
    seen: set[str] = set()
    for module in modules:
        if not isinstance(module, dict):
            raise RuntimeError("法律常识模块必须是 JSON 对象")
        for field in ("key", "name", "brief", "blocks"):
            if not module.get(field):
                raise RuntimeError(f"法律常识模块缺少字段：{field}")
        if not MODULE_KEY_RE.fullmatch(module["key"]):
            raise RuntimeError(f"法律常识模块 key 无效：{module['key']}")
        if module["key"] in seen:
            raise RuntimeError(f"法律常识模块 key 重复：{module['key']}")
        seen.add(module["key"])
        _check_blocks(module["key"], module["blocks"])
    return data


def _check_blocks(module_key: str, blocks) -> None:
    if not isinstance(blocks, list) or not blocks:
        raise RuntimeError(f"法律常识模块缺少内容块：{module_key}")
    for block in blocks:
        block_type = block.get("type") if isinstance(block, dict) else None
        if block_type not in BLOCK_SPECS:
            raise RuntimeError(f"法律常识块类型无效（{module_key}）：{block_type}")
        if not block.get("title"):
            raise RuntimeError(f"法律常识块缺少标题（{module_key}/{block_type}）")
        for field, item_fields in BLOCK_SPECS[block_type].items():
            items = block.get(field)
            if not isinstance(items, list) or not items:
                raise RuntimeError(f"法律常识块缺少 {field}（{module_key}/{block_type}）")
            if block_type == "table":
                if field == "rows" and any(
                        not isinstance(row, list)
                        or len(row) != len(block["columns"]) for row in items):
                    raise RuntimeError(f"法律常识表格行列不齐（{module_key}）")
                continue
            for item in items:
                if not item_fields:
                    # notes 等清单型条目是纯字符串
                    if not isinstance(item, str) or not item.strip():
                        raise RuntimeError(
                            f"法律常识 {field} 条目必须是非空字符串（{module_key}/{block_type}）")
                    continue
                if not isinstance(item, dict):
                    raise RuntimeError(
                        f"法律常识 {field} 条目必须是对象（{module_key}/{block_type}）")
                for name in item_fields:
                    if not item.get(name):
                        raise RuntimeError(
                            f"法律常识 {field} 条目缺少 {name}（{module_key}/{block_type}）")
        if block_type == "route":
            _check_route(module_key, block)


def _check_route(module_key: str, block: dict) -> None:
    """route 块附加校验：站点 key 唯一、坐标为正整数、道路端点可解析。"""
    stations = block["stations"]
    keys: set[str] = set()
    for station in stations:
        if station["key"] in keys:
            raise RuntimeError(f"路线图站点 key 重复（{module_key}）：{station['key']}")
        keys.add(station["key"])
        for axis in ("col", "row"):
            value = station[axis]
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise RuntimeError(
                    f"路线图站点 {axis} 必须是正整数（{module_key}/{station['key']}）")
    legend = block.get("legend")
    if legend is not None and (
            not isinstance(legend, dict) or not legend.get("title") or not legend.get("text")):
        raise RuntimeError(f"路线图图例缺少标题或正文（{module_key}）")
    for road in block["roads"]:
        for end in ("from", "to"):
            node = road[end]
            if node not in keys and node not in ROUTE_PSEUDO_NODES:
                raise RuntimeError(f"路线图道路端点未定义（{module_key}）：{node}")


BASICS = load_basics()


def _module_meta(module: dict) -> dict:
    return {
        "key": module["key"],
        "name": module["name"],
        "sigil": module.get("sigil", ""),
        "brief": module["brief"],
        "block_count": len(module["blocks"]),
    }


def public_basics_index() -> dict:
    """「法律常识」Tab 的卡片索引：只给模块元数据，不携带正文。"""
    return {
        "updated_at": BASICS["updated_at"],
        "disclaimer": BASICS["disclaimer"],
        "modules": [_module_meta(module) for module in BASICS["modules"]],
    }


def public_basics_article(module_key: str) -> dict | None:
    """单篇普法文章视图：正文 + 上下篇导航；未知 key 返回 None。"""
    modules = BASICS["modules"]
    for index, module in enumerate(modules):
        if module["key"] != module_key:
            continue
        prev_module = modules[index - 1] if index > 0 else None
        next_module = modules[index + 1] if index + 1 < len(modules) else None
        return {
            "module": module,
            "prev": _module_meta(prev_module) if prev_module else None,
            "next": _module_meta(next_module) if next_module else None,
            "updated_at": BASICS["updated_at"],
            "disclaimer": BASICS["disclaimer"],
        }
    return None
