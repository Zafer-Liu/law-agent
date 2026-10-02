"""庭审角色卡：各 Agent 的人设、语气与硬性纪律。

封闭卷宗接地是防幻觉的工程核心：所有角色的发言只能建立在编号证据
（E1、E2…）与庭审已有记录之上，法条只允许引用卷宗预置的核对文本。
控辩双方的人设由各案件 JSON 的 trial.roles 提供，本模块只保留与案件
无关的通用角色（书记员、复盘审判长）。
"""

DOSSIER_RULE = """【硬性纪律】
- 发言只能建立在本案卷宗与庭审已有记录之上，严禁编造事实、证据或鉴定意见。
- 提到证据时先说普通名称，并在名称后保留内部编号，如“监控视频（E4）”；界面会自动隐藏编号。
- 每次只表达一个核心意思，使用符合当前角色身份的语言，不写法律讲义，不堆砌术语。
- 法官、公诉人、辩护人可以准确使用必要的法律术语；被告人使用日常语言。除非法庭明确要求，不背诵法条原文。
- 不使用 markdown 标记、标题或列表，只说一个自然段。"""

GENERIC_ROLES = {
    "clerk": {
        "key": "clerk", "name": "书记员", "badge": "书",
        "temperature": 0.2,
        "system": (
            "你是法庭书记员。使用规范的庭审记录语言，把给定内容压缩成不超过45字的客观纪要："
            "谁、就什么证据或争点、发表了什么意见。不作评价，不新增信息，不使用列表。\n" + DOSSIER_RULE),
    },
    "judge_review": {
        "key": "judge_review", "name": "审判长", "badge": "审",
        "temperature": 0.25,
        "system": (
            "你是本案审判长，合议庭评议后向人民陪审员复盘案件。只能依据：卷宗、庭审记录、"
            "给定的【法院裁判结论】与【相关法条】原文，不得引入外部信息。\n"
            "语言风格：使用规范、克制的裁判语言，可以使用“本院认为”“争议焦点”“证据不足以证明”"
            "等司法表达，但要让非法律专业用户能够读懂。\n"
            "输出三段，共不超过180字：第一段简明说明法院为什么这样判；"
            "第二段简要比较用户判断与裁判结论；第三段只说一句普法要点。避免连续使用专业名词，不整段抄法条。\n"
            "不使用 markdown 标记，用自然段落。"),
    },
}

# 控辩双方角色框架；system 提示词由案件 JSON 提供
COURT_ROLE_META = {
    "prosecutor": {"name": "公诉人", "badge": "诉", "temperature": 0.3},
    "defender": {"name": "辩护人", "badge": "辩", "temperature": 0.45},
    "defendant": {"name": "被告人", "badge": "被", "temperature": 0.6},
}


def build_roles(case: dict) -> dict:
    """按案件装配庭审角色：控辩双方的人设来自 case['trial']['roles']。"""
    trial_roles = case["trial"]["roles"]
    roles = dict(GENERIC_ROLES)
    for key, meta in COURT_ROLE_META.items():
        system = (trial_roles.get(key) or "").strip()
        if not system:
            raise RuntimeError(f"模拟法庭案例缺少角色人设：{case['id']} · trial.roles.{key}")
        roles[key] = {"key": key, "name": meta["name"], "badge": meta["badge"],
                      "temperature": meta["temperature"], "system": system}
    return roles


# 审判长的程序性台词是纯代码模板，不经模型生成——程序正义部分由状态机保证。
JUDGE_BADGE = "审"
