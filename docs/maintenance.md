# 维护手册

## 1. 内置法律知识库

### 1.1 目录职责

- `assset/law/`：法律问答检索用法条、司法解释等规范性文本。
- `assset/case/`：法律问答检索用判例或裁判规则，不是模拟法庭案例。
- `seed/`：体积较小、结构化较好的普法节选和开发种子资料。
- `assset/moot-court/`：仅供模拟法庭使用的案例 JSON。

不要把法律问答判例放进 `moot-court`，也不要让模拟法庭自动检索 `assset/case`。两套内容的证据边界不同。

### 1.2 新增法条或判例

1. 使用 UTF-8 编码的 `.md`、`.markdown`、`.txt` 或可提取正文的 `.docx`。
2. 文件名应清晰、稳定、可区分版本；不要只写“新文件.txt”。
3. 法条正文尽量让每个“第×条”从新行开始，逐条引用解析依赖该结构。
4. Markdown 使用真实标题层级；解析器会把标题路径保存为 `section`。
5. 判例应包含案号/来源、裁判要旨、基本事实和裁判理由；不要混入给模型的指令。
6. 放入正确目录后，在临时 `LAW_DATA_DIR` 启动应用，检查 `/api/kb` 中的块数并做检索验证。
7. 提交文件与必要的测试/文档，再部署。

解析限制：

| 项目 | 限制 |
|---|---:|
| HTTP 上传体积 | 16 MiB |
| 文本最大字符数 | 4,000,000 |
| DOCX 解压正文 XML | 12,000,000 bytes |
| 单文档最大分块数 | 10,000 |
| 默认分块 | 1200 字，重叠 160 字 |

扫描 PDF 不受支持，应先在受控流程中 OCR，并人工校对法条号和关键事实。

### 1.3 更新既有内置资料

这是维护中最重要的行为：启动导入只检查 `sources.title` 是否已存在。直接替换同名文件内容不会更新持久库。

安全流程：

1. 在数据库备份上确认要更新的 `sources.title`。
2. 停止写入或进入维护窗口。
3. 删除该 `sources` 记录；外键级联会删除 `chunks`，触发器会删除 `chunks_fts`。
4. 部署并重启应用，`seed_sources()` 会重新导入磁盘文件。
5. 检查 `/api/kb` 的 `chunk_count`，再用实际问题验证引用。

当前没有“删除/重建内置资料”的管理 API。生产数据库操作必须先备份，并在数据库副本上验证 SQL。不要删除整个 `/data` 卷来更新单份资料。

如果 embedding 模型、端点或维度变化，也应重建需要向量化的来源。`embedding_space` 指纹不匹配时旧向量不会参与查询，但不重建会导致这些资料只走词法通道。

### 1.4 重复文件

启动扫描会按文件内容 SHA-256 跳过重复内置文件。内容相同但文件名不同不会重复写库；内容略有差异则会视作不同来源。仓库目前保留一份带 `(1)` 的重复借款合同文件，导入时会被跳过，后续整理前应确认没有外部流程依赖其文件名。

## 2. 运行时上传资料

`POST /api/kb` 把文件保存到 `<LAW_DATA_DIR>/uploads/`，再建立索引并清空进程内 chunk cache。**仅管理员**（首个注册用户）可调用；未登录返回 `401`，普通用户返回 `403`。

维护注意：

- 标题在数据库内唯一；重复标题返回 `409`。
- 上传失败时临时文件会删除。
- 上传成功后原文件和数据库索引都必须随持久卷备份。
- 多 worker 部署时，每个进程有独立缓存；当前单 worker 配置避免了上传后其他 worker 仍使用旧缓存的问题。

## 2.1 来源效力元数据（sources.meta）

引用面板与资料列表中的「现行有效 / 已被修改 / 废止或失效 / 未确认」来自 `legalweb/source_meta.py`：

- 索引时解析文件头部的著录字段（时效性、公布日期、施行日期、制定机关、发文字号）。新增法条文件只要保留这些头部行，效力标识自动生效。
- 旧库启动时由 `backfill_source_meta()` 用每个来源的首个分块幂等补录。
- 没有著录头且未补录的来源显示「未确认」——这是有意设计，不要凭印象补状态。
- 个别无著录头的内置法条（如《刑法（2023 修正）》）在 `source_meta.BUILTIN_META` 中按展示标题补录；新增此类文件时同步维护该字典，并在 `tests/test_trust.py` 中补一条解析断言。
- 定期复查知识库中的废止/已被修改文本，确认是否应下架或替换为新版本，避免检索继续引用失效依据。

## 2.2 效力治理下架记录

2026-10-03 下架（文件保留在仓库，`builtin_documents()` 跳过 + `RETIRED_SEED_TITLES` 启动清理，含旧库命名变体）：

| 文件 | 原因 | 替代来源 |
|---|---|---|
| 中华人民共和国合同法 | 废止 | 民法典（合同编） |
| 借款合同条例 | 废止（1985 年国务院法规） | 民法典（借款合同章） |
| 民间借贷司法解释（2015 原版） | 已被修改 | (2020 第二次修正) 现行版 |
| 民间借贷司法解释（2020 第一次修正） | 已被修改 | (2020 第二次修正) 现行版 |

