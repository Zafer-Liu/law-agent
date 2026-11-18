# 民事类案检索实验

实验使用课程数据 `civil_database_1k.json` 和 `query_cases/` 中的 10 个 TXT 查询。现有数据库与评测独立于法律助手应用。原始文书存入 PostgreSQL 的 `civil_case_lab.cases`，模型向量存入 `civil_case_lab.case_embeddings`。数据与运行产物放在本地 `.law-data/`。

## 使用 PostgreSQL 和百炼指定模型

准备专用 PostgreSQL 数据库，安装 `pgvector`，并由有权限的角色执行 `CREATE EXTENSION IF NOT EXISTS vector;`。脚本需要 Python 3.11+、`requests` 和 `psycopg2`（可用 `psycopg2-binary` 安装）。Windows 本地运行也可使用下方的 PGlite 入口。

将连接串配置为环境变量 `CASE_VECTOR_DSN`。在自己的环境配置 `DASHSCOPE_API_KEY` 和 `DASHSCOPE_BASE_URL`。Base URL 使用工作空间实际提供的 OpenAI 兼容地址，形式为 `https://<WorkspaceId>.<region>.maas.aliyuncs.com/compatible-mode/v1`，不含 `/embeddings`。地域、工作空间和 Key 需匹配。[阿里云向量 API 文档](https://help.aliyun.com/zh/model-studio/text-embedding-synchronous-api) · [创建 API Key](https://help.aliyun.com/zh/model-studio/get-api-key)

在 PowerShell 中把 `$dataset` 设为课程数据集目录。获得课程语料的云端处理授权后运行 `--method qwen`，这会向百炼发送库内的事实段和查询正文，可能产生 API 用量。`--method both` 同时生成 BM25 对照排名。

```powershell
python -I -B -X utf8 evals/civil_retrieval/experiment.py --data $dataset --method qwen --out .law-data/civil-case-qwen.json
```

脚本分批保存向量。重跑同一份语料、模型和文本版本会复用已写入的文书向量，查询向量重新计算。结果文件使用新文件名。以内嵌库的 SQL 为例，`embedding <=> query_vector` 计算余弦距离，按距离升序取前 K 个。1000 条向量使用精确检索，可以直接比较检索策略，避免近似索引带来的额外召回误差。

## 在本地重跑

已安装 PostgreSQL 时，可直接用上面的 Python 命令，将 `--method qwen` 改成 `--method baseline`。这个模式从 PostgreSQL 读取检索文本，在 Python 中计算中文字符二元组 BM25。

如果没有 PostgreSQL 服务，可把下列三个 npm 包装在独立工具目录 `$toolRoot`。本次使用的 PGlite 是 PostgreSQL 18.6 的 WebAssembly 实现，运行的是实际 `pgvector` 扩展。脚本在随机回环端口使用 PostgreSQL 协议启动数据库，并在实验结束时关闭。

```powershell
npm install --prefix $toolRoot --ignore-scripts --no-audit --no-fund --save-exact @electric-sql/pglite@0.5.16 @electric-sql/pglite-pgvector@0.0.17 @electric-sql/pglite-socket@0.2.19
```

```powershell
$env:CASE_PGLITE_TOOL_ROOT = $toolRoot
$env:CASE_PGLITE_DATA_DIR = Join-Path (Get-Location) '.law-data/civil-case-lab-postgres'
node evals/civil_retrieval/run_pglite.mjs --data $dataset --out .law-data/civil-case-baseline-replay.json
```

本地向量对照使用已有的 BAAI/bge-m3 模型快照，需额外安装 PyTorch 2.6+ 和支持 `dtype` 加载参数的 Transformers 4.56+。把 `$snapshot` 设为模型快照目录。该模式从本地加载模型，最多取 2048 token，使用 CLS 向量和 L2 归一化，向量仍写入同一 PostgreSQL 库，按模型区分。

```powershell
node evals/civil_retrieval/run_pglite.mjs --data $dataset --method bge --local-model-dir $snapshot --batch-size 12 --out .law-data/civil-case-bge-replay.json
```

## 接入法晓

配置项目 `.env` 中的 `CASE_VECTOR_DSN` 和 `CASE_VECTOR_CORPUS`（结果中的完整 `corpus_sha256`），然后重启项目。从 `/app` 进入「类案检索」，使用管理员账号登录。课程案例含真实当事人资料，页面为管理员开放。可通过 `GET /api/cases/config` 核对配置，通过 `POST /api/cases/search` 提交 `{"query":"脱敏案情或争点","limit":5}`（最多 3,000 字，返回 1–10 条）。配置中的模型应与导入的向量一致。

管理员也可在「法律问答」输入区开启「检索案例库」。当前请求会附带 `case_search: true`，命中的裁判文书以「参考案例」显示在引用面板。开关默认关闭，案例检索失败时问答会继续并说明检索状态。

配置 `CASE_VECTOR_MODEL=qwen3.7-text-embedding` 时，项目沿用百炼工作空间变量。使用本地 BGE 时，设置 `CASE_VECTOR_MODEL=BAAI/bge-m3` 和 `CASE_VECTOR_LOCAL_MODEL_DIR`。PGlite 可通过 `node evals/civil_retrieval/run_pglite.mjs --app` 启动法晓，继承 `CASE_PGLITE_*`，并自动注入本地 DSN。同一 PGlite 数据目录每次只由一个进程打开，导入和启动法晓请先后执行。

## 复核结果

`case_no` 是源 JSON 数组从 1 开始的文书序号，即 `rows[case_no - 1]` 的 `uniqid` 与正文。序号依赖固定原始文件，结果同时保存 SHA-256 和 `case_id` 便于核对。每个查询的完整 Top 5 包含排名、文书 ID、案由标签及方法自己的分数。

阅读 [实验报告](report.md) 中的结论、查询结果和改进建议。使用相同的争点评审标准复核新的候选。表中 Top 3 的标注只覆盖此次实测候选池，新的模型候选需要补充评审。
