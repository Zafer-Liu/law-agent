# Railway 部署手册

## 1. 当前部署模型

- Railway project：`law-agent`（ID `a9ffaa6f-55b7-4119-a78b-68b16608cc36`）
- Service：`web`（ID `b2939183-5c1c-4b17-8745-6e796d16db37`）
- Environment：`production`（ID `31c9b11d-1b60-4f07-a814-1cbecf3d8ab7`）
- 正式域名：<https://law-agent.si>（Hostinger 注册，DNS 指向 Railway 边缘；`www.law-agent.si` 同站）——见 §9
- Railway 内部地址：<https://web-production-8fccca.up.railway.app/>（region sfo）
- Volume：`web-volume`（ID `fa5b2845-2b0f-47f6-8e50-a22c1c9969f0`，5GB）挂载 `/data`
- 健康检查：`GET /health`（timeout 300s，restart ON_FAILURE max 10）
- 启动方式：根目录 `Procfile`，并由 `railway.toml` 保留同等配置
- 部署方式：**GitHub 仓库联动**（2026-10-01 起）——私有仓库 `Zafer-Liu/law-agent`（branch `main`）已通过 GraphQL `serviceConnect` 挂到 web 服务，**push 到 main 即自动构建部署**（Railway GitHub App 已装在账号上；auto-deploy 触发器已占用该服务唯一名额，无需另开）。应急时仍可本机 `railway up` 手动部署（CLI 在 `E:\Tool\railway-cli\node_modules\.bin\railway`；上传遵循 `.gitignore` 与 `.railwayignore`，`.deploy/` 密钥与数据库备份两边都已排除）

生产启动命令：

```text
gunicorn 'legalweb.app:create_app()' --bind 0.0.0.0:$PORT --workers 1 --threads 8 --timeout 300
```

架构沿革：2026-09-29 曾部署在阿里云 ECS（ai-moment.com/law/ 子路径，NPM 反代）；**2026-10-01 已整体迁回 Railway**，ECS 侧 law-agent 全部拆除（服务/代码/数据/密钥/系统用户/nginx 入口），旧地址 `/law/` 的 302 跳转已改指 <https://law-agent.si>（注意 reload 的是 `nginx-app` 容器，别 reload 错 `nginx`）。数据库（含全部向量索引与历史对话）经卷文件直传完整迁移，未做二次索引。

## 2. 首次创建服务

1. 在 Railway 创建项目并关联仓库。
2. 选择仓库根目录作为服务目录。
3. 创建持久卷并挂载到 `/data`。
4. 配置 `LAW_DATA_DIR=/data`。
5. 添加模型和可选检索环境变量。
6. 部署并等待知识库首次索引完成。
7. 验证健康检查、页面、问答和模拟法庭。

不要把 SQLite 放在容器临时文件系统；重新部署会丢失会话、上传资料和庭审状态。

**冷启动陷阱（2026-10-01 实测规避）**：全新空卷 + 自建 bge-m3 向量服务时，2906 段全量索引约 40 分钟，远超 300s 健康检查超时，会 crash-loop。规避法：先把已建好索引的 `law.sqlite3` 直传进卷再首次部署（见 §8），或临时切 MiniMax embo-01（云端 ~3 分钟，但向量空间不兼容，切换即全量重建）。

## 3. 生产环境变量

2026-10-01 起线上实际配置（值为密钥，只在 Railway 控制台/CLI 查看，不入库）：

```text
LAW_DATA_DIR=/data
LAW_LLM_BASE_URL=https://api.minimax.cn/v1        # 问答主模型（MiniMax-M3）
LAW_LLM_API_KEY=...
LAW_LLM_MAX_TOKENS=16000
LAW_COURT_LLM_BASE_URL=...                         # 模拟法庭（MiniMax-M2.7-highspeed）
LAW_COURT_LLM_API_KEY=...
LAW_EMBED_BASE_URL=https://embedding.zafer-liu-product.xyz/v1   # 自建 NPU bge-m3
LAW_EMBED_API_KEY=...
LAW_EMBED_MODEL=bge-m3
TYPESAFE_API_KEY=...                               # JEV 门控（api.typesafe.ai jev-latest）
LAW_JEV_GATE=1
LAW_JEV_REL_MIN=2.0
LAW_JEV_INJ_MAX=0.5
LAW_JEV_EXCERPT_CHARS=600
LAW_PKULAW_TOKEN=...                               # 北大法宝 MCP（不设即停用）
PYTHONUNBUFFERED=1
```

