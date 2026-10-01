# 法晓 · AI 法律助手

> 2026 中国（广西）—东盟"AI+法律"应用创新大赛 · 高校学生赛道 · 法治宣传方向

一个面向公众的 AI 普法应用，包含两大功能：

1. **法律问答** — 面向普通人处理民事、合同与刑事问题的 RAG 引用式问答；内置《民法典》与《刑法》普法节选，每个回答标注引用来源
2. **沉浸模拟法庭** — 法庭式布局的多智能体庭审对抗（审判长 ⇄ 公诉人 ⇄ 辩护人 ⇄ 被告人）。用户可作为旁听观众观看全程，或选择人民陪审员、公诉人、辩护人、被告人：陪审员进行预判和发问，诉讼角色在两个关键节点从预设意见或自定义内容中完成发言；庭审支持暂停，最后可作出裁判预测并与真实判决对照

## 快速开始

```bash
pip install -r requirements.txt
cp .env.example .env   # 编辑 .env 填入模型配置
python -m legalweb
```

打开 http://127.0.0.1:5000

## 配置

| 环境变量 | 说明 |
|---|---|
| `LAW_LLM_BASE_URL` | OpenAI 兼容接口地址（以 /v1 结尾） |
| `LAW_LLM_API_KEY` | API Key |
| `LAW_LLM_MODEL` | 法律问答模型，默认 `MiniMax-M3` |
| `LAW_COURT_LLM_BASE_URL` | 模拟法庭的 OpenAI 兼容接口地址 |
| `LAW_COURT_LLM_API_KEY` | 模拟法庭 API Key |
| `LAW_COURT_LLM_MODEL` | 模拟法庭模型，默认 `MiniMax-M2.7-highspeed` |
| `LAW_EMBED_BASE_URL` | （可选）向量服务地址，配置后启用语义检索 |
| `LAW_EMBED_API_KEY` | （可选）向量服务 Key |
| `TYPESAFE_API_KEY` | （可选）TypeSafe API Key，配置后启用 Jev 检索质量门控 |
| `LAW_JEV_GATE` | Jev 开关，默认 `1`；设为 `0` 可关闭 |

未配置向量服务时自动降级为词法 + FTS5 检索，界面会明确标注。

## 技术架构

- **后端**：Flask + SQLite/FTS5 + jieba 词法检索 + 可选向量（RRF 融合）+ 可选 Jev 相关度/提示注入门控
- **前端**：原生 HTML/JS/CSS，零构建步骤
- **LLM**：OpenAI 兼容接口，支持备用模型自动切换
- **模拟法庭**：多 Agent 编排（公诉人/辩护人/被告人/书记员），程序状态机保证庭审流程合法性，封闭卷宗接地防幻觉；旁听角色不打断流程，其他角色在「法庭调查后」「法庭辩论后」进入相应检查点，通过 `/continue` 续跑；前端可暂停事件播放并保留当前发言，评议复盘含用户判断轨迹

## 目录结构

```
legalweb/          # Python 后端包
  app.py           # Flask 路由 + SSE
  rag.py           # 检索引擎（词法 + FTS5 + 向量 RRF）
  llm.py           # OpenAI 兼容客户端
  qa.py            # 法律问答（RAG + 引用校验）
  courtroom/       # 模拟法庭
    cases.py       # 从内置资产加载并校验模拟法庭案例
    agents.py      # 角色卡（公诉人/辩护人/被告人/书记员）
    engine.py      # 庭审状态机 + SSE 事件流
assset/
  law/             # 法律问答内置法条
  case/            # 法律问答内置判例
  moot-court/      # 模拟法庭案例 JSON
web/               # 前端（原生 HTML/JS/CSS）
seed/              # 种子法律知识库（法条节选）
```

## 开发文档

完整的架构、API、本地开发、维护和 Railway 部署说明见 [`docs/README.md`](docs/README.md)。

## 免责声明

本应用提供的法律信息仅供参考，不构成法律意见。法条内容为演示用节选，正式文本以国家法律法规数据库为准。模拟法庭案例根据公开指导性案例改编，当事人均系化名。
