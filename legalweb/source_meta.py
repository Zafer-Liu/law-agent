"""法律来源效力元数据：解析内置法条文件头部的北大法宝著录信息。

内置法条 TXT 文件普遍带有「制定机关：/发文字号：/公布日期：/施行日期：/
时效性：/效力位阶：」著录头；解析后存入 sources.meta，随引用下发前端，
让用户看到每条依据的效力状态、发布与施行信息。无法解析的来源显式标注
「未确认」，不猜测效力。

北大法宝在线检索的 timeliness 字段（现行有效/已被修改/废止或失效）与
本模块共用同一套状态归一化。
"""

import json
import re

# 状态键 → (展示名, 提示语)。前端按状态键着色。
STATUS_INFO = {
    "current": ("现行有效", ""),
    "amended": ("已被修改", "该文本已被修改，请核对是否为最新版本"),
    "repealed": ("废止或失效", "该法规已废止或失效，仅供历史参考"),
    "unknown": ("未确认", "未收录该资料的效力信息，请以官方发布文本为准"),
}

_TIMELINESS_MAP = (
    (("现行有效", "有效"), "current"),
    (("已被修改", "已修改", "已修订"), "amended"),
    (("废止", "失效"), "repealed"),
)

# 著录头字段名（用于切段与截值）
_FIELDS = ("制定机关", "发文字号", "公布日期", "施行日期", "时效性",
           "效力位阶", "法规类别", "专题分类", "专题", "废止或失效依据",
           "失效依据", "机构沿革", "批准日期", "实施日期")
_FIELD_TAIL_RE = re.compile(
    r"\s*(" + "|".join(map(re.escape, _FIELDS)) + r")\s*$")
_REGION_LOCAL_RE = re.compile(r"省|市|自治区|特别行政区|自治州")

# 个别内置法条文件没有著录头（正文直接从法条开始），按展示标题补录。
BUILTIN_META = {
    "中华人民共和国刑法(2023修正)": {
        "status": "current",
        "published": "2023.12.29",
        "effective": "2024.03.01",
        "authority": "全国人民代表大会",
        "document_number": "",
        "note": "2023 年修正后的现行文本（含刑法修正案（十二））",
    },
    "中华人民共和国刑法修正案(十二)": {
        "status": "current",
        "published": "2023.12.29",
        "effective": "2024.03.01",
        "authority": "全国人民代表大会常务委员会",
        "document_number": "主席令第十八号",
    },
}


def status_from_timeliness(value: str) -> str:
    """把北大法宝时效性文字归一化为状态键；无法识别返回 unknown。"""
    text = (value or "").strip()
    for keywords, status in _TIMELINESS_MAP:
        if any(keyword in text for keyword in keywords):
            return status
    return "unknown" if text else ""


def _split_header_fields(head: str) -> dict:
    """按「字段名：」把著录头切段，取每个字段的值（不含冒号本身）。"""
    segments = re.split("[：:]", head)
    fields: dict[str, str] = {}
    next_field = ""
    for index, segment in enumerate(segments):
        match = _FIELD_TAIL_RE.search(segment)
        value = segment if match is None else segment[:match.start()]
        if index > 0 and next_field:
            value = value.strip().strip("　 ")
            if value and next_field not in fields:
                fields[next_field] = value[:80]
        if match is not None:
            next_field = match.group(1)
        elif index > 0:
            # 段尾没有接续字段名：前一字段的值到此为止
            next_field = ""
    return fields


def parse_source_meta(head_text: str, title: str = "") -> dict:
    """从文档开头解析效力元数据；解析不到但有补录条目时用补录。

    返回空 dict 表示该来源没有可确认的元数据（前端按「未确认」展示）。
    """
    display = re.sub(r"^(?:法条|判例)\s*·\s*", "", (title or "").strip())
    for builtin_title, meta in BUILTIN_META.items():
        if display == builtin_title:
            return dict(meta)

    fields = _split_header_fields((head_text or "")[:2000])
    timeliness = fields.get("时效性", "")
    status = status_from_timeliness(timeliness)
    if not status:
        # 没有时效性著录的来源不做任何猜测
        return {}
    authority = fields.get("制定机关", "").strip()
    meta = {
        "status": status,
        "published": fields.get("公布日期", "")[:20],
        "effective": fields.get("施行日期", fields.get("实施日期", ""))[:20],
        "authority": authority[:40],
        "document_number": fields.get("发文字号", "")[:40],
    }
    superseded_by = fields.get("废止或失效依据", fields.get("失效依据", ""))
    if superseded_by:
        meta["superseded_by"] = superseded_by[:120]
    if authority:
        meta["region"] = "地方" if _REGION_LOCAL_RE.search(authority) else "全国"
    return meta


def build_validity(meta_text: str = "", *, timeliness: str = "",
                   indexed_at: str = "") -> dict:
    """把 sources.meta JSON 或在线 timeliness 归一为前端展示结构。"""
    meta = {}
    if meta_text:
        try:
            loaded = json.loads(meta_text)
            if isinstance(loaded, dict):
                meta = loaded
        except ValueError:
            meta = {}
    status = meta.get("status") or ""
    if not status and timeliness:
        status = status_from_timeliness(timeliness) or "unknown"
    if not status:
        status = "unknown"
    label, note = STATUS_INFO.get(status, STATUS_INFO["unknown"])
    validity = {
        "status": status,
        "label": timeliness.strip() if (timeliness.strip() and not meta) else label,
        "published": str(meta.get("published") or "")[:20],
        "effective": str(meta.get("effective") or "")[:20],
        "authority": str(meta.get("authority") or "")[:40],
        "region": str(meta.get("region") or "")[:10],
        "document_number": str(meta.get("document_number") or "")[:40],
        "superseded_by": str(meta.get("superseded_by") or "")[:120],
        "note": meta.get("note") or note,
        "indexed_at": (indexed_at or "")[:10],
    }
    if not meta and timeliness:
        validity["authority"] = "北大法宝在线检索"
    return validity
