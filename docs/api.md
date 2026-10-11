# API 参考

## 1. 通用约定

- 基础路径：与页面同源。
- JSON 请求：`Content-Type: application/json`。
- 流式接口：响应类型 `text/event-stream`，每个事件格式为 `data: {JSON}\n\n`。
- 账户体系：登录通过 `law_session` Cookie（不透明令牌，30 天）。业务写入类接口（问答、会话、宠物、反馈、埋点）均需登录，匿名返回 `401`；管理员接口返回 `403`。认证类端点按 IP 限流（300 秒 30 次），超限返回 `429`。
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

### `GET /api/basics`

法律常识索引，公开访问、无需登录。只返回文章卡片所需的元数据，由「法律常识」Tab 的卡片列表消费。

```json
{
  "updated_at": "2026年10月",
  "disclaimer": "……普法常识，不构成法律意见……",
  "modules": [
    {"key": "procedure", "name": "诉讼流程", "sigil": "诉", "brief": "……", "block_count": 3},
    {"key": "knowledge", "name": "诉讼基本知识", "sigil": "识", "brief": "……", "block_count": 1},
    {"key": "loan", "name": "贷款与借钱", "sigil": "贷", "brief": "……", "block_count": 2},
    {"key": "help", "name": "求助渠道", "sigil": "援", "brief": "……", "block_count": 2}
  ]
}
```

### `GET /api/basics/<key>`

单篇普法文章，公开访问，返回该模块完整正文与上一篇/下一篇导航；未知模块返回 404。前端配对的独立文章页 `GET /basics/<key>`（返回 `web/basics.html`）由它驱动。

```json
{
  "module": {"key": "procedure", "name": "诉讼流程", "sigil": "诉", "brief": "……",
    "blocks": [
      {"type": "route", "title": "主线与支线：一场官司可能走的路", "hint": "……",
       "legend": {"title": "怎么看这张图", "text": "……"},
       "stations": [{"key": "jurisdiction", "col": 1, "row": 2, "num": "1",
                     "title": "起诉在哪里？", "tag": "管辖", "text": "……",
                     "tips": ["……"], "ref": "……"}],
       "roads": [{"from": "start", "to": "jurisdiction", "label": "决定起诉"},
                 {"from": "trial", "to": "small", "label": "……", "branch": true},
                 {"from": "trial", "to": "enforce", "label": "不上诉 · 十五日后生效", "via": "top"}]},
      {"type": "faq", "title": "……", "items": [
        {"q": "……", "a": "……", "ref": "……", "ask": "预填到问答输入框的问题"}]},
      {"type": "cards", "items": [{"title": "利率有红线", "badge": "红线",
                                    "level": "warn", "text": "……", "ref": "……"}]},
      {"type": "notes", "items": ["清单条目"]}]},
  "prev": null,
  "next": {"key": "knowledge", "name": "诉讼基本知识"},
  "updated_at": "2026年10月",
  "disclaimer": "……"
}
```

块类型共七种：`route`（`stations`/`roads`/`legend`）、`table`（`columns`/`rows`）、`cards`（`items`，可选 `badge`/`level`/`ref`/`ask`）、`faq`（`items`，可选 `ref`/`ask`）、`notes`（字符串清单）、`hotlines`（`items` 含 `number`/`name`/`text`）。`route` 描绘诉讼主线与支线：站点由 `col`/`row` 定位网格（主线在居中行从左到右，支线挂下方行），`branch` 站点与道路渲染为琥珀色支线，道路端点可用伪节点 `start`/`end`，`via: "top"` 表示越过主线行上方的弧线。`ask` 字段驱动「去问法晓」按钮：带问题跳回首页问答并预填该问题。结构由 `legalweb/basics.py` 在启动时校验，改坏 JSON 会直接启动失败。

## 2.1 账户接口

认证基于 `law_session` Cookie（httpOnly、SameSite=Lax、30 天、HTTPS 下自动 Secure），令牌落库，重启部署不掉线。注册/登录可携带 `client_id`，把本浏览器匿名期的对话与宠物数据并入账户。

