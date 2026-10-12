# 法晓 · Law-Agent

开源的 AI 法律助手：把一个法律问题，转化为**有依据、可理解、可练习**的处理路径。基于 RAG 引用式问答、多智能体模拟法庭与普法内容库，服务于普通人的日常法律求助与法律学习。

- 在线体验：落地页 `/` · 应用 `/app`
- 源码仓库：[Zafer-Liu/law-agent](https://github.com/Zafer-Liu/law-agent)

## 功能

- **法律问答** — 面向民事、合同与刑事问题的引用式问答（RAG）。每个回答标注可核对的引用，并展示每条依据的效力状态（现行有效 / 已被修改 / 废止失效 / 未确认）；支持多轮对话、历史管理（重命名 / 置顶 / 分类 / 导出 Markdown）、反馈，对话可一键脱敏整理成事项档案
- **问题工作台** — 结构化采集案情，检索现行法条后生成可执行的事项档案：问题归类、事实摘要、适用依据、待补信息、证据清单、行动清单、风险提醒，并推荐可练手的模拟庭审
- **模拟法庭** — 多智能体庭审对抗（审判长 ⇄ 公诉人 ⇄ 辩护人 ⇄ 被告人），法庭式布局。可旁听观看，也可选择陪审员或诉讼角色在关键节点发言；庭审支持暂停，裁判预测按证据判断、程序理解、说理完整性三维对照真实判决评分，复盘页内置知识复习卡片
- **法律常识** — 免登录浏览的普法板块，独立文章页（`/basics/<key>`）覆盖诉讼流程、诉讼基本知识、贷款与借钱、求助渠道，多数知识点可带着问题一键跳转问答深究
- **桌宠与学习地图** — 獬豸桌宠伴随学习成长，问答与庭审积累经验、解锁形态；学习地图把知识点、事项档案与庭审记录按主题聚合，掌握进度与待复习法条一目了然
- **账户体系** — 注册 / 登录、数据按账户隔离（匿名期数据登录后自动并入）、全部数据导出、注销删除；首个注册用户自动成为管理员，可管理知识库上传、检索增强开关、日志与统计

## 快速开始

```bash
pip install -r requirements.txt
cp .env.example .env   # 编辑 .env 填入模型配置
python -m legalweb
```

打开 http://127.0.0.1:5000（落地页），应用入口在 http://127.0.0.1:5000/app

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
| `LAW_PKULAW_TOKEN` | （可选）北大法宝 MCP 检索 Token，配置后问答启用在线法条检索（条目附原文链接与时效性） |
| `TYPESAFE_API_KEY` | （可选）TypeSafe API Key，配置后启用 Jev 检索质量门控 |
| `LAW_JEV_GATE` | Jev 开关，默认 `1`；设为 `0` 可关闭 |
| `LAW_WEBSEARCH_API_KEY` | （可选）智谱联网搜索 Key（GLM Coding Plan MCP 或普通智谱 Key），配置后问答输入区出现「联网搜索」开关，开启后自动搜索补充依据并附可跳转的原文链接 |

未配置向量服务时自动降级为词法 + FTS5 检索，界面会明确标注。

## 技术架构

- **后端**：Flask + SQLite/FTS5 + jieba 词法检索 + 可选向量（RRF 融合）+ 可选 Jev 相关度/提示注入门控
- **应用前端**：原生 HTML/JS/CSS，零构建步骤
- **落地页**：React + Vite + Tailwind（`frontend/`，报纸美学设计系统），`npm run build` 产物输出至 `web/landing/`
- **LLM**：OpenAI 兼容接口，支持备用模型自动切换
- **模拟法庭**：多 Agent 编排 + 程序状态机保证庭审流程合法，封闭卷宗接地防幻觉；旁听角色不打断流程，诉讼角色在「法庭调查后」「法庭辩论后」检查点发言，通过 `/continue` 续跑；SSE 事件流支持前端暂停与保留当前发言

## 目录结构

```
legalweb/          # Python 后端包
  app.py           # Flask 路由 + SSE（问答/庭审/账户/反馈/埋点）
  database.py      # SQLite schema 与幂等迁移
  rag.py           # 检索引擎（词法 + FTS + 向量 RRF）
  llm.py           # OpenAI 兼容客户端
  qa.py            # 法律问答（RAG + 引用校验）
  basics.py        # 法律常识内容加载与结构校验
  source_meta.py   # 法条效力元数据解析（时效性/公布/施行）
  courtroom/       # 模拟法庭（cases 案例加载 / agents 角色卡 / engine 状态机）
assset/            # 内置法律资产（law 法条 / case 判例 / moot-court 庭审案例 / legal-basics 普法内容）
frontend/          # 产品落地页源码（React + Vite + Tailwind）
web/               # 应用前端（原生 HTML/JS/CSS）
  app.html         # 应用单页（问答/工作台/法庭/常识/学习），路由 /app
  basics.html      # 普法独立文章页 /basics/<key>
  landing/         # 落地页构建产物，路由 / 与 /share
tests/             # 自动化回归测试（账户隔离/问答/庭审/宠物/反馈埋点）
seed/              # 种子法律知识库（法条节选）
```

## 开发文档

完整的架构、API、本地开发、维护和 Railway 部署说明见 [`docs/README.md`](docs/README.md)。

## 免责声明

本应用提供的法律信息仅供参考，不构成法律意见。法条内容为演示用节选，正式文本以国家法律法规数据库为准。模拟法庭案例根据公开指导性案例改编，当事人均系化名。
