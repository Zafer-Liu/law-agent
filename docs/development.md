# 本地开发

## 1. 前置条件

- Windows、macOS 或 Linux；仓库当前主要在 Windows PowerShell 下维护。
- Python 3.10 或更高版本。
- 支持 FTS5 的 SQLite（官方 Python 通常已包含）。
- 至少一套 OpenAI Chat Completions 兼容模型配置，才能运行需要生成的功能。

前端没有 Node.js 构建步骤。

## 2. 创建环境

PowerShell：

```powershell
cd F:\Projects\law-agent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Bash：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## 3. 环境变量

`python -m legalweb` 会通过 `python-dotenv` 自动读取仓库根目录的 `.env`。该文件已被 `.gitignore` 排除，禁止提交。

最小开发配置示例：

```dotenv
LAW_LLM_BASE_URL=https://your-provider.example/v1
LAW_LLM_API_KEY=replace-me
LAW_LLM_MODEL=MiniMax-M3

LAW_COURT_LLM_BASE_URL=https://your-provider.example/v1
LAW_COURT_LLM_API_KEY=replace-me
LAW_COURT_LLM_MODEL=MiniMax-M2.7-highspeed

LAW_DATA_DIR=.law-data
LAW_PORT=5000
```

可选检索配置：

```dotenv
LAW_EMBED_BASE_URL=https://your-embedding-provider.example/v1
LAW_EMBED_API_KEY=replace-me
LAW_EMBED_MODEL=bge-large-zh