| 接口 | 说明 |
|---|---|
| `POST /api/auth/register` | `{username, password, client_id?}`。用户名 2–24 位字母/数字/下划线/汉字，密码 6–128 位。首个注册用户自动成为管理员。 |
| `POST /api/auth/login` | `{username, password, client_id?}`。用户名大小写不敏感。 |
| `POST /api/auth/logout` | 删除当前会话令牌并清除 Cookie。 |
| `GET /api/auth/me` | 返回 `{"user": {...}}` 或 `{"user": null}`。 |

注册与登录参与 IP 限流（300 秒 30 次）。改密与注销接口见「4.3 账户数据控制」。

## 3. 知识库接口

### `GET /api/kb`

返回已索引资料、效力标识及 embedding 是否配置。`validity` 来自资料文件头的著录信息（时效性/公布日期/施行日期/制定机关），无法解析时为 `未确认`。

```json
{
  "sources": [
    {
      "id": "...",
      "title": "法条 · 中华人民共和国民法典...",
      "filename": "中华人民共和国民法典.txt",
      "chunk_count": 107,
      "created_at": "2026-09-24T00:00:00.000Z",
      "meta": "{...原始解析结果...}",
      "validity": {
        "status": "current",
        "label": "现行有效",
        "published": "2020.05.28",
        "effective": "2021.01.01",
        "authority": "全国人民代表大会",
        "region": "全国",
        "document_number": "中华人民共和国主席令第45号",
        "superseded_by": "",
        "note": "",
        "indexed_at": "2026-09-24"
      }
    }
  ],
  "embed_configured": false
}
```

`status` 取值：`current`（现行有效）、`amended`（已被修改）、`repealed`（废止或失效）、`unknown`（未确认）。

### `POST /api/kb`

上传并立即索引资料。请求必须是 `multipart/form-data`。**仅管理员**（首个注册用户；后续需要提权时直接操作数据库需谨慎）可调用。

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

常见错误：缺少文件 `400`、格式/内容无效 `400`、同名标题 `409`、请求超过 16 MiB `413`、未登录 `401`、非管理员 `403`。

### `GET /api/settings` · `POST /api/settings`

仅管理员。读取/更新检索增强开关（`vector`、`jev`、`pkulaw`，布尔）。开关状态存 `settings` 表，重启保留。

### `GET /api/logs`

仅管理员。返回内存环形日志最近 200 条（不落盘，重启清空）。

## 4. 对话接口

以下接口均要求登录，数据按账户隔离。

### `GET /api/conversations`

列出当前登录账户最近 60 个有消息的会话。

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

### `GET /api/conversations/<conversation_id>`

返回会话和消息。会话不属于当前账户时响应中的 `conversation` 为 `null`。助手消息带该账户的反馈状态（`feedback`，未评价时省略）。

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
      "feedback": "helpful",
      "created_at": "..."
    }
  ]
}
```

### `DELETE /api/conversations/<conversation_id>`

删除当前登录账户拥有的会话及其全部消息。接口会校验会话归属；目标不存在或不属于当前账户时返回 `deleted: 0`。

```json
{"deleted": 1}
```

### `PATCH /api/conversations/<conversation_id>`

更新会话元数据（重命名、分类）。`title` 需为 1-60 字；`category` 取值：`劳动`、`合同`、`消费`、`侵权`、`婚姻家庭`、`刑事`、`其他` 或空串（清除分类）。两个字段至少传一个。

```json
{"title": "新的对话标题", "category": "劳动"}
```

返回 `{"updated": 1, "title": "...", "category": "..."}`；目标不属于当前账户时 `updated: 0`。

### `POST /api/conversations/<conversation_id>/pin`

置顶或取消置顶。会话列表按 `pinned DESC, updated_at DESC` 排序。

```json
{"pinned": true}
```

### `POST /api/conversations/<conversation_id>/intake`

把多轮对话蒸馏成工作台采集表单（脱敏：提示词要求去除人名/手机号/住址等个人信息）。返回的表单由前端预填，用户确认后才走 `POST /api/matters` 生成报告。

```json
{"intake": {"what": "…", "when_where": "…", "parties": "…", "amount": "…",
            "evidence": "…", "goal": "…", "urgent": "…"}}
