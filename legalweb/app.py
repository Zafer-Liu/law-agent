"""Flask 应用：静态页面、法律问答 SSE、知识库管理、模拟法庭 SSE。"""

import hashlib
import json
import logging
import mimetypes
import os
import re
import secrets
import sqlite3
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from itertools import zip_longest
from pathlib import Path
from threading import BoundedSemaphore, Lock

from flask import (
    Flask,
    Response,
    abort,
    g,
    jsonify,
    request,
    send_from_directory,
    stream_with_context,
)
from werkzeug.security import check_password_hash, generate_password_hash

from . import qa
from .courtroom import cases as court_cases
from .courtroom.engine import PLAYER_ROLES, TrialEngine
from .database import Database, new_id, now_iso, public_message
from .documents import FORMATS, parse_document, split_sections
from .jev_gate import configured as jev_configured
from .llm import LLMClient, LLMError
from .pkulaw import PkulawClient
from .rag import EmbeddingClient, retrieve, terms, tokenize

LOG = logging.getLogger("legalweb")
WEB_DIR = Path(__file__).resolve().parent.parent / "web"


class _RingLogHandler(logging.Handler):
    """内存环形日志：设置面板展示最近的运行日志（不落盘、重启即清空）。"""

    def __init__(self, capacity: int = 200):
        super().__init__()
        self.records = deque(maxlen=capacity)

    def emit(self, record):
        try:
            self.records.append({
                "time": datetime.now().strftime("%m-%d %H:%M:%S"),
                "level": record.levelname,
                "name": record.name.split(".")[-1],
                "message": record.getMessage(),
            })
        except Exception:  # noqa: BLE001 — 日志钩子永不影响主链路
            pass


_RING_LOG = _RingLogHandler()
SETTING_KEYS = ("vector", "jev", "pkulaw")

# ---- 账户体系：不透明会话令牌 Cookie（DB 存储，重启/重部署不掉线） ----
AUTH_COOKIE = "law_session"
AUTH_SESSION_DAYS = 30
AUTH_USERNAME_RE = re.compile(r"^[A-Za-z0-9_\-一-鿿]{2,24}$")
AUTH_PASSWORD_MIN, AUTH_PASSWORD_MAX = 6, 128
AUTH_RATE_WINDOW = 300   # 认证端点限流窗口（秒），防爆破注册/登录
AUTH_RATE_MAX = 30
_auth_attempts: dict[str, deque] = {}
_auth_attempts_lock = Lock()


def _auth_rate_ok() -> bool:
    """每 IP 滑动窗口限流；取 X-Forwarded-For 首跳以兼容 Railway 代理。"""
    forwarded = request.headers.get("X-Forwarded-For", "")
    ip = (forwarded.split(",")[0].strip()
          if forwarded.strip() else (request.remote_addr or "?"))
    now = datetime.now(timezone.utc).timestamp()
    with _auth_attempts_lock:
        bucket = _auth_attempts.setdefault(ip, deque())
        while bucket and bucket[0] < now - AUTH_RATE_WINDOW:
            bucket.popleft()
        if len(bucket) >= AUTH_RATE_MAX:
            return False
        bucket.append(now)
        return True


def _load_settings(db) -> dict:
    """检索增强开关（settings 表，缺省全开）；键名固定，容错未知行。"""
    rows = db.execute(
        f"SELECT key, value FROM settings WHERE key IN ({','.join('?' * len(SETTING_KEYS))})",
        SETTING_KEYS).fetchall()
    stored = {row["key"]: row["value"] for row in rows}
    return {key: stored.get(key, "1") != "0" for key in SETTING_KEYS}
SEED_DIR = Path(__file__).resolve().parent.parent / "seed"
ASSET_DIR = Path(__file__).resolve().parent.parent / "assset"
BUILTIN_KB_DIRS = (
    (SEED_DIR, ""),
    (ASSET_DIR / "law", "法条"),
    (ASSET_DIR / "case", "判例"),
)

MAX_QUESTION = 2000
MAX_COURT_QUESTION = 200
MAX_COURT_STATEMENT = 500
# The parser also enforces character and DOCX-XML limits.  Rejecting an
# overlarge request before saving it protects the uploads directory as well.
MAX_UPLOAD_BYTES = 16 * 1024 * 1024
# Product positioning changed from youth education to general legal literacy.
RETIRED_SEED_TITLES = {
    "weihuichengnianren",
    # 普法演示用节选（2026-09-29 应要求下架，仅保留正式法条/司法解释）
    "民法典与合同规则（普法节选）",
    "刑法（演示用节选）",
    "刑事诉讼法（庭审程序演示用节选）",
}