后续下架新来源时：把展示标题（及旧命名变体）加入 `RETIRED_SEED_TITLES`，在 `tests/test_governance.py` 补断言，并在此表登记。恢复上架只需从清单移除并按 1.3 节重建该来源。

## 3. 模拟法庭案例

### 3.1 添加案例

在 `assset/moot-court/` 新建 UTF-8 JSON。文件顶层必须是对象，并包含：

```text
id, title, subtitle, theme, duration, entry_note, learning_goal,
disclaimer, parties, charge, disputes, facts, dossier, key_evidence,
laws, real_verdict, options
```

关键约束：

- `id` 只能包含小写字母、数字、下划线和连字符，长度 1–64；所有案例 ID 唯一。
- `dossier[].id` 使用稳定的 `E1`、`E2` 等编号。
- `key_evidence` 中的编号必须真实存在于 `dossier`。
- `laws` 必须放入允许 Agent 引用的完整核对文本。
- `real_verdict` 至少应包含前端使用的 `summary`、`reasoning`、`articles`。
- `options[].id` 必须唯一；定罪选项提供 `years_range: [min, max]`，无罪选项提供 `years: 0` 和 `convict: false`。
- 案例中出现的角色姓名、地点和指控必须与现有角色提示词一致。当前 `agents.py` 和部分审判长模板仍针对“海城夜行案/陈明”编写；新增完全不同的案例前，应先把硬编码角色背景参数化。

`cases.py` 在模块导入时加载全部 JSON。任一文件无法读取、字段缺失、ID 无效或重复，都会阻止应用启动；新增案例后至少执行：

```powershell
python -c "from legalweb.courtroom.cases import CASES; print(list(CASES))"
python -m unittest discover -s tests -v
```

### 3.2 修改庭审流程

修改阶段时要同时检查：

- `STAGE_ORDER`；
- `TrialEngine.run()` 的阶段分支；
- `export_state()` / `load_state()` 的可序列化字段；
- `/trial`、`/continue`、`/deliberation` 的保存和恢复；
- `web/court.js` 对新 SSE 事件的处理（含 verdict 事件附带的 `knowledge` 复习材料）；
- `test_court_e2e.py` 的完整流程。

检查点必须在返回事件前持久化。失败重试必须恢复到检查点前的干净记录，避免重复庭审发言。

### 3.3 案例法条与知识复习

- 案例 JSON 的 `laws` 字典（`{《某法》第×条: 条文全文}`）是封闭卷宗的一部分，也是评议/复盘页知识复习卡片的来源；`article_ref` 即字典键，进入 `knowledge_points` 表后作为用户知识点标识，**改名等于新知识点**，不要修改既有案例的 laws 键名。
- 獬豸奖励类型：问答 +2、完成庭审 +10、首次掌握知识点 +6（按 `knowledge:<条文标识>` 去重）。`pet_reward_events.kind` 的 CHECK 约束为 `qa/court/knowledge`；新增类型需要整表重建迁移（见 `Database.__init__` 的既有先例）。

## 4. 数据库维护

### 4.1 位置

- 本地默认：`<repo>/.law-data/law.sqlite3`
- Railway：`/data/law.sqlite3`
- 上传文件：`<LAW_DATA_DIR>/uploads/`

### 4.2 Schema 变更

现有迁移方式是在 `Database.__init__()` 中执行幂等 `ALTER TABLE`，捕获“列已存在”。新增迁移应满足：

1. 可在空库和旧库重复运行；
2. 不删除现有列或数据；
3. 大表重建先在生产副本测试耗时和磁盘占用；
4. 同步更新 `SCHEMA`，确保新建库直接得到最终结构；
5. 增加覆盖旧库升级的自动化测试。

### 4.3 备份

SQLite 使用 WAL，直接只复制主 `.sqlite3` 文件可能遗漏尚未 checkpoint 的事务。推荐：

- Railway 使用平台提供的卷备份/快照能力；
- 或在维护窗口通过 SQLite Online Backup API 创建一致性副本；
- 同时备份 `/data/uploads/`；
- 定期在独立环境执行恢复演练，而不只验证“备份文件存在”。

本地一致性备份示例（应用可运行，但最好在低写入时执行）：

```powershell
python -c "import sqlite3; s=sqlite3.connect(r'.law-data/law.sqlite3'); d=sqlite3.connect(r'law-backup.sqlite3'); s.backup(d); d.close(); s.close()"
```

恢复前先停止应用，保留当前库副本，确认目标路径后再替换。不要在运行中的生产实例上用普通文件复制覆盖数据库。

## 5. 模型和检索维护

### 5.1 模型切换

切换模型时验证：

- OpenAI Chat Completions 请求格式是否兼容；
- `stream: true` 是否以 `data:` SSE 和 `[DONE]` 结束；
- 返回正文是否在 `choices[0].delta.content` / `message.content`；
- 是否输出 `<think>`；客户端会过滤，但不应依赖模型长期稳定输出该标签；
- 最大 token、首次 token 和整场庭审耗时；
- 模拟法庭不得启用会明显拖慢发言的长思考模式。

