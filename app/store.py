"""Persistência em SQLite: definições, fontes, publicações e pendentes."""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class Source:
    chat_id: int
    title: str
    topic_id: int | None
    active: bool


class Store:
    def __init__(self, path: Path | str):
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (k TEXT PRIMARY KEY, v TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sources (
                chat_id INTEGER PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                topic_id INTEGER,
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS posted (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT NOT NULL,
                ts INTEGER NOT NULL,
                src_chat INTEGER,
                src_msg INTEGER,
                title TEXT,
                url TEXT
            );
            CREATE INDEX IF NOT EXISTS posted_key ON posted(key, ts);
            CREATE TABLE IF NOT EXISTS pending (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT NOT NULL,
                ts INTEGER NOT NULL,
                payload TEXT NOT NULL
            );
            """
        )
        self.db.commit()

    # ---- definições ----
    def get(self, key: str, default: Any = None) -> Any:
        row = self.db.execute("SELECT v FROM settings WHERE k=?", (key,)).fetchone()
        if row is None:
            return default
        return json.loads(row["v"])

    def set(self, key: str, value: Any) -> None:
        self.db.execute(
            "INSERT INTO settings(k, v) VALUES(?, ?) "
            "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (key, json.dumps(value)),
        )
        self.db.commit()

    # ---- fontes ----
    @staticmethod
    def _src(row: sqlite3.Row) -> Source:
        return Source(row["chat_id"], row["title"], row["topic_id"], bool(row["active"]))

    def add_source(self, chat_id: int, title: str) -> None:
        self.db.execute(
            "INSERT INTO sources(chat_id, title, active) VALUES(?, ?, 1) "
            "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, active=1",
            (chat_id, title),
        )
        self.db.commit()

    def remove_source(self, chat_id: int) -> None:
        self.db.execute("DELETE FROM sources WHERE chat_id=?", (chat_id,))
        self.db.commit()

    def get_source(self, chat_id: int) -> Source | None:
        row = self.db.execute("SELECT * FROM sources WHERE chat_id=?", (chat_id,)).fetchone()
        return self._src(row) if row else None

    def list_sources(self) -> list[Source]:
        rows = self.db.execute("SELECT * FROM sources ORDER BY title").fetchall()
        return [self._src(r) for r in rows]

    def set_topic(self, chat_id: int, topic_id: int | None) -> bool:
        cur = self.db.execute(
            "UPDATE sources SET topic_id=? WHERE chat_id=?", (topic_id, chat_id)
        )
        self.db.commit()
        return cur.rowcount > 0

    # ---- publicações ----
    def was_posted(self, key: str, hours: int) -> bool:
        since = int(time.time() - hours * 3600)
        row = self.db.execute(
            "SELECT 1 FROM posted WHERE key=? AND ts>=? LIMIT 1", (key, since)
        ).fetchone()
        return row is not None

    def mark_posted(self, key: str, chat: int, msg: int, title: str, url: str) -> None:
        self.db.execute(
            "INSERT INTO posted(key, ts, src_chat, src_msg, title, url) VALUES(?,?,?,?,?,?)",
            (key, int(time.time()), chat, msg, title, url),
        )
        self.db.commit()

    def recent(self, n: int = 10) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM posted ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()

    def count_since(self, seconds: int) -> int:
        since = int(time.time() - seconds)
        return self.db.execute(
            "SELECT COUNT(*) AS c FROM posted WHERE ts>=?", (since,)
        ).fetchone()["c"]

    def last_post_ts(self) -> int | None:
        row = self.db.execute("SELECT MAX(ts) AS t FROM posted").fetchone()
        return row["t"]

    # ---- pendentes (modo manual) ----
    def add_pending(self, key: str, payload: dict) -> int:
        cur = self.db.execute(
            "INSERT INTO pending(key, ts, payload) VALUES(?,?,?)",
            (key, int(time.time()), json.dumps(payload)),
        )
        self.db.commit()
        return int(cur.lastrowid)

    def has_pending_key(self, key: str) -> bool:
        return (
            self.db.execute("SELECT 1 FROM pending WHERE key=? LIMIT 1", (key,)).fetchone()
            is not None
        )

    def pop_pending(self, pending_id: int) -> dict | None:
        row = self.db.execute("SELECT payload FROM pending WHERE id=?", (pending_id,)).fetchone()
        if row is None:
            return None
        self.db.execute("DELETE FROM pending WHERE id=?", (pending_id,))
        self.db.commit()
        return json.loads(row["payload"])

    def count_pending(self) -> int:
        return self.db.execute("SELECT COUNT(*) AS c FROM pending").fetchone()["c"]