def sse(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


# ------------------------------------------------------------------
# 獬豸记忆库：把每次问答蒸馏成一条宠物记忆（模型生成，失败退回问题摘要）
# ------------------------------------------------------------------
PET_MEMORY_LIMIT = 50      # 每个客户端保留的记忆条数上限，超出裁掉最旧
PET_MEMORY_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pet-memory")
PET_MEMORY_SLOTS = BoundedSemaphore(8)  # 2 个执行 + 最多 6 个排队，过载时宁可跳过附加体验
PET_MEMORY_PROMPT = (
    "你是法律助手「法晓」的桌宠獬豸。根据下面这次法律问答，为獬豸提炼一条关于这位用户的记忆。"
    "要求：第三人称；不超过 40 字；只记关键事实或关切（纠纷类型、身份角色、核心诉求）；"
    "不要建议、不要法条编号、不要引号和任何前后缀，直接输出记忆内容。"
)


def pet_memory_content(question: str, answer: str, complete) -> str:
    """用模型蒸馏一条记忆；complete 抛错或输出为空时退回问题摘要（≤60 字）。"""
    content = ""
    try:
        content = complete(
            [{"role": "system", "content": PET_MEMORY_PROMPT},
             {"role": "user", "content": f"问题：{question[:400]}\n回答：{answer[:1200]}"}],
            temperature=0.2, max_tokens=800)
    except Exception:  # noqa: BLE001 — 记忆是附加体验，模型故障不阻断问答
        content = ""
    content = str(content).strip().strip('"“”‘’').replace("\n", " ")[:60]
    return content or f"曾就「{question[:30]}」咨询法律问题"[:60]


def load_pet_memories(db, client_id: str, limit: int = 5) -> dict:
    """最近的记忆（新的在前）+ 总条数，随 /api/pet 一起下发。"""
    rows = db.execute(
        "SELECT id,content,created_at FROM pet_memories WHERE client_id=?"
        " ORDER BY id DESC LIMIT ?", (client_id, limit)).fetchall()
    total = db.execute("SELECT COUNT(*) AS n FROM pet_memories WHERE client_id=?",
                       (client_id,)).fetchone()["n"]
    return {"memories": [dict(row) for row in rows], "memory_count": total}


def load_pet_rewards(db, client_id: str, limit: int = 5) -> list[dict]:
    """最近成长记录，用于解释经验来源；不向前端暴露内部事件标识。"""
    rows = db.execute(
        "SELECT kind,gain,created_at FROM pet_reward_events WHERE client_id=?"
        " ORDER BY created_at DESC LIMIT ?", (client_id, limit)).fetchall()
    return [dict(row) for row in rows]


def pet_forget_old(db, client_id: str) -> None:
    """每客户端只留最近 PET_MEMORY_LIMIT 条记忆。"""
    db.execute(
        "DELETE FROM pet_memories WHERE client_id=? AND id NOT IN"
        " (SELECT id FROM pet_memories WHERE client_id=?"
        f"  ORDER BY id DESC LIMIT {PET_MEMORY_LIMIT})",
        (client_id, client_id))


def apply_pet_reward(db, client_id: str, kind: str,
                     gain: int, event_id: str) -> bool:
    """在一个事务内登记奖励事件并累加进度；返回本次是否首次发放。"""
    if kind not in ("qa", "court") or gain <= 0 or not event_id:
        return False
    created_at = now_iso()
    inserted = db.execute(
        "INSERT OR IGNORE INTO pet_reward_events"
        " (event_id,client_id,kind,gain,created_at) VALUES (?,?,?,?,?)",
        (event_id, client_id, kind, gain, created_at)).rowcount == 1
    if not inserted:
        return False
    counter = "qa_count" if kind == "qa" else "court_count"
    db.execute(
        "INSERT INTO pet_progress (client_id,exp,qa_count,court_count,updated_at)"
        " VALUES (?,?,?,?,?)"
        " ON CONFLICT(client_id) DO UPDATE SET"
        " exp=pet_progress.exp+excluded.exp,"
        f" {counter}=pet_progress.{counter}+1,"
        " updated_at=excluded.updated_at",
        (client_id, gain, kind == "qa", kind == "court", created_at))
    return True


def submit_pet_memory(work) -> bool:
    """把附加记忆任务送入有界线程池；过载时不拖垮主问答链路。"""
    if not PET_MEMORY_SLOTS.acquire(blocking=False):
        LOG.warning("獬豸记忆队列已满，跳过本次记忆")
        return False
    try:
        future = PET_MEMORY_EXECUTOR.submit(work)
    except RuntimeError:
        PET_MEMORY_SLOTS.release()
        LOG.warning("獬豸记忆任务无法提交", exc_info=True)
        return False
    future.add_done_callback(lambda _future: PET_MEMORY_SLOTS.release())
    return True



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


def _doc_display_title(path) -> str:
    """文档展示标题：Markdown 优先取首个一级标题；文件名去掉 FBM 来源编号。"""
    stem = re.sub(r"\s*\((?:FBM[-\w.]*)\)\s*", "", path.stem).strip() or path.stem
    if path.suffix.lower() == ".md":
        try:
            head = path.read_text(encoding="utf-8", errors="ignore")[:2000]
            match = re.search(r"^#\s+(.+?)\s*$", head, re.MULTILINE)
            if match:
                return match.group(1)[:120]
        except OSError:
            pass
    return stem


def builtin_documents():
    """Yield unique built-in RAG documents from seed, law and case folders."""
    candidates = []
    for directory, category in BUILTIN_KB_DIRS:
        if not directory.exists():
            continue
        for path in directory.rglob("*"):
            if path.is_file() and path.suffix.lower() in FORMATS:
                candidates.append((path, category))
    candidates.sort(key=lambda item: (" (1)" in item[0].stem, str(item[0])))
    seen_digests = set()
    for path, category in candidates:
        if path.stem.startswith("NOTES-"):
            continue
        digest = hashlib.sha256(path.read_bytes()).digest()
        if digest in seen_digests:
            LOG.info("skip duplicate built-in source %s", path.name)
            continue
        seen_digests.add(digest)
        display = _doc_display_title(path)
        title = f"{category} · {display}" if category else display
        # 旧版命名（文件名 stem 直接当标题）作迁移对照
        legacy = f"{category} · {path.stem}" if category else path.stem
        yield path, title[:120], legacy[:120]


def seed_sources(database: Database, embedder: EmbeddingClient):
    """Idempotently index the bundled legal provisions and precedent corpus."""
    with database.connect() as db:
        for title in RETIRED_SEED_TITLES:
            db.execute("DELETE FROM sources WHERE title=?", (title,))
        existing = {row["title"] for row in db.execute("SELECT title FROM sources")}
    for path, title, legacy in builtin_documents():
        # 清理旧版命名的同名来源（FBM 编号 / 拼音文件名标题），避免新旧重复
        if legacy != title:
            with database.connect() as db:
                db.execute("DELETE FROM sources WHERE title=?", (legacy,))
            existing.discard(legacy)
        if title in existing:
            continue
        try:
            summary = index_document(database, embedder, path, path.name, title)
            existing.add(title)
            LOG.info("seeded knowledge source %s (%d chunks)", title, summary["chunk_count"])
        except ValueError as exc:
            LOG.warning("seed %s failed: %s", path.name, exc)


def create_app() -> Flask:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # Windows 注册表可能把 .js 注册成 text/plain；模块脚本按规范严格校验 MIME，必须纠正
    mimetypes.add_type("text/javascript", ".js")
    mimetypes.add_type("text/javascript", ".mjs")
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES

    raw = (os.getenv("LAW_DATA_DIR") or "").strip()
    data_root = Path(raw).expanduser() if raw else Path.cwd() / ".law-data"
    database = Database(data_root)
    uploads_dir = data_root / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    embedder = EmbeddingClient()
    qa_llm = LLMClient("LAW_LLM")
    court_llm = LLMClient("LAW_COURT_LLM")
    pkulaw = PkulawClient()
    if not pkulaw.configured:
        LOG.info("北大法宝在线检索未配置：设置 LAW_PKULAW_TOKEN 后自动启用")
    legal_logger = logging.getLogger("legalweb")
    if _RING_LOG not in legal_logger.handlers:
        _RING_LOG.setLevel(logging.INFO)
        legal_logger.addHandler(_RING_LOG)
    seed_sources(database, embedder)
    if not qa_llm.configured:
        LOG.warning("法律问答模型未配置：请设置 LAW_LLM_* 环境变量")
    if not court_llm.configured:
        LOG.warning("模拟法庭模型未配置：请设置 LAW_COURT_LLM_* 环境变量")

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
        return jsonify({"llm_configured": qa_llm.configured,
                        "court_llm_configured": court_llm.configured,
                        "embed_configured": embedder.configured,
                        "jev_configured": jev_configured(),
                        "retrieval_backend": "vector+lexical+fts5" if embedder.configured else "lexical+fts5"})

    # ------------------------------------------------------------------
    # 账户体系：注册/登录/登出；业务数据按 u:<user_id> 身份键隔离
    # ------------------------------------------------------------------
    def current_user() -> dict | None:
        """从会话 Cookie 解析当前用户；每请求只查一次库（缓存在 flask.g）。"""
        if "user" in g:
            return g.user or None
        token = request.cookies.get(AUTH_COOKIE, "")
        user = None
        if token:
            with database.connect() as db:
                row = db.execute(
                    "SELECT u.id,u.username,u.is_admin FROM auth_sessions s"
                    " JOIN users u ON u.id=s.user_id"
                    " WHERE s.token=? AND s.expires_at>?",
                    (token, now_iso())).fetchone()
                if row:
                    user = dict(row)
        g.user = user
        return user

    def require_user() -> dict:
        user = current_user()
        if not user:
            abort(401, "请先登录后再使用")
        return user

    def require_admin() -> dict:
        user = require_user()
        if not user["is_admin"]:
            abort(403, "仅管理员可操作")
        return user

    def user_identity(user: dict) -> str:
        return f"u:{user['id']}"

    def public_user(user: dict) -> dict:
        return {"id": user["id"], "username": user["username"],
                "is_admin": bool(user["is_admin"])}

    def issue_session(response, user_id: str) -> None:
        """签发不透明令牌：落库 + httpOnly Cookie（30 天，HTTPS 下自动加 Secure）。"""
        token = secrets.token_urlsafe(32)
        expires = ((datetime.now(timezone.utc) + timedelta(days=AUTH_SESSION_DAYS))
                   .strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z")
        with database.connect() as db:
            db.execute("DELETE FROM auth_sessions WHERE expires_at<=?", (now_iso(),))
            db.execute("INSERT INTO auth_sessions (token,user_id,created_at,expires_at)"
                       " VALUES (?,?,?,?)", (token, user_id, now_iso(), expires))
        secure = (request.is_secure
                  or request.headers.get("X-Forwarded-Proto", "").lower() == "https")
        response.set_cookie(AUTH_COOKIE, token, max_age=AUTH_SESSION_DAYS * 86400,
                            httponly=True, samesite="Lax", secure=secure, path="/")

    def claim_anonymous_data(db, client_id: str, identity: str) -> None:
        """登录/注册时把本浏览器匿名期数据并入账户；会撞唯一键的行跳过。"""
        if (len(client_id) < 8
                or not re.fullmatch(r"[A-Za-z0-9._:-]+", client_id)
                or client_id == identity):
            return
        db.execute("UPDATE conversations SET owner_id=? WHERE owner_id=?",
                   (identity, client_id))
        db.execute(
            "UPDATE pet_progress SET client_id=? WHERE client_id=?"
            " AND NOT EXISTS(SELECT 1 FROM pet_progress WHERE client_id=?)",
            (identity, client_id, identity))
        db.execute(
            "UPDATE pet_memories SET client_id=? WHERE client_id=? AND ("
            " source_event_id='' OR source_event_id NOT IN"
            " (SELECT source_event_id FROM pet_memories WHERE client_id=?"
            "  AND source_event_id<>''))",
            (identity, client_id, identity))
        db.execute(
            "UPDATE pet_reward_events SET client_id=? WHERE client_id=? AND event_id NOT IN"
            " (SELECT event_id FROM pet_reward_events WHERE client_id=?)",
            (identity, client_id, identity))

    @app.post("/api/auth/register")
    def auth_register():
        if not _auth_rate_ok():
            abort(429, "尝试过于频繁，请稍后再试")
        data = request.get_json(force=True, silent=True) or {}
        username = text_value(data, "username", limit=24).strip()
        password = str(data.get("password") or "")
        if not AUTH_USERNAME_RE.fullmatch(username):
            abort(400, "用户名需为 2-24 位字母、数字、下划线或汉字")
        if not AUTH_PASSWORD_MIN <= len(password) <= AUTH_PASSWORD_MAX:
            abort(400, "密码需为 6-128 位")
        with database.connect() as db:
            if db.execute("SELECT 1 FROM users WHERE username=?",
                          (username,)).fetchone():
                abort(409, "用户名已被使用")
            user_id = new_id()
            is_admin = 1 if not db.execute("SELECT 1 FROM users").fetchone() else 0
            db.execute("INSERT INTO users (id,username,password_hash,is_admin,created_at)"
                       " VALUES (?,?,?,?,?)",
                       (user_id, username, generate_password_hash(password),
                        is_admin, now_iso()))
            claim_anonymous_data(db, str(data.get("client_id") or ""), f"u:{user_id}")
        LOG.info("新用户注册 · %s%s", username, "（管理员）" if is_admin else "")
        response = jsonify({"user": {"id": user_id, "username": username,
                                     "is_admin": bool(is_admin)}})
        issue_session(response, user_id)
        return response

    @app.post("/api/auth/login")
    def auth_login():
        if not _auth_rate_ok():
            abort(429, "尝试过于频繁，请稍后再试")
        data = request.get_json(force=True, silent=True) or {}
        username = text_value(data, "username", limit=24).strip()
        password = str(data.get("password") or "")
        with database.connect() as db:
            row = db.execute("SELECT * FROM users WHERE username=?",
                             (username,)).fetchone()
            if not row or not check_password_hash(row["password_hash"], password):
                abort(401, "用户名或密码不正确")
            claim_anonymous_data(db, str(data.get("client_id") or ""),
                                 f"u:{row['id']}")
        response = jsonify({"user": public_user(dict(row))})
        issue_session(response, row["id"])
        return response

    @app.post("/api/auth/logout")
    def auth_logout():
        token = request.cookies.get(AUTH_COOKIE, "")
        if token:
            with database.connect() as db:
                db.execute("DELETE FROM auth_sessions WHERE token=?", (token,))
        response = jsonify({"ok": True})
        response.delete_cookie(AUTH_COOKIE, path="/")
        return response

    @app.get("/api/auth/me")
    def auth_me():
        user = current_user()
        return jsonify({"user": public_user(user) if user else None})

    # ------------------------------------------------------------------
    # 诊断：设置与日志（全局开关，仅管理员）
    # ------------------------------------------------------------------
    @app.get("/api/settings")
    def get_settings():
        require_admin()
        with database.connect() as db:
            enabled = _load_settings(db)
        return jsonify({"features": {
            "vector": {"enabled": enabled["vector"] and embedder.configured,
                       "configured": embedder.configured,
                       "model": embedder.model,
                       "note": "" if embedder.configured else "未配置 LAW_EMBED_BASE_URL，当前为关键词检索"},
            "jev": {"enabled": enabled["jev"] and jev_configured(),
                    "configured": jev_configured(),
                    "model": "jev-latest",
                    "note": "" if jev_configured() else "未配置 TYPESAFE_API_KEY，门控未生效"},
            "pkulaw": {"enabled": enabled["pkulaw"] and pkulaw.configured,
                       "configured": pkulaw.configured,
                       "model": f"语义检索 · 每次取 {pkulaw.size} 条",
                       "note": "" if pkulaw.configured else "未配置 LAW_PKULAW_TOKEN，在线检索停用"},
        }})

    @app.post("/api/settings")
    def update_settings():
        require_admin()
        data = request.get_json(force=True, silent=True) or {}
        updates = {key: bool(data[key]) for key in SETTING_KEYS if key in data}
        if not updates:
            abort(400, "没有可更新的设置项")
        with database.connect() as db:
            for key, value in updates.items():
                db.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                           "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                           (key, "1" if value else "0"))
            enabled = _load_settings(db)
        return jsonify({"ok": True, "features": {
            "vector": enabled["vector"], "jev": enabled["jev"], "pkulaw": enabled["pkulaw"]}})

    @app.get("/api/logs")
    def recent_logs():
        require_admin()
        return jsonify({"logs": list(reversed(_RING_LOG.records))})

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
            rows = db.execute(
                "SELECT c.*, s.title AS source_title FROM chunks c "
                "JOIN sources s ON s.id=c.source_id ORDER BY c.source_id, c.ordinal"
            ).fetchall()
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

    @app.get("/api/kb/<source_id>")
    def kb_detail(source_id):
        with database.connect() as db:
            src = db.execute("SELECT id,title,chunk_count,created_at FROM sources"
                             " WHERE id=?", (source_id,)).fetchone()
            if src is None:
                abort(404, "资料不存在")
            rows = db.execute("SELECT ordinal,section,text FROM chunks"
                              " WHERE source_id=? ORDER BY ordinal", (source_id,)).fetchall()
        return jsonify({"source": dict(src), "chunks": [dict(row) for row in rows]})

    @app.post("/api/kb")
    def kb_upload():
        require_admin()  # 知识库写入影响全体用户并消耗向量化额度
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
    def request_identity(data) -> str:
        request_id = text_value(data, "request_id", limit=128)
        if not request_id:
            return new_id()
        if len(request_id) < 8 or not re.fullmatch(r"[A-Za-z0-9._:-]+", request_id):
            abort(400, "请求标识格式无效")
        return request_id

    # ------------------------------------------------------------------
    # 獬豸桌宠：成长进度（按完成问答/庭审次数积累）
    # ------------------------------------------------------------------
    PET_STAGES = (
        {"key": "cub", "name": "幼獬", "need": 0, "title": "初识人间，蹒跚学步",
         "appearance": "幼角初生，浅色鬃毛"},
        {"key": "dawn", "name": "启蒙", "need": 30, "title": "开始辨认是非曲直",
         "appearance": "独角渐长，步态更轻快"},
        {"key": "keen", "name": "明辨", "need": 100, "title": "目光如炬，善恶分明",
         "appearance": "额角更挺拔，目光更灵动"},
        {"key": "just", "name": "执正", "need": 250, "title": "以角触不直，仗义执言",
         "appearance": "周身显现金色轮廓"},
        {"key": "sage", "name": "神判", "need": 500, "title": "明察秋毫，公正无私",
         "appearance": "鬃毛更丰盛，金辉更明亮"},
        {"key": "luck", "name": "祥瑞", "need": 1000, "title": "德刑昭明，天下大安",
         "appearance": "祥瑞金辉环绕全身"},
    )
    PET_GAINS = {"qa": 2, "court": 10}

    def pet_state(row, client_id: str = "") -> dict:
        exp = row["exp"] if row else 0
        index = 0
        for i, stage in enumerate(PET_STAGES):
            if exp >= stage["need"]:
                index = i
        stage, nxt = PET_STAGES[index], (PET_STAGES[index + 1]
                                         if index + 1 < len(PET_STAGES) else None)
        span = (nxt["need"] - stage["need"]) if nxt else 1
        state = {
            "exp": exp,
            "qa_count": row["qa_count"] if row else 0,
            "court_count": row["court_count"] if row else 0,
            "stage": stage["key"], "stage_index": index,
            "stage_name": stage["name"], "stage_title": stage["title"],
            "stage_appearance": stage["appearance"],
            "next_stage_name": nxt["name"] if nxt else "",
            "next_need": max(nxt["need"] - exp, 0) if nxt else 0,
            "progress": min(int((exp - stage["need"]) * 100 / span), 100),
            "stages": [{"key": s["key"], "name": s["name"], "need": s["need"]}
                       for s in PET_STAGES],
        }
        if client_id:
            with database.connect() as db:
                state.update(load_pet_memories(db, client_id))
                state["recent_rewards"] = load_pet_rewards(db, client_id)
        return state

    def pet_tick(client_id: str, kind: str, event_id: str) -> dict:
        """按唯一业务事件累加经验；同一回答或庭审重试不会重复奖励。"""
        gain = PET_GAINS.get(kind)
        if not gain or not event_id:
            state = pet_state(None, client_id)
            state["gained"] = 0
            return state
        with database.connect() as db:
            inserted = apply_pet_reward(db, client_id, kind, gain, event_id)
            row = db.execute("SELECT * FROM pet_progress WHERE client_id=?",
                             (client_id,)).fetchone()
        state = pet_state(row, client_id)
        state["gained"] = gain if inserted else 0
        return state

    def pet_remember(client_id: str, event_id: str,
                     question: str, answer: str) -> bool:
        """用户主动开启后异步蒸馏记忆；有界队列不阻塞主问答链路。"""
        def work():
            content = pet_memory_content(question, answer, qa_llm.complete)
            try:
                with database.connect() as db:
                    inserted = db.execute(
                        "INSERT OR IGNORE INTO pet_memories"
                        " (client_id,content,source_event_id,created_at) VALUES (?,?,?,?)",
                        (client_id, content, event_id, now_iso())).rowcount == 1
                    if inserted:
                        pet_forget_old(db, client_id)
            except sqlite3.Error:
                LOG.warning("獬豸记忆写入失败", exc_info=True)
        return submit_pet_memory(work)

    @app.get("/api/pet")
    def pet_progress_api():
        identity = user_identity(require_user())
        with database.connect() as db:
            row = db.execute("SELECT * FROM pet_progress WHERE client_id=?",
                             (identity,)).fetchone()
        return jsonify(pet_state(row, identity))

    @app.delete("/api/pet/memories")
    def pet_memories_delete_api():
        identity = user_identity(require_user())
        with database.connect() as db:
            deleted = db.execute("DELETE FROM pet_memories WHERE client_id=?",
                                 (identity,)).rowcount
        return jsonify({"deleted": deleted})

    @app.delete("/api/pet/memories/<int:memory_id>")
    def pet_memory_delete_api(memory_id: int):
        identity = user_identity(require_user())
        with database.connect() as db:
            deleted = db.execute(
                "DELETE FROM pet_memories WHERE id=? AND client_id=?",
                (memory_id, identity)).rowcount
        return jsonify({"deleted": deleted})

    @app.get("/api/conversations")
    def conversation_list():
        identity = user_identity(require_user())
        with database.connect() as db:
            rows = db.execute(
                "SELECT c.id,c.title,c.created_at,"
                " COALESCE((SELECT MAX(m.created_at) FROM messages m"
                " WHERE m.conversation_id=c.id),c.created_at) AS updated_at,"
                " COALESCE((SELECT m.content FROM messages m"
                " WHERE m.conversation_id=c.id ORDER BY m.created_at DESC LIMIT 1),'') AS preview"
                " FROM conversations c WHERE c.owner_id=?"
                " AND EXISTS(SELECT 1 FROM messages m WHERE m.conversation_id=c.id)"
                " ORDER BY updated_at DESC LIMIT 60",
                (identity,),
            ).fetchall()
        return jsonify({"conversations": [dict(row) for row in rows]})

    @app.get("/api/conversations/<conversation_id>")
    def conversation_detail(conversation_id):
        identity = user_identity(require_user())
        with database.connect() as db:
            row = db.execute("SELECT id,title FROM conversations WHERE id=? AND owner_id=?",
                             (conversation_id, identity)).fetchone()
            if not row:
                return jsonify({"conversation": None})
            messages = db.execute("SELECT * FROM messages WHERE conversation_id=?"
                                  " ORDER BY created_at", (conversation_id,)).fetchall()
        return jsonify({"conversation": {"id": row["id"], "title": row["title"]},
                        "messages": [public_message(m) for m in messages]})

    @app.delete("/api/conversations/<conversation_id>")
    def conversation_delete(conversation_id):
        identity = user_identity(require_user())
        with database.connect() as db:
            deleted = db.execute(
                "DELETE FROM conversations WHERE id=? AND owner_id=?",
                (conversation_id, identity),
            ).rowcount
        return jsonify({"deleted": deleted})

    @app.post("/api/ask")
    def ask():
        data = request.get_json(force=True, silent=True) or {}
        question = text_value(data, "question", limit=MAX_QUESTION)
        conversation_id = text_value(data, "conversation_id", limit=128)
        request_id = request_identity(data)
        identity = user_identity(require_user())
        remember_pet = data.get("remember") is True
        if not 2 <= len(question) <= MAX_QUESTION:
            abort(400, "问题长度需在 2-2000 字之间")
        if not qa_llm.configured:
            abort(503, "智能服务暂不可用，请稍后再试")
        with database.connect() as db:
            if conversation_id:
                found = db.execute("SELECT id,owner_id FROM conversations WHERE id=?",
                                   (conversation_id,)).fetchone()
                if not found or found["owner_id"] not in ("", identity):
                    conversation_id = ""
                elif not found["owner_id"]:
                    db.execute("UPDATE conversations SET owner_id=? WHERE id=?",
                               (identity, conversation_id))
            if not conversation_id:
                conversation_id = new_id()
                db.execute("INSERT INTO conversations (id,title,owner_id,created_at)"
                           " VALUES (?,?,?,?)",
                           (conversation_id, question[:60], identity, now_iso()))
            old = db.execute("SELECT role,content FROM messages WHERE conversation_id=?"
                             " ORDER BY created_at DESC LIMIT 20", (conversation_id,)).fetchall()
        previous = [row["content"] for row in reversed(old) if row["role"] == "user"][-3:]
        recent_answers = [row["content"] for row in reversed(old)
                          if row["role"] == "assistant"][-2:]

        def stream():
            yield sse({"type": "status", "text": "正在查找相关法律…"})
            with database.connect() as db:
                features = _load_settings(db)
            chunks = load_chunks()
            retrieval = retrieve(question, chunks, fts_lookup(terms(question)),
                                 embedder if features["vector"] else None,
                                 limit=6, use_gate=features["jev"])
            online_hits = (pkulaw.search(question)
                           if features["pkulaw"] and pkulaw.configured else [])
            backend = retrieval["backend"] + ("+pkulaw" if online_hits else "")
            LOG.info("问答检索 · 本地 %d 段 · 在线 %d 段 · backend=%s",
                     len(retrieval["hits"]), len(online_hits), backend)
            if not retrieval["hits"] and not online_hits:
                payload = {"content": "当前知识库未检索到足以回答的依据。请换个问法或补充关键词（如具体条文、罪名、情形）；我不会编造依据。",
                           "citations": [], "grounded": False,
                           "conversation_id": conversation_id,
                           "backend": retrieval["backend"]}
                with database.connect() as db:
                    user_message_id, assistant_message_id = new_id(), new_id()
                    db.execute("INSERT INTO messages (id,conversation_id,role,content,payload,created_at)"
                               " VALUES (?,?,?,?,?,?)",
                               (user_message_id, conversation_id, "user", question, "{}", now_iso()))
                    db.execute("INSERT INTO messages (id,conversation_id,role,content,payload,created_at)"
                               " VALUES (?,?,?,?,?,?)",
                               (assistant_message_id, conversation_id, "assistant", payload["content"],
                                json.dumps({"grounded": False}), now_iso()))
                yield sse({"type": "result", **payload})
                return
            evidence = qa.Evidence([*retrieval["hits"], *online_hits], question)
            messages = qa.build_messages(question, evidence, previous, recent_answers)
            yield sse({"type": "status", "text": "正在生成回答…", "backend": backend})
            buffer = []
            try:
                for kind, piece in qa_llm.stream(messages, temperature=0.15, max_tokens=3000):
                    if kind == "delta":
                        buffer.append(piece)
                        yield sse({"type": "delta", "text": piece})
            except LLMError as exc:
                yield sse({"type": "error", "error": str(exc)})
                return
            result = qa.sanitize_citations("".join(buffer), evidence.valid_labels)
            citations = [ref for ref in evidence.refs if ref["label"] in set(result["used"])]
            notice = ("回答参考本地知识库与北大法宝在线检索（在线条目附原文链接）；均以官方发布文本为准，不构成法律意见。"
                      if online_hits else
                      "回答仅参考本次检索到的知识库片段，以官方发布文本为准；不构成法律意见。")
            if result["removed"]:
                notice = f"已移除 {result['removed']} 处无效引用。" + notice
            elif not result["used"]:
                notice = "本次回答未携带引用标注，请注意核对原文。" + notice
            with database.connect() as db:
                user_message_id, assistant_message_id = new_id(), new_id()
                db.execute("INSERT INTO messages (id,conversation_id,role,content,payload,created_at)"
                           " VALUES (?,?,?,?,?,?)",
                           (user_message_id, conversation_id, "user", question, "{}", now_iso()))
                db.execute("INSERT INTO messages (id,conversation_id,role,content,payload,created_at)"
                           " VALUES (?,?,?,?,?,?)",
                           (assistant_message_id, conversation_id, "assistant", result["content"],
                            json.dumps({"citations": citations,
                                        "backend": backend,
                                        "grounded": bool(result["used"]),
                                        "notice": notice}, ensure_ascii=False),
                            now_iso()))
            pet = None
            if result["used"]:
                reward_event_id = f"qa:{request_id}"
                pet = pet_tick(identity, "qa", reward_event_id)
                if remember_pet and pet["gained"]:
                    pet_remember(identity, reward_event_id,
                                 question, result["content"])
            yield sse({"type": "result", "content": result["content"], "citations": citations,
                       "grounded": bool(result["used"]), "notice": notice,
                       "backend": backend, "conversation_id": conversation_id, "pet": pet})

        return Response(stream_with_context(stream()), mimetype="text/event-stream")

    # ------------------------------------------------------------------
    # 模拟法庭
    # ------------------------------------------------------------------
    def save_court_session(session_id: str, case_id: str, engine: TrialEngine,
                           *, next_stage: str = "", checkpoint: dict | None = None,
                           owner: str = ""):
        """庭审状态落库：transcript + 引擎状态（检查点续跑用）。"""
        state = engine.export_state()
        state["next_stage"] = next_stage
        state["checkpoint"] = checkpoint
        with database.connect() as db:
            db.execute(
                "INSERT INTO court_sessions (id,case_id,owner_id,transcript,state,created_at)"
                " VALUES (?,?,?,?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET transcript=excluded.transcript,"
                " state=excluded.state",
                (session_id, case_id, owner,
                 json.dumps(engine.transcript, ensure_ascii=False),
                 json.dumps(state, ensure_ascii=False), now_iso()))

    def restore_engine(row) -> TrialEngine:
        case = court_cases.CASES.get(row["case_id"])
        if not case:
            abort(404, "案例不存在")
        engine = TrialEngine(case, court_llm)
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
        if not court_llm.configured:
            abort(503, "模拟法庭服务暂不可用，请稍后再试")
        data = request.get_json(force=True, silent=True) or {}
        player_role = text_value(data, "role", limit=32) or "juror"
        if player_role not in PLAYER_ROLES:
            abort(400, "请选择有效的庭审角色")
        identity = user_identity(require_user())
        session_id = new_id()
        engine = TrialEngine(case, court_llm, player_role=player_role)

        def stream():
            yield sse({"type": "session", "session_id": session_id, "case_id": case_id,
                       "player_role": player_role})
            try:
                for event in engine.run():
                    # 检查点：暂停庭审，等待用户通过 /continue 提交预判或角色发言。
                    if event.get("type") == "checkpoint":
                        save_court_session(session_id, case_id, engine,
                                           next_stage=event["next"], checkpoint=event,
                                           owner=identity)
                        yield sse(event)
                        return
                    yield sse(event)
            except LLMError as exc:
                yield sse({"type": "error", "error": str(exc)})
                return
            save_court_session(session_id, case_id, engine,
                               next_stage="await_deliberation", owner=identity)

        return Response(stream_with_context(stream()), mimetype="text/event-stream")

    @app.post("/api/court/sessions/<session_id>/continue")
    def court_continue(session_id):
        """用户在陪审员或诉讼角色检查点提交内容后，续跑庭审。"""
        identity = user_identity(require_user())
        data = request.get_json(force=True, silent=True) or {}
        with database.connect() as db:
            row = db.execute("SELECT * FROM court_sessions WHERE id=?", (session_id,)).fetchone()
        if not row or row["owner_id"] != identity:
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
        kind = checkpoint["kind"]
        if kind == "role_input":
            statement = text_value(data, "statement", limit=MAX_COURT_STATEMENT)
            if not 10 <= len(statement) <= MAX_COURT_STATEMENT:
                abort(400, "角色发言需在 10-500 字之间")
            payload = {"statement": statement, "stage": checkpoint.get("stage", "")}
        else:
            poll_id = text_value(data, "poll", limit=32)
            if not any(o["id"] == poll_id for o in case["options"]):
                abort(400, "请先选择你的阶段性预判")
            question = text_value(data, "question", limit=MAX_COURT_QUESTION)
            target = text_value(data, "target", limit=32)
            if question and target not in ("prosecutor", "defender", "defendant"):
                abort(400, "发问对象需为公诉人、辩护人或被告人")
            payload = {"poll": poll_id, "question": question, "target": target}
        if not court_llm.configured:
            abort(503, "模拟法庭服务暂不可用，请稍后再试")
        engine = restore_engine(row)
        next_stage = state["next_stage"]

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
                rollback = TrialEngine(case, court_llm)
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
        identity = user_identity(require_user())
        data = request.get_json(force=True, silent=True) or {}
        with database.connect() as db:
            row = db.execute("SELECT * FROM court_sessions WHERE id=?", (session_id,)).fetchone()
        if not row or row["owner_id"] != identity:
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
            pet = pet_tick(identity, "court", f"court:{session_id}")
            if pet:
                yield sse({"type": "pet", "pet": pet})

        return Response(stream_with_context(stream()), mimetype="text/event-stream")

    return app