```

错误：对话不存在 `404`、LLM 未配置或蒸馏失败 `503`、对话内容不足以整理出案情 `422`。

### `GET /api/conversations/<conversation_id>/export`

把会话导出为 Markdown 附件（含双方发言、时间与引用依据列表），用于线下留存或事项归档。非本人会话返回 `404`。

### `POST /api/ask`

提交法律问题并接收 SSE。要求登录。

```json
{
  "question": "合同违约后可以要求什么？",
  "conversation_id": "可选；继续既有会话",
  "request_id": "客户端为本次提交生成的 UUID",
  "remember": false
}
```

限制：`question` 为 2–2000 字。不存在或不属于当前账户的 `conversation_id` 会被忽略并创建新会话。只有 `remember` 严格为 `true` 且回答带有有效法律依据时，才会异步生成一条宠物咨询记忆。

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
  "message_id": "该回答的消息 ID（用于提交反馈）",
  "content": "回答正文 [C1]",
  "citations": [
    {
      "label": "C1",
      "source": "中华人民共和国民法典",
      "article": "第五百七十七条",
      "section": "中华人民共和国民法典 / 第五百七十七条",
      "excerpt": "条文正文……",
      "validity": {"status": "current", "label": "现行有效", "...": "同 /api/kb 的 validity 结构"},
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

### `GET /api/pet`

返回当前登录账户的獬豸阶段、外观变化、经验、问答/庭审次数、最近 5 条成长记录，以及最近 5 条咨询记忆。

问答奖励为 2 经验，完成庭审为 10 经验。奖励按 `request_id` 或庭审会话的唯一事件 ID 去重；使用同一 `request_id` 重试不会重复加经验。无有效依据的兜底回答不奖励经验，也不生成记忆。

### `DELETE /api/pet/memories`

清空当前登录账户的全部咨询记忆。成功返回删除数量：`{"deleted": 3}`。关闭前端的咨询记忆开关只会停止生成新记忆，不会自动删除已有内容。

### `DELETE /api/pet/memories/{memory_id}`

删除一条属于当前登录账户的咨询记忆。无法删除其他账户的记录；未找到时返回 `{"deleted": 0}`。

## 4.1 回答反馈

### `POST /api/feedback`

要求登录。对某条助手回答提交反馈；同一账户对同一回答重复提交会覆盖此前结果（upsert）。只能评价自己会话里的回答。

```json
{
  "message_id": "result 事件返回的消息 ID",
  "rating": "unhelpful",
  "reason": "outdated",
  "comment": "引用的是旧版条文"
}
```

- `rating`：`helpful` 或 `unhelpful`。
- `reason`（`unhelpful` 时可选，默认 `other`）：`inaccurate`、`outdated`、`irrelevant`、`unclear`、`verbose`、`other`。
- `comment`：可选，最长 500 字。

错误：参数无效 `400`、未登录 `401`、回答不存在或不属于当前账户 `404`。

### `GET /api/feedback`

仅管理员。返回最近 100 条反馈（含对应问题与回答片段）、按类型统计与原因分布：

```json
{
  "recent": [{"rating": "unhelpful", "reason": "outdated", "comment": "...", "question": "...", "answer": "...", "created_at": "..."}],
  "stats": {"helpful": 3, "unhelpful": 1},
  "reasons": {"outdated": 1}
}
```

## 4.2 产品事件埋点

### `POST /api/events`

要求登录。批量上报白名单事件；每批最多入库 20 条，白名单外的事件被静默丢弃。事件属性只保留数值、布尔与 ≤120 字符的短字符串（≤12 个键），不收集问题内容。

白名单事件：`qa_ask`、`answer_received`、`citation_open`、`feedback_submitted`、`court_case_open`、`court_start`、`court_complete`、`court_review`、`module_switch`、`account_export`、`matter_created`、`matter_completed`、`matter_review`、`matter_to_court`、`knowledge_marked`、`qa_to_court`、`reasoning_scored`、`learn_map_viewed`、`basics_viewed`、`basics_open_article`、`basics_article_viewed`、`basics_ask_clicked`。

```json
{"events": [{"name": "court_start", "props": {"case_id": "haicheng", "role": "juror"}}]}
```

返回 `{"stored": 1}`。

### `GET /api/events/summary`

仅管理员。返回近 30 天事件统计：

```json
{"days": 30, "events": [{"name": "qa_ask", "count": 42, "users": 9}]}
```

## 4.3 法律问题工作台

把一个法律问题转化为结构化处理路径：采集案情 → 检索现行法条 → 生成事项档案（问题归类、事实摘要、适用依据、缺少信息、证据清单、行动清单、风险提醒）→ 推荐模拟庭审。全部接口要求登录，数据按账户隔离。

### `POST /api/matters`

提交案情采集并接收 SSE。字段全部为字符串：`what`（发生了什么，必填 10-2000 字）、`goal`（诉求，必填 2-500 字），可选 `when_where`、`parties`、`amount`、`evidence`、`urgent`。

事件流：`status`（正在查找相关法律… / 正在生成分析报告…）→ `result` 或 `error`。

```json
{
  "type": "result",
  "matter_id": "...",
  "report": {
    "issue_type": "劳动争议",
    "fact_summary": "…",
    "applicable_laws": [{"title": "劳动合同法", "article": "第四十七条", "reason": "…", "excerpt": "…", "validity": {"...": "同 /api/kb 的 validity"}}],
    "missing_info": ["…"],
    "evidence_checklist": ["…"],
    "action_steps": [{"step": "…", "detail": "…"}],
    "risks": ["…"],
    "disclaimer": "…"
  },
  "suggested_cases": [{"case_id": "haicheng", "title": "…", "theme": "…"}]
}
```

`applicable_laws` 强制来自检索白名单：模型输出的依据若不在本次检索证据（按 `title+article` 匹配）内会被丢弃，原文与效力标识以检索结果回填。生成失败时事项标记为 `failed` 并返回 `{"type":"error"}`。

### `GET /api/matters`

当前账户最近 50 个事项（不含报告正文）：`id`、`title`、`status`（`generating`/`ready`/`failed`）、`intake`、时间戳。

### `GET /api/matters/<matter_id>`

事项详情，含 `result.report` 与 `result.suggested_cases`。不存在或不属于当前账户返回 `404`。

### `POST /api/matters/<matter_id>/regenerate`

对失败（或想更新）的事项重新检索并生成，SSE 同 `POST /api/matters`。

### `DELETE /api/matters/<matter_id>`

删除事项档案，返回 `{"deleted": n}`。

## 4.4 知识复习

庭审评议与复盘会随判决下发本案卷宗法条（`knowledge` 数组：`article_ref` + `text` 节选），用户可把每条法条标记为已掌握或待复习，形成账户级知识点库；首次标记"已掌握"发放獬豸经验（+6，按条文去重），宠物状态中的 `knowledge` 字段聚合已掌握/待复习计数。

### `GET /api/knowledge`

当前账户的知识点列表（按更新时间倒序，最多 200 条）与统计：

```json
{"points": [{"id": 1, "article_ref": "《刑法》第二十条", "title": "海城夜行案 · 《刑法》第二十条",
             "case_id": "haicheng", "status": "mastered", "created_at": "...", "updated_at": "..."}],
 "summary": {"mastered": 1, "review": 0}}
