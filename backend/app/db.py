"""SQLite 持久层：无 ORM，避免额外依赖，所有派生数据均可重建。"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterable

from .settings import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    authors TEXT DEFAULT '[]',
    year INTEGER,
    venue TEXT,
    abstract TEXT,
    doi TEXT,
    arxiv_id TEXT,
    filename TEXT,
    path TEXT,
    num_pages INTEGER DEFAULT 0,
    content_hash TEXT,
    minhash TEXT,
    status TEXT DEFAULT 'ready',
    meta TEXT DEFAULT '{}',
    created_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL,
    page INTEGER,
    bbox TEXT,
    section TEXT,
    level INTEGER DEFAULT 0,
    kind TEXT DEFAULT 'paragraph',
    text TEXT,
    seq INTEGER DEFAULT 0,
    embedding BLOB
);
CREATE TABLE IF NOT EXISTS refs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id TEXT NOT NULL,
    raw TEXT,
    title TEXT,
    doi TEXT,
    arxiv_id TEXT,
    year INTEGER,
    matched_paper_id TEXT
);
CREATE TABLE IF NOT EXISTS translations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    lang TEXT NOT NULL,
    text TEXT,
    created_at TEXT DEFAULT '',
    UNIQUE (chunk_id, lang)
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    kind TEXT,
    payload TEXT,
    trace TEXT,
    final_text TEXT,
    created_at TEXT DEFAULT '',
    finished_at TEXT DEFAULT '',
    duration_ms INTEGER,
    status TEXT DEFAULT 'done'
);
CREATE TABLE IF NOT EXISTS graph_cache (
    version INTEGER PRIMARY KEY,
    data TEXT NOT NULL,
    updated_at TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_chunks_paper ON chunks(paper_id, seq);
CREATE INDEX IF NOT EXISTS idx_refs_paper ON refs(paper_id);
CREATE INDEX IF NOT EXISTS idx_trans_chunk ON translations(chunk_id, lang);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at DESC);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        # 对已有 DB 做最小 migration：确保 runs/final_text 列存在
        try:
            conn.execute("ALTER TABLE runs ADD COLUMN final_text TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE runs ADD COLUMN finished_at TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE runs ADD COLUMN duration_ms INTEGER")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE runs ADD COLUMN status TEXT DEFAULT 'done'")
        except sqlite3.OperationalError:
            pass
        # 上次进程遗留的 running 记录不可能再恢复，标记为中断，避免历史列表一直显示「运行中」
        try:
            conn.execute("UPDATE runs SET status='interrupted' WHERE status='running'")
        except sqlite3.OperationalError:
            pass


def query(sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(sql, tuple(params)).fetchall()


def execute(sql: str, params: Iterable[Any] = ()) -> None:
    with get_conn() as conn:
        conn.execute(sql, tuple(params))


def row_to_dict(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in row.keys()}


def loads(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default
