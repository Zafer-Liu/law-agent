# 法晓开发文档

本文档集面向接手开发、部署和维护「法晓 · AI 法律助手」的工程人员。内容以 2026-09-24 的 `main` 分支实现为准；代码行为与文档冲突时，以代码和自动化测试为准，并应同步修正文档。

## 文档导航

| 文档 | 内容 | 建议读者 |
|---|---|---|
| [系统架构](architecture.md) | 系统边界、组件、数据流、RAG、模拟法庭状态机、持久化 | 全体开发者 |
| [本地开发](development.md) | 环境配置、启动、调试、测试、编码约定 | 开发与测试人员 |
| [API 参考](api.md) | HTTP 接口、请求参数、SSE 事件与错误码 | 前后端开发者 |
| [维护手册](maintenance.md) | 法条/判例/模拟法庭内容维护、数据库、备份、安全与排障 | 维护人员 |
| [部署手册](deployment.md) | Railway 部署、环境变量、持久卷、验证与回滚 | 部署与运维人员 |

## 项目速览

- 产品功能：引用式法律问答、可选角色的多 Agent 模拟法庭。
- 后端：Flask、SQLite、FTS5、jieba、OpenAI 兼容模型接口。
- 前端：原生 HTML、CSS、JavaScript，无构建步骤。
- 法律问答模型：生产环境预期为 `MiniMax-M3`。
- 模拟法庭模型：生产环境预期为 `MiniMax-M2.7-highspeed`。
- 生产地址：<https://web-production-8fccca.up.railway.app/>
- Railway 持久化目录：`/data`，应用通过 `LAW_DATA_DIR=/data` 使用它。

## 代码入口

```text
legalweb/app.py                 Flask 应用工厂、HTTP API、SSE 编排
legalweb/qa.py                  法律问答提示词、逐条证据、引用校验
legalweb/rag.py                 词法/FTS/向量检索和 RRF 融合
legalweb/documents.py           Markdown/TXT/DOCX 解析与分块
legalweb/database.py            SQLite 表结构、迁移与事务
legalweb/llm.py                 OpenAI 兼容 LLM 客户端与备用模型
legalweb/jev_gate.py            可选 Jev 相关性/提示注入门控
legalweb/courtroom/engine.py    模拟法庭状态机
legalweb/courtroom/agents.py    模拟法庭角色约束和提示词
legalweb/courtroom/cases.py     模拟法庭案例加载与校验
web/                            零构建前端
assset/law/                     法律问答内置法条
assset/case/                    法律问答内置判例
assset/moot-court/              模拟法庭案例 JSON
seed/                           小型种子知识库
tests/                          无外部模型依赖的回归测试
test_court_e2e.py               需要真实模型的模拟法庭端到端测试
```

> 注意：仓库目录名当前就是 `assset`（三个 `s`）。不要擅自改为 `asset`，除非同时更新全部代码和部署数据路径。

## 维护原则

1. 法律结论必须可追溯到本次检索证据；不要绕过引用白名单。
2. 模拟法庭只能使用案例卷宗和案例内置法条；程序流程由状态机控制，不交给模型自由决定。
3. 密钥只放环境变量，不写入代码、文档示例值、日志或 Git 历史。
4. 数据结构变更必须提供幂等迁移，并验证现有 Railway 持久卷。
5. 修改 API 或 SSE 事件时，同时更新前端、测试和本目录文档。