法律问答和模拟法庭使用不同前缀，避免误把两者切到同一模型。修改 Railway 环境变量后要重新部署并分别验证两个功能。

### 5.2 相关性调优

调优顺序建议：

1. 先检查知识库文本结构和法条换行；
2. 查看问题关键词能否命中 FTS/词法；
3. 检查向量空间指纹是否匹配；
4. 查看 Jev 是否误过滤；
5. 最后再调整阈值。

Jev 是 fail-open 设计，日志会记录输入数、放行数、低相关滤除、注入拦截和耗时。不要把 Jev 失败误判为主问答不可用。

## 6. 日常检查清单

### 每次发布

- `git status` 无非预期文件。
- `tests/` 下的全部回归测试通过。
- JS 和 Python 语法检查通过。
- Railway 部署为 Online，`/health` 返回 `{"ok":true}`。
- 首页和静态资源是最新版本。
- 法律问答真实请求可完成，引用带 `source`、`article`、`validity`、`excerpt`。
- 模拟法庭至少可加载案例和角色；涉及引擎修改时跑完整端到端流程。

### 定期

- 检查法律资料的现行有效性和版本日期；重点看知识库里是否仍在引用「废止或失效 / 已被修改」的文本。
- 管理员在设置面板查看「回答反馈」：关注没帮助的原因分布（答错、过时、答非所问），据此修订知识库或提示词。
- 管理员在设置面板查看「产品事件」：关注问答到庭审的转化与庭审完成率。
- 检查模型/embedding/Jev 调用失败率和延迟。
- 轮换密钥并确认旧密钥失效。
- 检查 Railway 卷容量、数据库大小和上传目录。
- 做一次可恢复的数据库/上传文件备份。
- 审计公开接口权限（知识库上传、设置、日志、反馈、事件统计应仅管理员）。

## 7. 故障排查

### “模型请求失败或超时”

1. 检查 `/api/config` 对应模型是否 configured。
2. 核对 BASE_URL、MODEL、API_KEY，注意法律问答与模拟法庭前缀不同。
3. 从服务日志确认是连接超时、HTTP 错误还是无效输出。
4. 直接对提供方做最小 Chat Completions 测试，但不要把密钥打印到日志。
5. 配置 fallback 时确认主模型在首个可见 token 前失败才会切换。

### 检索不到资料

1. 查看 `/api/kb` 是否存在来源和合理的 `chunk_count`。
2. 确认 `LAW_DATA_DIR` 指向正确持久卷。
3. 检查资料是否因同标题而没有重新导入。
4. 用更直接的法条关键词测试词法/FTS。
5. 检查 Jev 日志；若全部过滤，系统应回退原候选。

### 引用很长或不是逐条法条

1. 确认线上 `qa.py` 已启用 `Evidence(..., question)`。
2. 检查法条是否以“第×条”在行首开始。
3. 新回答应带结构化 `source` 和 `article`；旧 payload 由前端兼容拆分。
4. 判例不会按其中偶然出现的法条号拆分，这是有意设计。

### 会话串到其他浏览器 / 看不到自己的数据

1. 确认 `/api/auth/me` 返回的是预期账户；业务数据按 `u:<user_id>` 隔离。
2. 检查 `conversations.owner_id` 是否为当前账户的身份键。
3. 登录/注册时是否携带了正确的 `client_id`（匿名期数据并入依赖它）。
4. 会话 Cookie 失效（改密吊销其它设备、注销、过期）时需重新登录。

### Railway 部署失败：No start command detected

确认根目录存在 `Procfile`：

```text
web: gunicorn 'legalweb.app:create_app()' --bind 0.0.0.0:$PORT --workers 1 --threads 8 --timeout 300
```

仓库也保留 `railway.toml`。Railway 当前对旧 Config as Code 格式有弃用提示，因此 `Procfile` 是启动命令的兼容兜底。

### 庭审卡在检查点

1. 查询 `court_sessions.state` 是否包含 `checkpoint` 和 `next_stage`。
2. 根据 `checkpoint.kind` 发送正确 payload。
3. 已完成评议的会话不能继续，属于预期 `409`。
4. 模型失败时前端应重新显示原检查点；如果出现重复发言，检查回滚保存逻辑。

## 8. 已知技术债

- 没有知识库删除/重建管理接口（当前靠直接删除 `sources` 行 + 重启重建）。
- 没有管理员提权/转移界面：管理员由「首个注册用户」决定，后续提权需要直接操作数据库。
- 模拟法庭部分角色背景和程序台词仍硬编码当前案例人物，不适合直接扩展到完全不同案例。
- SQLite 和进程内 chunk cache 更适合单实例/单 worker；横向扩展前需要外部数据库或明确的缓存失效机制。
- 事件埋点只存明细（`events` 表），尚无按周聚合的「可信法律学习闭环」指标视图；数据积累后应增加定期归档。
- 依赖版本采用范围约束，没有 lockfile；生产可重复构建能力仍可加强。
- `railway.toml` 格式收到平台弃用提示，应在截止日期前迁移并验证新的 Railway 配置方式。
