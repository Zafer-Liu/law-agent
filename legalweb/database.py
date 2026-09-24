"""SQLite 持久化：知识库分块(+FTS5)、问答会话、模拟法庭评议记录。

单租户演示应用，无用户体系；所有写入都通过 Database.connect() 的事务上下文。
"""

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY, title TEXT NOT NULL UNIQUE, filename TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ready', error TEXT NOT NULL DEFAULT '',
    chunk_count INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL, section TEXT NOT NULL, text TEXT NOT NULL,
    embedding TEXT, embedding_space TEXT,
    UNIQUE(source_id, ordinal)
);
CREATE INDEX IF NOT EXISTS chunks_source ON chunks(source_id, ordinal);
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, owner_id TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK(role IN ('user','assistant')), content TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_scope ON messages(conversation_id, created_at);
CREATE TABLE IF NOT EXISTS court_sessions (
    id TEXT PRIMARY KEY, case_id TEXT NOT NULL,
    transcript TEXT NOT NULL DEFAULT '[]',
    deliberation TEXT, analysis TEXT,
    state TEXT,
    created_at TEXT NOT NULL, finished_at TEXT
);
CREATE INDEX IF NOT EXISTS court_sessions_case ON court_sessions(case_id, created_at);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def new_id() -> str:
    """Return a collision-resistant identifier safe for concurrent requests.

    The former timestamp/object-address construction could repeat when two
    requests arrived in the same millisecond: CPython is free to reuse the
    address of the short-lived object immediately.
    """
    return uuid.uuid4().hex


class Database:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "law.sqlite3"
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(tokens)")
            db.executescript("""
                CREATE TRIGGER IF NOT EXISTS chunks_delete_fts AFTER DELETE ON chunks BEGIN
                    DELETE FROM chunks_fts WHERE rowid=old.id;
                END;
            """)
            # 旧库迁移：court_sessions 增加 state 列（庭审检查点续跑状态）
            try:
                db.execute("ALTER TABLE court_sessions ADD COLUMN state TEXT")
            except sqlite3.OperationalError:
                pass  # 列已存在
            # 旧库迁移：匿名浏览器标识隔离各自的问答历史，避免公开站点串话。
            try:
                db.execute("ALTER TABLE conversations ADD COLUMN owner_id TEXT NOT NULL DEFAULT ''")
            except sqlite3.OperationalError:
                pass  # 列已存在
            db.execute("CREATE INDEX IF NOT EXISTS conversations_owner"
                       " ON conversations(owner_id, created_at)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=30000")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


def public_message(row) -> dict:
    result = json.loads(row["payload"] or "{}")
    result.update({key: row[key] for key in ("id", "role", "content", "created_at")})
    return result