要点：

- 向量服务是**自建 NPU 板**（详见 `F:\Projects\Orange-seting`）：1024 维对称向量、OpenAI 兼容 `input/data` 格式；`rag.py` 按域名/模型名自动识别接口风格，MiniMax embo-01（`texts/type` 非对称）是备选，`LAW_EMBED_STYLE` 可强制。板子离线时自动降级词法检索，不影响主路径。
- 换 embedding 模型 = 向量空间指纹变化 = 必须全量重建 chunks 向量（仓里 `reindex_once.py` / 部署机脚本可断点续跑）。
- JEV 门控**永不阻断主路径**：0 段放行时保留原始检索结果并记 warning。
- 北大法宝按次扣积分；401 `Error while checking remaining points` 是账户积分侧问题（本地/ECS/Railway 同报），去 mcp.pkulaw.com 控制台处理，代码侧无需动。

Railway 自动注入 `PORT`，不要硬编码生产端口。不要在构建日志、截图或 issue 中粘贴环境变量值。

## 4. 常规发布

发布前：

```powershell
python -m unittest discover -s tests -v
node --check web\app.js
node --check web\court.js
git status --short
```

提交并 `git push origin main`，Railway 自动拉取构建（构建器现为 RAILPACK/V3，`Procfile` 提供启动命令，卷与重启策略不受影响）；在 Railway 控制台或 `railway status` 等最新 deployment 变 Online。需要从本机绕过 Git 手动部署时：

```powershell
railway status
railway up --detach
railway status
```

构建或部署失败时：

```powershell
railway logs --latest --build --lines 200
railway logs --latest --deployment --lines 200
```

不要只看到旧实例仍为 Online 就认为新版本成功；必须确认最新 deployment ID 已成为 Online。

## 5. 发布后验证

### 5.1 基础验证

```powershell
$base = 'https://web-production-8fccca.up.railway.app'
Invoke-RestMethod "$base/health"
Invoke-RestMethod "$base/api/config"
```

期望：

- `/health` 为 `{"ok":true}`；
- 两套 LLM 均 configured；
- 首页、`/assets/styles.css`、`/assets/app.js`、`/assets/court.js` 返回 200。

### 5.2 业务验证

法律问答：

- 状态先显示检索，再流式输出回答；
- `result.grounded` 为 true 时至少有一个引用；
- 引用对象含 `label`、`source`、`article`、`excerpt`；
- 新对话出现在历史列表，其他浏览器不可见。

模拟法庭：

- 案例和角色可选；
- 开庭流能到达第一个检查点；
- 用户输入后可以继续；
- 最终评议显示真实判决与分析。

### 5.3 数据验证

- `/api/kb` 有内置来源；
- 重启/重新部署后历史对话仍存在；
- `/data` 卷空间充足；
- 新增资料部署后块数符合预期。

## 6. 回滚

1. 优先在 Railway 控制台把流量回滚到最近一次健康 deployment。
2. 若代码变更涉及 schema，先判断旧代码能否读取已升级数据库。
3. 不要用旧数据库文件直接覆盖生产卷；先备份当前状态并在副本验证。
4. 回滚后重复基础和业务验证。

数据库迁移应保持向后兼容，正是为了使代码回滚不必同时破坏性回滚数据。

## 7. 配置文件说明

`railway.toml` 当前包含 Nixpacks builder、启动命令、健康检查、300 秒健康检查超时和失败重启策略。Railway CLI 已提示旧 Config as Code 格式将在未来停止支持；迁移前应：

1. 在单独分支生成新配置；
2. 对比 start command、health check、volume 和环境变量是否保持一致；
3. 在预发布环境验证；
4. 保留 `Procfile` 作为通用平台启动声明。

不要在一次普通功能发布中顺带迁移部署配置。

## 8. 数据卷文件操作（2026-10-01 迁移实测）

把整库搬进/搬出 Railway 卷（ECS → Railway 就是这么迁的）：

