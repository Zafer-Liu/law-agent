"""问答人设（回答模式）：普通模式 / 专业模式。

两个提示词分文件维护：
- ordinary.py     普通模式（面向普通公众，通俗解释）
- professional.py 专业模式（面向法学院学生/律师/法务，专业分析）

非法取值一律回退普通模式。
"""

from .ordinary import KEY as ORDINARY_KEY
from .ordinary import SYSTEM_PROMPT as ORDINARY_PROMPT
from .professional import KEY as PROFESSIONAL_KEY
from .professional import SYSTEM_PROMPT as PROFESSIONAL_PROMPT

PERSONAS = {
    ORDINARY_KEY: ORDINARY_PROMPT,
    PROFESSIONAL_KEY: PROFESSIONAL_PROMPT,
}


def persona_prompt(mode: str) -> str:
    """按问答模式返回对应系统提示词；未知值回退普通模式。"""
    return PERSONAS.get((mode or "").strip(), ORDINARY_PROMPT)
