"""Flask 应用：静态页面、法律问答 SSE、知识库管理、模拟法庭 SSE。"""

import json
import logging
import os
import sqlite3
from itertools import zip_longest
from pathlib import Path
from threading import Lock

from flask import Flask, Response, abort, jsonify, request, send_from_directory, stream_with_context

from . import qa
from .courtroom import cases as court_cases
from .courtroom.engine import TrialEngine
from .database import Database, new_id, now_iso, public_message
from .documents import FORMATS, parse_document, split_sections
from .jev_gate import configured as jev_configured
from .llm import LLMClient, LLMError
from .rag import EmbeddingClient, retrieve, terms, tokenize

LOG = logging.getLogger("legalweb")
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SEED_DIR = Path(__file__).resolve().parent.parent / "seed"

MAX_QUESTION = 2000
MAX_COURT_QUESTION = 200
# The parser also enforces character and DOCX-XML limits.  Rejecting an
# overlarge request before saving it protects the uploads directory as well.
MAX_UPLOAD_BYTES = 16 * 1024 * 1024
# Product positioning changed from youth education to general legal literacy.
RETIRED_SEED_TITLES = {"weihuichengnianren"}


def sse(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def text_value(data: dict, key: str, *, limit: int | None = None) -> str:
    """Read an optional JSON string without turning malformed input into 500s."""
    value = data.get(key, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        abort(400, f"{key} 必须是字符串")
    value = value.strip()
    if limit is not None and len(value) > limit:
        abort(400, f"{key} 不能超过 {limit} 个字符")
    return value


def index_document(database: Database, embedder: EmbeddingClient,
                   path: Path, filename: str, title: str) -> dict:
    """解析 + 分块 + FTS + 可选向量，写入知识库。"""
    sections = parse_document(path, filename)
    chunks = split_sections(sections)
    source_id = new_id()
    title = (title or Path(filename).stem).strip()[:120]
    embeddings, space = [], ""
    if embedder.configured:
        try:
            embeddings, space = embedder.embed_texts([chunk["text"] for chunk in chunks])
        except ValueError as exc:
            LOG.warning("embedding degraded for %s: %s", title, exc)
    with database.connect() as db:
        db.execute("INSERT INTO sources (id,title,filename,status,chunk_count,created_at) VALUES (?,?,?,?,?,?)",
                   (source_id, title, filename, "ready", len(chunks), now_iso()))
        for chunk, vector in zip_longest(chunks, embeddings):
            cursor = db.execute(
                "INSERT INTO chunks (source_id, ordinal, section, text, embedding, embedding_space)"
                " VALUES (?,?,?,?,?,?)",
                (source_id, chunk["ordinal"], chunk["section"], chunk["text"],
                 json.dumps(vector) if vector else None, space or None))
            db.execute("INSERT INTO chunks_fts (rowid, tokens) VALUES (?, ?)",
                       (cursor.lastrowid, tokenize(chunk["text"])))
    return {"id": source_id, "title": title, "filename": filename,
            "chunk_count": len(chunks), "vector_indexed": bool(embeddings)}


def seed_sources(database: Database, embedder: EmbeddingClient):
    """首次启动把 seed/ 下的法条文件灌入知识库（按标题幂等）。"""
    if not SEED_DIR.exists():
        return
    with database.connect() as db:
        for title in RETIRED_SEED_TITLES:
            db.execute("DELETE FROM sources WHERE title=?", (title,))
        existing = {row["title"] for row in db.execute("SELECT title FROM sources")}
    for path in sorted(SEED_DIR.glob("*.md")):
        # NOTES-* 是内部工作文档（校对清单等），不是法律资料，不入知识库
        if path.stem.startswith("NOTES-"):
            continue
        if path.stem in existing:
            continue
        try:
            summary = index_document(database, embedder, path, path.name, path.stem)
            LOG.info("seeded knowledge source %s (%d chunks)", path.stem, summary["chunk_count"])
        except ValueError as exc:
            LOG.warning("seed %s failed: %s", path.name, exc)


def create_app() -> Flask:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES

    raw = (os.getenv("LAW_DATA_DIR") or "").strip()
    data_root = Path(raw).expanduser() if raw else Path.cwd() / ".law-data"
    database = Database(data_root)
    uploads_dir = data_root / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    embedder = EmbeddingClient()
    llm = LLMClient()
    seed_sources(database, embedder)
    if not llm.configured:
        LOG.warning("LLM 未配置：请设置 LAW_LLM_BASE_URL / LAW_LLM_API_KEY / LAW_LLM_MODEL")

    # ------------------------------------------------------------------
    # 静态页面
    # ------------------------------------------------------------------
    @app.get("/health")
    def health():
        return jsonify({"ok": True})

    @app.get("/")
    def index():
        return send_from_directory(WEB_DIR, "index.html")

    @app.get("/assets/<path:name>")
    def assets(name):
        return send_from_directory(WEB_DIR, name)

    @app.get("/api/config")
    def config():
        return jsonify({"llm_configured": llm.configured,
                        "embed_configured": embedder.configured,
                        "jev_configured": jev_configured(),
                        "retrieval_backend": "vector+lexical+fts5" if embedder.configured else "lexical+fts5"})

    # ------------------------------------------------------------------
    # 知识库
    # ------------------------------------------------------------------
    chunk_cache: list[dict] | None = None
    chunk_cache_lock = Lock()

    def load_chunks() -> list[dict]:
        """Load and decode the corpus once per worker; uploads invalidate it."""
        nonlocal chunk_cache
        if chunk_cache is not None:
            return chunk_cache
        with chunk_cache_lock:
            if chunk_cache is not None:
                return chunk_cache
            chunk_cache = _load_chunks()
        return chunk_cache

    def _load_chunks() -> list[dict]:
        with database.connect() as db:
            rows = db.execute("SELECT * FROM chunks ORDER BY source_id, ordinal").fetchall()
        chunks = []
        for row in rows:
            chunk = dict(row)
            chunk["embedding"] = json.loads(chunk["embedding"]) if chunk["embedding"] else None
            chunks.append(chunk)
        return chunks

    def invalidate_chunk_cache() -> None:
        nonlocal chunk_cache
        with chunk_cache_lock:
            chunk_cache = None

    def fts_lookup(word_list: list[str]) -> list[int]:
        if not word_list:
            return []
        match = " OR ".join('"' + word.replace('"', '""') + '"' for word in word_list)
        sql = ("SELECT c.id FROM chunks_fts JOIN chunks c ON c.id=chunks_fts.rowid "
               "WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts) LIMIT 24")
        try:
            with database.connect() as db:
                return [row[0] for row in db.execute(sql, (match,))]
        except sqlite3.OperationalError:
            LOG.warning("FTS 查询不可用，退回词法通道")
            return []

    @app.get("/api/kb")
    def kb_list():
        with database.connect() as db:
            rows = db.execute("SELECT id,title,filename,chunk_count,created_at FROM sources"
                              " ORDER BY created_at").fetchall()
        return jsonify({"sources": [dict(row) for row in rows],
                        "embed_configured": embedder.configured})

    @app.post("/api/kb")
    def kb_upload():
        file = request.files.get("file")
        if file is None:
            abort(400, "缺少上传文件")
        filename = file.filename or "upload.md"
        suffix = Path(filename).suffix.lower()
        if suffix not in FORMATS:
            abort(400, "仅支持 Markdown、TXT 或 DOCX")
        title = (request.form.get("title") or Path(filename).stem).strip()[:120]
        with database.connect() as db:
            if db.execute("SELECT 1 FROM sources WHERE title=?", (title,)).fetchone():
                abort(409, "同名资料已存在于知识库")
        path = uploads_dir / f"{new_id()}{suffix}"
        file.save(path)
        try:
            summary = index_document(database, embedder, path, filename, title)
        except ValueError as exc:
            path.unlink(missing_ok=True)
            abort(400, str(exc))
        invalidate_chunk_cache()
        return jsonify(summary), 201

    # ------------------------------------------------------------------
    # 法律问答
    # ------------------------------------------------------------------
    @app.get("/api/conversations/<conversation_id>")
    def conversation_detail(conversation_id):
        with database.connect() as db:
            row = db.execute("SELECT id,title FROM conversations WHERE id=?",
                             (conversation_id,)).fetchone()
            if not row:
                return jsonify({"conversation": None})
            messages = db.execute("SELECT * FROM messages WHERE conversation_id=?"
                                  " ORDER BY created_at", (conversation_id,)).fetchall()
        return jsonify({"conversation": {"id": row["id"], "title": row["title"]},
                        "messages": [public_message(m) for m in messages]})

    @app.post("/api/ask")
    def ask():
        data = request.get_json(force=True, silent=True) or {}
        question = text_value(data, "question", limit=MAX_QUESTION)
        conversation_id = text_value(data, "conversation_id", limit=128)
        if not 2 <= len(question) <= MAX_QUESTION:
            abort(400, "问题长度需在 2-2000 字之间")
        if not llm.configured:
            abort(503, "尚未配置模型服务，请设置 LAW_LLM_* 环境变量")
        with database.connect() as db:
            if conversation_id:
                found = db.execute("SELECT id FROM conversations WHERE id=?",
                                   (conversation_id,)).fetchone()
                if not found:
                    conversation_id = ""
            if not conversation_id:
                conversation_id = new_id()
                db.execute("INSERT INTO conversations (id,title,created_at) VALUES (?,?,?)",
                           (conversation_id, question[:60], now_iso()))
            old = db.execute("SELECT role,content FROM messages WHERE conversation_id=?"
                             " ORDER BY created_at DESC LIMIT 20", (conversation_id,)).fetchall()
        previous = [row["content"] for row in reversed(old) if row["role"] == "user"][-3:]
        recent_answers = [row["content"] for row in reversed(old)
                          if row["role"] == "assistant"][-2:]

        def stream():
            yield sse({"type": "status", "text": "正在检索法律知识库…"})
            chunks = load_chunks()
            retrieval = retrieve(question, chunks, fts_lookup(terms(question)), embedder, limit=6)
            if not retrieval["hits"]:
                payload = {"content": "当前知识库未检索到足以回答的依据。请换个问法或补充关键词（如具体条文、罪名、情形）；我不会编造依据。",
                           "citations": [], "grounded": False,
                           "conversation_id": conversation_id,
                           "backend": retrieval["backend"]}
                with database.connect() as db:
                    db.execute("INSERT INTO messages (id,conversation_id,role,content,payload,created_at)"
                               " VALUES (?,?,?,?,?,?)",
                               (new_id(), conversation_id, "user", question, "{}", now_iso()))
                    db.execute("INSERT INTO messages (id,conversation_id,role,content,payload,created_at)"
                               " VALUES (?,?,?,?,?,?)",
                               (new_id(), conversation_id, "assistant", payload["content"],
                                json.dumps({"grounded": False}), now_iso()))
                yield sse({"type": "result", **payload})
                return
            evidence = qa.Evidence(retrieval["hits"])
            messages = qa.build_messages(question, evidence, previous, recent_answers)
            yield sse({"type": "status", "text": "正在生成回答…", "backend": retrieval["backend"]})
            buffer = []
            try:
                for kind, piece in llm.stream(messages, temperature=0.15, max_tokens=3000):
                    if kind == "delta":
                        buffer.append(piece)
                        yield sse({"type": "delta", "text": piece})
            except LLMError as exc:
                yield sse({"type": "error", "error": str(exc)})
                return
            result = qa.sanitize_citations("".join(buffer), evidence.valid_labels)
            citations = [ref for ref in evidence.refs if ref["label"] in set(result["used"])]
            notice = "回答仅参考本次检索到的知识库片段，以官方发布文本为准；不构成法律意见。"
            if result["removed"]:
                notice = f"已移除 {result['removed']} 处无效引用。" + notice
            elif not result["used"]:
                notice = "本次回答未携带引用标注，请注意核对原文。" + notice
            with database.connect() as db:
                db.execute("INSERT INTO messages (id,conversation_id,role,content,payload,created_at)"
                           " VALUES (?,?,?,?,?,?)",
                           (new_id(), conversation_id, "user", question, "{}", now_iso()))
                db.execute("INSERT INTO messages (id,conversation_id,role,content,payload,created_at)"
                           " VALUES (?,?,?,?,?,?)",
                           (new_id(), conversation_id, "assistant", result["content"],
                            json.dumps({"citations": citations,
                                        "backend": retrieval["backend"],
                                        "grounded": bool(result["used"])}, ensure_ascii=False),
                            now_iso()))
            yield sse({"type": "result", "content": result["content"], "citations": citations,
                       "grounded": bool(result["used"]), "notice": notice,
                       "backend": retrieval["backend"], "conversation_id": conversation_id})

        return Response(stream_with_context(stream()), mimetype="text/event-stream")

    # ------------------------------------------------------------------
    # 模拟法庭
    # ------------------------------------------------------------------
    def save_court_session(session_id: str, case_id: str, engine: TrialEngine,
                           *, next_stage: str = "", checkpoint: dict | None = None):
        """庭审状态落库：transcript + 引擎状态（检查点续跑用）。"""
        state = engine.export_state()
        state["next_stage"] = next_stage
        state["checkpoint"] = checkpoint
        with database.connect() as db:
            db.execute(
                "INSERT INTO court_sessions (id,case_id,transcript,state,created_at)"
                " VALUES (?,?,?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET transcript=excluded.transcript,"
                " state=excluded.state",
                (session_id, case_id,
                 json.dumps(engine.transcript, ensure_ascii=False),
                 json.dumps(state, ensure_ascii=False), now_iso()))

    def restore_engine(row) -> TrialEngine:
        case = court_cases.CASES.get(row["case_id"])
        if not case:
            abort(404, "案例不存在")
        engine = TrialEngine(case, llm)
        engine.transcript = json.loads(row["transcript"] or "[]")
        engine.load_state(json.loads(row["state"] or "null"))
        return engine

    @app.get("/api/court/cases")
    def court_case_list():
        items = [{key: case[key] for key in
                  ("id", "title", "subtitle", "theme", "duration", "charge", "disclaimer",
                   "entry_note", "learning_goal")}
                 for case in court_cases.CASES.values()]
        return jsonify({"cases": items})

    @app.get("/api/court/cases/<case_id>")
    def court_case_detail(case_id):
        case = court_cases.CASES.get(case_id)
        if not case:
            abort(404, "案例不存在")
        return jsonify(court_cases.public_case(case))

    @app.post("/api/court/cases/<case_id>/trial")
    def court_trial(case_id):
        case = court_cases.CASES.get(case_id)
        if not case:
            abort(404, "案例不存在")
        if not llm.configured:
            abort(503, "尚未配置模型服务，请设置 LAW_LLM_* 环境变量")
        session_id = new_id()
        engine = TrialEngine(case, llm)

        def stream():
            yield sse({"type": "session", "session_id": session_id, "case_id": case_id})
            try:
                for event in engine.run():
                    # 检查点：暂停庭审，等待陪审员（用户）通过 /continue 提交意见
                    if event.get("type") == "checkpoint":
                        save_court_session(session_id, case_id, engine,
                                           next_stage=event["next"], checkpoint=event)
                        yield sse(event)
                        return
                    yield sse(event)
            except LLMError as exc:
                yield sse({"type": "error", "error": str(exc)})
                return
            save_court_session(session_id, case_id, engine, next_stage="await_deliberation")

        return Response(stream_with_context(stream()), mimetype="text/event-stream")

    @app.post("/api/court/sessions/<session_id>/continue")
    def court_continue(session_id):
        """陪审员在检查点提交阶段性预判 / 发问后，续跑庭审。"""
        data = request.get_json(force=True, silent=True) or {}
        with database.connect() as db:
            row = db.execute("SELECT * FROM court_sessions WHERE id=?", (session_id,)).fetchone()
        if not row:
            abort(404, "庭审会话不存在")
        if row["deliberation"]:
            abort(409, "该会话已结束")
        state = json.loads(row["state"] or "null") or {}
        checkpoint = state.get("checkpoint")
        if not checkpoint or not state.get("next_stage"):
            abort(409, "当前没有等待处理的庭审环节")
        case = court_cases.CASES.get(row["case_id"])
        if not case:
            abort(404, "案例不存在")
        poll_id = text_value(data, "poll", limit=32)
        if not any(o["id"] == poll_id for o in case["options"]):
            abort(400, "请先选择你的阶段性预判")
        question = text_value(data, "question", limit=MAX_COURT_QUESTION)
        target = text_value(data, "target", limit=32)
        if question and target not in ("prosecutor", "defender", "defendant"):
            abort(400, "发问对象需为公诉人、辩护人或被告人")
        if not llm.configured:
            abort(503, "尚未配置模型服务，请设置 LAW_LLM_* 环境变量")
        engine = restore_engine(row)
        kind = checkpoint["kind"]
        next_stage = state["next_stage"]
        payload = {"poll": poll_id, "question": question, "target": target}

        def stream():
            yield sse({"type": "resume", "kind": kind})
            try:
                for event in engine.apply_checkpoint(kind, payload):
                    yield sse(event)
                for event in engine.run(from_stage=next_stage):
                    if event.get("type") == "checkpoint":
                        save_court_session(session_id, row["case_id"], engine,
                                           next_stage=event["next"], checkpoint=event)
                        yield sse(event)
                        return
                    yield sse(event)
            except LLMError as exc:
                # 回滚到检查点时刻的干净状态：用户重新提交即可完整重放，不产生重复发言
                rollback = TrialEngine(case, llm)
                rollback.transcript = json.loads(row["transcript"] or "[]")
                rollback.load_state(state)
                save_court_session(session_id, row["case_id"], rollback,
                                   next_stage=next_stage, checkpoint=checkpoint)
                yield sse({"type": "error", "error": str(exc)})
                return
            save_court_session(session_id, row["case_id"], engine, next_stage="await_deliberation")

        return Response(stream_with_context(stream()), mimetype="text/event-stream")

    @app.post("/api/court/sessions/<session_id>/deliberation")
    def court_deliberation(session_id):
        data = request.get_json(force=True, silent=True) or {}
        with database.connect() as db:
            row = db.execute("SELECT * FROM court_sessions WHERE id=?", (session_id,)).fetchone()
        if not row:
            abort(404, "庭审会话不存在或未完成")
        case = court_cases.CASES.get(row["case_id"])
        if not case:
            abort(404, "案例不存在")
        if row["deliberation"]:
            abort(409, "该会话已完成过评议")
        option_id = text_value(data, "option", limit=32)
        option = next((o for o in case["options"] if o["id"] == option_id), None)
        if not option:
            abort(400, "无效的评议选项")
        years = 0
        if option.get("convict"):
            low, high = option["years_range"]
            try:
                years = int(data.get("years"))
            except (TypeError, ValueError):
                years = low
            years = max(low, min(high, years))
        valid_ids = {e["id"] for e in case["dossier"]}
        marks = [m for m in (data.get("marks") or []) if m in valid_ids][:8]
        deliberation = {"option_id": option_id, "label": option["label"],
                        "years": years, "convict": bool(option.get("convict")),
                        "marks": marks}
        engine = restore_engine(row)

        def stream():
            analysis = ""
            try:
                for event in engine.verdict_events(deliberation):
                    if event.get("type") == "result":
                        analysis = event.get("analysis", "")
                    yield sse(event)
            except LLMError as exc:
                yield sse({"type": "error", "error": str(exc)})
                return
            with database.connect() as db:
                db.execute("UPDATE court_sessions SET deliberation=?, analysis=?, finished_at=?"
                           " WHERE id=?",
                           (json.dumps(deliberation, ensure_ascii=False), analysis,
                            now_iso(), session_id))

        return Response(stream_with_context(stream()), mimetype="text/event-stream")

    return app