```bash
RAIL=/e/Tool/railway-cli/node_modules/.bin/railway
# 上传（--overwrite 必须带，卷里已有同名文件时才会覆盖）
MSYS_NO_PATHCONV=1 $RAIL volume files --volume fa5b2845-2b0f-47f6-8e50-a22c1c9969f0 upload .deploy/law.sqlite3 /data/law.sqlite3 --overwrite
# 下载
MSYS_NO_PATHCONV=1 $RAIL volume files --volume fa5b2845-2b0f-47f6-8e50-a22c1c9969f0 download /data/law.sqlite3 ./law.sqlite3
```

踩坑：

- `--volume` 是 **group 级 flag**，要放在 `files` 后、子命令（upload/download）前；CLI < 5.6x 会报 "non-interactive mode"，先升级。
- Git Bash 会把 `/data/...` 改写成 Windows 路径，`MSYS_NO_PATHCONV=1` 必加。
- 传大库前先在源机 `VACUUM`（本次 100MB → 74MB）。
- 上传 SQLite 前确保源库无活跃写入（停服务或 WAL checkpoint 后再拷）。
- 单变量改密钥用 stdin，避免命令行落历史：`printf '%s' "$VALUE" | $RAIL variable set KEY --stdin`（printf 不带换行，echo 会把 `\n` 一起塞进去）。

## 9. 域名接入（Hostinger → Railway，2026-10-01 已完成）

正式域名 **law-agent.si**（Hostinger 注册，NS `horizon/orbit.dns-parking.com`），apex 与 `www` 都指向 Railway：

| 记录 | 值 | 说明 |
|---|---|---|
| A `@` → `69.46.46.54` | TTL 300 | Railway 边缘 IP（`x7wid27n.up.railway.app` 的解析值） |
| CNAME `www` → `qzhv6h08.up.railway.app` | TTL 300 | Railway 为 www 域名分配的目标 |
| TXT `_railway-verify` / `_railway-verify.www` | `railway-verify=…` | 所有权验证，验证完即长期保留 |

Railway 侧域名 ID：apex `f43c3c7f-288e-4f45-838f-3cb472d5a91d`，www `fe95ee5d-3ed3-4239-8c6f-02c1daab17ab`；查状态 `railway domain status <ID>`。证书均已 VALID，`/health` 双域名 200。

**为什么 apex 用 A 记录而不是 Railway 建议的根 CNAME**：Hostinger 后台把根域 CNAME 实现为 ALIAS，而 zone 模板会留一条停放 A `@` 记录，区域校验拒绝「ALIAS 与 A 同名共存」。`dns_records_delete` 想删它时——该 operation 的 inputSchema 只声明了 `domain`，`filters` 参数会被 MCP 网关剥掉（上游报 "The filters field is required"），即 **API 删不掉这条 A**。解法：直接把 A `@` 的 content 改成 Railway 边缘 IP（update overwrite 即可，不触发 ALIAS 冲突）。代价：边缘 IP 变化时不会自动跟随，若哪天 502，先 `nslookup x7wid27n.up.railway.app` 核对 IP；想根治需在 hPanel 网页端手删 A 记录后换回根 CNAME。

**Hostinger MCP（`hostinger@claude-plugins-official`，remote `https://mcp.hostinger.com`）实操**：

- OAuth 手动走通：DCR `POST …/oauth-server/register`（UA 必须伪装浏览器，Python 默认 UA 被 Cloudflare 403）→ PKCE S256 authorize → localhost 回调 → token。凭据在 `~/.claude/hostinger-mcp-token.json`（含 refresh_token；access_token 1 小时过期，`POST …/oauth-server/token` grant_type=refresh_token 续期）。
- MCP 是通用网关：`tools/call search` 找 operation → `tools/call execute` 执行。DNS 相关只有 list/update/delete/reset/validate/snapshots 六个。
- zone 格式：`[{"type","name","ttl","records":[{"content","is_disabled"}]}]`，写入前先 `dns_records_validate`；update `overwrite=true` 按 name+type 替换。

## 10. 扩容限制

当前架构按单实例、单 Gunicorn worker设计：

- SQLite 在单卷上写入；
- chunk cache 是进程内变量；
- 上传只会让当前进程缓存失效；
- 庭审和问答状态保存在 SQLite，但没有分布式锁。

如果要增加 worker 或副本，应先完成：

- 将数据库迁移到适合多实例的服务，或验证 Railway 卷的并发挂载语义；
- 使用共享缓存版本/失效机制；
- 为知识库写操作增加管理员鉴权和并发控制；
- 做并发问答、上传和庭审恢复测试。

