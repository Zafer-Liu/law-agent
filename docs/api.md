# API 参考

## 1. 通用约定

- 基础路径：与页面同源。
- JSON 请求：`Content-Type: application/json`。
- 流式接口：响应类型 `text/event-stream`，每个事件格式为 `data: {JSON}\n\n`。
- Flask `abort()` 当前可能返回 HTML 错误正文；调用方不能假定非 2xx 一定是 JSON，应提供通用回退提示。流开始后还要单独处理 SSE `error` 事件。
- 没有版本化前缀。任何不兼容变更都必须同步更新 `web/` 和本文档。

## 2. 系统接口

### `GET /`

返回 `web/index.html` 应用入口。

### `GET /assets/<path:name>`

从 `web/` 返回同源静态资源，目前主要是 `styles.css`、`app.js` 和 `court.js`。该路由不经过独立前端构建或 CDN manifest。

### `GET /health`

用于 Railway 健康检查。

```json
{"ok": true}
```

### `GET /api/config`

返回能力配置，不返回任何密钥。

```json
{
  "llm_configured": true,
  "court_llm_configured": true,
  "embed_configured": false,
  "jev_configured": true,
  "retrieval_backend": "lexical+fts5"
}
```

## 3. 知识库接口

### `GET /api/kb`

返回已索引资料及 embedding 是否配置。

```json
{
  "sources": [
    {
      "id": "...",
      "title": "法条 · 中华人民共和国民法典...",
      "filename": "中华人民共和国民法典.txt",
      "chunk_count": 107,
      "created_at": "2026-09-24T00:00:00.000Z"
    }
  ],
  "embed_configured": false
}
```

### `POST /api/kb`

上传并立即索引资料。请求必须是 `multipart/form-data`。

| 字段 | 类型 | 必需 | 说明 |
|---|---|---:|---|
| `file` | 文件 | 是 | `.md`、`.markdown`、`.txt`、`.docx` |
| `title` | 文本 | 否 | 最长 120 字；默认文件名 stem；必须唯一 |

成功返回 `201`：

```json
{
  "id": "...",
  "title": "资料标题",
  "filename": "source.docx",
  "chunk_count": 12,
  "vector_indexed": true
}
```

常见错误：缺少文件 `400`、格式/内容无效 `400`、同名标题 `409`、请求超过 16 MiB `413`。

> 当前接口没有管理员鉴权。不要在开放生产环境向不可信用户暴露上传入口。

## 4. 对话接口

### `GET /api/conversations?client_id=...`

列出该浏览器标识下最近 60 个有消息的会话。

`client_id` 必须是 8–128 字符。前端将随机 UUID 保存在 localStorage 的 `faxiao_client_id`。

```json
{
  "conversations": [
    {
      "id": "...",
      "title": "合同违约后怎么办？",
      "created_at": "...",
      "updated_at": "...",
      "preview": "..."
    }
  ]
}
```

### `GET /api/conversations/<conversation_id>?client_id=...`

返回会话和消息。标识不匹配时响应中的 `conversation` 为 `null`。

```json
{
  "conversation": {"id": "...", "title": "..."},
  "messages": [
    {"id": "...", "role": "user", "content": "...", "created_at": "..."},
    {
      "id": "...",
      "role": "assistant",
      "content": "... [C1]",
      "citations": [],
      "grounded": true,
      "notice": "...",
      "backend": "lexical+fts5",
      "created_at": "..."
    }
  ]
}
```

### `POST /api/ask`

提交法律问题并接收 SSE。

```json
{
  "question": "合同违约后可以要求什么？",
  "client_id": "浏览器生成的至少 8 字符标识",
  "conversation_id": "可选；继续既有会话",
  "request_id": "客户端为本次提交生成的 UUID",
  "remember": false
}
```

限制：`question` 为 2–2000 字。不存在或不属于当前 `client_id` 的 `conversation_id` 会被忽略并创建新会话。只有 `remember` 严格为 `true` 且回答带有有效法律依据时，才会异步生成一条宠物咨询记忆。

典型事件：

```text
data: {"type":"status","text":"正在查找相关法律…"}
data: {"type":"status","text":"正在生成回答…","backend":"lexical+fts5"}
data: {"type":"delta","text":"根据"}
data: {"type":"delta","text":"……"}
data: {"type":"result", ...}
```

`result`：

```json
{
  "type": "result",
  "content": "回答正文 [C1]",
  "citations": [
    {
      "label": "C1",
      "source": "中华人民共和国民法典",
      "article": "第五百七十七条",
      "section": "中华人民共和国民法典 / 第五百七十七条",
      "excerpt": "条文正文……",
      "chunk_id": 123,
      "ordinal": 8
    }
  ],
  "grounded": true,
  "notice": "回答仅参考……",
  "backend": "lexical+fts5",
  "conversation_id": "..."
}
```

如果模型调用在流中失败，返回：