TYPESAFE_API_KEY=replace-me
LAW_JEV_GATE=1
```

### 3.1 完整变量表

| 变量 | 必需 | 默认值 | 说明 |
|---|---:|---|---|
| `LAW_LLM_BASE_URL` | 是 | 无 | 法律问答 OpenAI 兼容根地址；无路径时自动补 `/v1` |
| `LAW_LLM_API_KEY` | 视提供方 | 空 | 法律问答 Bearer token |
| `LAW_LLM_MODEL` | 是 | 无 | 生产预期 `MiniMax-M3` |
| `LAW_LLM_MAX_TOKENS` | 否 | `8000` | 提供方上限，限制在 500–200000 |
| `LAW_LLM_FALLBACK_BASE_URL` | 否 | 无 | 法律问答备用提供方 |
| `LAW_LLM_FALLBACK_API_KEY` | 否 | 空 | 备用密钥 |
| `LAW_LLM_FALLBACK_MODEL` | 否 | 无 | 备用模型 |
| `LAW_LLM_FALLBACK_MAX_TOKENS` | 否 | 主模型上限 | 备用上限 |
| `LAW_COURT_LLM_BASE_URL` | 是 | 无 | 模拟法庭 OpenAI 兼容根地址 |
| `LAW_COURT_LLM_API_KEY` | 视提供方 | 空 | 模拟法庭 Bearer token |
| `LAW_COURT_LLM_MODEL` | 是 | 无 | 生产预期 `MiniMax-M2.7-highspeed` |
| `LAW_COURT_LLM_MAX_TOKENS` | 否 | `8000` | 模拟法庭提供方上限 |
| `LAW_COURT_LLM_FALLBACK_*` | 否 | 无 | 与法律问答相同结构的备用配置 |
| `LAW_EMBED_BASE_URL` | 否 | 无 | 向量服务地址；配置地址和模型后启用 |
| `LAW_EMBED_API_KEY` | 否 | 空 | 向量服务密钥 |
| `LAW_EMBED_MODEL` | 否 | `bge-large-zh` | embedding 模型 |
| `TYPESAFE_API_KEY` | 否 | 无 | Jev 服务密钥 |
| `LAW_JEV_GATE` | 否 | `1` | `0/false/off/no` 关闭 Jev |
| `LAW_JEV_REL_MIN` | 否 | `2.0` | Jev 最低相关度 |
| `LAW_JEV_INJ_MAX` | 否 | `0.5` | Jev 最大提示注入分数 |
| `LAW_JEV_EXCERPT_CHARS` | 否 | `600` | 每块发送给 Jev 的字符数，限制 100–2000 |
| `LAW_DATA_DIR` | 否 | `.law-data` | SQLite 和上传文件目录 |
| `LAW_PORT` | 否 | `PORT` 或 `5000` | 本地服务器端口，优先级最高 |
| `PORT` | 部署注入 | `5000` | Railway/Gunicorn 使用的端口 |

只有在首个可见流式 token 产生前发生失败时，法律问答才会切换备用模型，防止前端出现重复回答。

## 4. 启动

```powershell
python -m legalweb
```

打开 <http://127.0.0.1:5000>，健康检查为 <http://127.0.0.1:5000/health>。

首次启动会导入内置知识库。未配置 embedding 时不会报错，而是只建立词法和 FTS5 索引。开发服务器只监听 `127.0.0.1`；局域网访问应使用明确的反向代理或生产 WSGI 服务，不要直接修改成公开 debug server。

## 5. 开发调试

### 5.1 法律问答

重点观察 SSE 事件顺序：

```text
status(检索) → status(生成) → delta* → result
```

后端发生模型错误时是 `error` 事件，不一定是非 200 HTTP 状态。浏览器 Network 面板中应检查 `/api/ask` 的流式响应内容。

### 5.2 模拟法庭

一个完整的陪审员流程会发起：

```text
POST /trial
POST /continue       # 调查后预判/发问
POST /continue       # 辩论后复核
POST /deliberation   # 最终评议
```

庭审调用可能持续数分钟。不要用短于模型最长生成时间的代理超时；生产 Gunicorn 当前为 300 秒。

### 5.3 重建本地运行数据

如确认不需要保留本地会话和上传资料，可停止应用后删除项目内的 `.law-data` 再启动。此操作不可用于生产数据。删除前必须确认绝对路径指向本项目的 `.law-data`，不要对模糊环境变量执行递归删除。

## 6. 测试

无模型依赖的回归测试：

```powershell
python -m unittest discover -s tests -v
node --check web\app.js
node --check web\court.js
python -m compileall -q legalweb
```

覆盖范围包括：

- ID 唯一性和输入类型边界；
- LLM 流中 `<think>` 过滤；
- 引用格式规范化、无效引用移除和逐条法条证据；
- 对话按浏览器匿名标识隔离；
- Jev 重排、注入过滤和失败透传。

模拟法庭端到端测试需要本地服务已启动、真实模型配置可用：

```powershell
python test_court_e2e.py
```

该脚本会实际调用模型并写入庭审会话，可能产生费用。它默认访问 `http://127.0.0.1:5000`，验证两次检查点、用户发问、评议轨迹和重复提交保护。

## 7. 修改后的最低验证标准

| 修改类型 | 最低验证 |
|---|---|
| Python 业务逻辑 | 单元测试 + `compileall` |
| 问答检索/引用 | 单元测试 + 一次真实 `/api/ask`，核对 `citations` |
| 模拟法庭状态机 | 单元测试 + `test_court_e2e.py` 或等价的完整检查点流程 |
| 前端交互 | JS 语法检查 + 桌面和移动视口人工验证 |
| 数据库 schema | 空库创建 + 现有数据库升级副本验证 |
| Railway 配置 | `/health`、首页、静态资源、一次业务请求 |

## 8. 编码约定

- Python 保持模块职责清晰；检索算法放在 `rag.py`，回答约束放在 `qa.py`，庭审流程放在 `engine.py`。
- 所有用户输入都应限制类型和长度；沿用 `text_value()` 的失败方式。
- SSE 数据统一由 `sse()` 生成，事件必须包含 `type`。
- 新增模型输出路径时必须过滤思考内容，并明确模型失败后的恢复策略。
- 数据库变更使用可重复执行的增量迁移，不依赖手工先改生产库。
- 前端保持无构建依赖；需要引入框架或打包器时，先记录迁移理由、部署变化和回滚方案。
- 面向用户的界面不要暴露模型、RAG、Jev 等后端技术细节。