```

### `POST /api/knowledge`

标记或更新一条知识点。`status` 为 `mastered` / `review`；传空串表示清除该标记。同一 `(账户, article_ref)` 唯一，重复标记为更新；只有首次标记"已掌握"发放经验。

```json
{"article_ref": "《刑法》第二十条", "title": "…", "case_id": "haicheng", "status": "mastered"}
```

返回 `{"ok": true, "first_time": bool, "pet": {...} | null}`。`case_id` 若提供必须在内置案例白名单内。

### `DELETE /api/knowledge/<point_id>`

删除一条知识点，返回 `{"deleted": n}`。

### `GET /api/learn/map`

学习地图：把当前账户的知识点、事项档案与庭审记录按主题聚合为学习导航。要求登录。

```json
{
  "summary": {"mastered": 2, "review": 1, "matters": 1, "courts_done": 1, "cases_total": 3},
  "themes": [
    {"key": "criminal", "name": "刑事法律", "mastered": 2, "review": 0, "matters": 0,
     "points": [{"article_ref": "《刑法》第二十条", "status": "mastered"}],
     "cases": [{"case_id": "haicheng", "title": "海城夜行案", "theme": "刑法 · 正当防卫的边界",
                "laws_count": 2, "completed": true}]}
  ]
}
```

主题按关键词归类（刑事、网络与诈骗防范、合同与借贷、劳动、婚姻家庭、消费、侵权、诉讼程序、其他）；知识点同时参考案例主题、标记时的标题与条文号。每个内置案例都会归入主题轨道（含是否已完成评议）。

## 4.5 账户数据控制

### `POST /api/auth/password`

要求登录。修改密码：校验当前密码，成功后吊销其它设备的会话（当前会话保留）。参与 IP 限流。

```json
{"current_password": "...", "new_password": "...（6-128 位，不能与当前相同）"}
```

错误：新密码无效 `400`、当前密码错误 `403`、限流 `429`。

### `GET /api/account/export`

要求登录。以 JSON 附件下载当前账户的全部数据：对话与消息、模拟法庭评议与记录、獬豸进度/记忆/奖励、回答反馈。

### `POST /api/account/delete`

要求登录。注销账号并永久删除名下全部数据（对话、庭审、宠物、反馈、埋点）。需密码二次确认；系统最后一名管理员不允许注销（`409`）。参与 IP 限流。

```json
{"confirm": "DELETE", "password": "..."}
```

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

提交最终评议并获取真实判决、AI 复盘与（可选的）说理评分。

```json
{
  "option": "C",
  "years": 0,
  "marks": ["E4", "E5"],
  "reasoning": "我认为构成正当防卫：监控显示侵害持续仅 11 秒……（20-800 字，可选）"
}
```

- `option` 必须匹配案例选项。
- 只有定罪选项使用 `years`，后端会限制在该选项的 `years_range`。
- `marks` 只接受卷宗中存在的证据编号，最多 8 个。
- `reasoning` 可选（≤800 字）：提交后会对照真实判决按证据判断、程序理解、说理完整性三个维度评分（各 0-10），以 `reasoning_score` SSE 事件下发并随复盘接口回放；评分失败静默跳过，不影响主链路。
- 同一会话只能成功评议一次；重复请求返回 `409`。

### `POST /api/court/suggest`

按问题文本推荐可练手的内置案例（问答/工作台 → 庭审直连）。要求登录。`text` 最长 2000 字，短于 2 字返回空列表。

```json
{"text": "对方持刀行凶，我夺刀反击，这算正当防卫吗？"}
```

```json
{"cases": [{"case_id": "haicheng", "title": "海城夜行案", "theme": "刑法 · 正当防卫的边界"}]}
```

### `GET /api/court/sessions`

当前登录账户已完成评议的庭审列表（复盘入口，最多 20 条）。

### `GET /api/court/sessions/<session_id>/review`

复盘详情：已存储的评议、法院裁判、审判长复盘、庭审过程回顾（轨迹、发言、纪要），以及本案法条复习材料：

```json
{
  "knowledge": [{"article_ref": "《刑法》第二十条", "text": "条文节选（≤400 字）"}],
  "reasoning_score": {"evidence": 8, "procedure": 6, "reasoning": 7, "overall": "…", "improvement": "…"},
  "deliberation": {"...": "..."},
  "transcript": [{"role": "judge", "name": "审判长", "text": "...", "player": false}],
  "analysis": "审判长复盘正文",
  "...": "..."
}
```

不再调用模型；未完成评议的会话返回 `409`。

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
| `verdict` | `real`, `yours`, `trajectory`, `contributions`, `knowledge` | 真实判决、用户轨迹与本案法条复习材料 |
| `reasoning_score` | `score` | 说理评分（`evidence`/`procedure`/`reasoning` 各 0-10，`overall`、`improvement`）；仅提交了 `reasoning` 且解析成功时出现 |
| `result` | `analysis` | 审判长复盘完成 |
| `error` | `error` | 流内错误，可按当前检查点重试 |

## 8. 错误处理约定

| 状态 | 场景 |
|---:|---|
| `400` | 参数、长度、格式、选项或当前角色输入无效 |
| `401` | 业务写入接口未登录（Cookie 缺失或过期） |
| `403` | 非管理员访问管理员接口；当前密码错误 |
| `404` | 案例或庭审会话不存在；反馈目标不存在 |
| `409` | 同名资料、无待处理检查点、会话已结束、重复评议或最后一名管理员注销 |
| `413` | 上传请求超过 16 MiB |
| `429` | 认证类端点触发 IP 限流 |
| `503` | 对应 LLM 未配置 |

对 SSE 接口，HTTP `200` 后仍可能出现 `error` 事件。前端必须保留用户输入和重试入口。