```json
{"type":"error","error":"模型请求失败或超时。"}
```

### `GET /api/pet?client_id=...`

返回该浏览器标识下的獬豸阶段、外观变化、经验、问答/庭审次数、最近 5 条成长记录，以及最近 5 条咨询记忆。`client_id` 采用与对话接口相同的匿名浏览器标识。

问答奖励为 2 经验，完成庭审为 10 经验。奖励按 `request_id` 或庭审会话的唯一事件 ID 去重；使用同一 `request_id` 重试不会重复加经验。无有效依据的兜底回答不奖励经验，也不生成记忆。

### `DELETE /api/pet/memories`

清空当前匿名浏览器标识下的全部咨询记忆：

```json
{"client_id":"浏览器生成的至少 8 字符标识"}
```

成功返回删除数量：`{"deleted": 3}`。关闭前端的咨询记忆开关只会停止生成新记忆，不会自动删除已有内容。

### `DELETE /api/pet/memories/{memory_id}`

删除一条属于当前匿名浏览器标识的咨询记忆，请求体仍为 `{"client_id":"..."}`。无法删除其他浏览器标识下的记录；未找到时返回 `{"deleted": 0}`。

## 5. 模拟法庭案例接口

### `GET /api/court/cases`

返回案例卡片所需摘要，不返回卷宗和真实判决。

### `GET /api/court/cases/<case_id>`

返回案情、当事人、争点、卷宗、选项等前端庭审数据。真实判决不会在开庭前返回。

## 6. 模拟法庭流式接口

### `POST /api/court/cases/<case_id>/trial`

开始新庭审。

```json
{"role":"juror"}
```

`role` 可取：`audience`、`juror`、`prosecutor`、`defender`、`defendant`，省略时为 `juror`。`audience` 会连续运行到最终评议，不产生中途检查点。

首个事件包含后续请求所需的 `session_id`：

```json
{"type":"session","session_id":"...","case_id":"haicheng","player_role":"juror"}
```

流会在检查点或 `await_deliberation` 结束，不会保持连接等待用户输入。

### `POST /api/court/sessions/<session_id>/continue`

处理当前检查点并继续庭审。

陪审员调查后预判与可选发问：

```json
{
  "poll": "C",
  "question": "E4 说明冲突只有 11 秒，如何认定侵害已结束？",
  "target": "prosecutor"
}
```

- `poll` 必须匹配案例 `options[].id`。
- `question` 可选，最长 200 字。
- 有问题时 `target` 必须是 `prosecutor`、`defender`、`defendant`。

陪审员第二次预判：

```json
{"poll":"B"}
```

诉讼角色发言：

```json
{"statement":"结合监控视频（E4），我认为……"}
```

`statement` 必须为 10–500 字。后端根据已保存的检查点判断请求类型，客户端不能任意指定阶段。

### `POST /api/court/sessions/<session_id>/deliberation`

提交最终评议并获取真实判决和 AI 复盘。

```json
{
  "option": "C",
  "years": 0,
  "marks": ["E4", "E5"]
}
```

- `option` 必须匹配案例选项。
- 只有定罪选项使用 `years`，后端会限制在该选项的 `years_range`。
- `marks` 只接受卷宗中存在的证据编号，最多 8 个。
- 同一会话只能成功评议一次；重复请求返回 `409`。

## 7. 模拟法庭 SSE 事件

| `type` | 主要字段 | 含义 |
|---|---|---|
| `session` | `session_id`, `case_id`, `player_role` | 新会话 |
| `phase` | `phase`, `title` | 庭审阶段切换 |
| `line` | `role`, `name`, `badge`, `text`, `player?` | 完整程序台词或用户发言 |
| `turn_start` | `role`, `name`, `badge` | Agent 发言开始 |
| `delta` | `text` | 发言增量 |
| `turn_end` | `role` | Agent 发言结束 |
| `summary` | `text` | 书记员阶段摘要 |
| `checkpoint` | `kind`, `next`, 其他表单字段 | 暂停并等待用户 |
| `resume` | `kind` | 已接受检查点输入 |
| `await_deliberation` | `options`, `player_role` | 等待最终评议 |
| `verdict` | `real`, `yours`, `trajectory`, `contributions` | 真实判决与用户轨迹 |
| `result` | `analysis` | 审判长复盘完成 |
| `error` | `error` | 流内错误，可按当前检查点重试 |

## 8. 错误处理约定

| 状态 | 场景 |
|---:|---|
| `400` | 参数、长度、格式、选项或当前角色输入无效 |
| `404` | 案例或庭审会话不存在 |
| `409` | 同名资料、无待处理检查点、会话已结束或重复评议 |
| `413` | 上传请求超过 16 MiB |
| `503` | 对应 LLM 未配置 |

对 SSE 接口，HTTP `200` 后仍可能出现 `error` 事件。前端必须保留用户输入和重试入口。
