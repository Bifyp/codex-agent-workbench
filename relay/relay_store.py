"""Durable controller state, jobs and Telegram delivery queue."""
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


def chunks(text, limit=3700):
    text, start = str(text), 0
    while start < len(text):
        end, units = start, 0
        while end < len(text):
            size = 2 if ord(text[end]) > 0xFFFF else 1
            if units + size > limit:
                break
            units, end = units + size, end + 1
        if end < len(text):
            middle = start + (end - start) // 2
            for separator in ("\n\n", "\n", " "):
                boundary = text.rfind(separator, middle, end)
                if boundary >= middle:
                    end = boundary + len(separator)
                    break
        yield text[start:end]
        start = end


class Store:
    def __init__(self, path):
        self.path = Path(path)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS inbox(id INTEGER PRIMARY KEY, payload TEXT NOT NULL, done INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS jobs(
                    id TEXT PRIMARY KEY, source TEXT UNIQUE, agent TEXT, prompt TEXT,
                    workspace TEXT, topic INTEGER, status TEXT, phase TEXT DEFAULT '',
                    created REAL, error TEXT DEFAULT '', result TEXT DEFAULT '');
                CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY, workspace TEXT, speaker TEXT, text TEXT);
                CREATE TABLE IF NOT EXISTS outbox(
                    id INTEGER PRIMARY KEY, agent TEXT, topic INTEGER, text TEXT,
                    attempts INTEGER DEFAULT 0, due REAL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS routes(topic INTEGER PRIMARY KEY, agent TEXT, note TEXT);
            """)
            for table, column, definition in (
                ("jobs", "metadata", "TEXT NOT NULL DEFAULT '{}'"),
                ("jobs", "available_at", "REAL NOT NULL DEFAULT 0"),
                ("outbox", "payload", "TEXT NOT NULL DEFAULT '{}'"),
            ):
                if column not in {row[1] for row in db.execute(f"PRAGMA table_info({table})")}:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO kv VALUES(?,?)", (key, json.dumps(value)))

    def merge(self, key, changes):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
            value = json.loads(row[0]) if row else {}
            value.update(changes)
            db.execute("INSERT OR REPLACE INTO kv VALUES(?,?)", (key, json.dumps(value)))
        return value

    def enqueue(self, agent, topic, text, payload=None):
        with self.connect() as db:
            pieces = list(chunks(text))
            db.executemany("INSERT INTO outbox(agent,topic,text,payload) VALUES(?,?,?,?)",
                           [(agent, topic, chunk, json.dumps(payload or {}) if index == len(pieces)-1 else '{}')
                            for index, chunk in enumerate(pieces)])

    def history(self, workspace, speaker, text):
        with self.connect() as db:
            db.execute("INSERT INTO history(workspace,speaker,text) VALUES(?,?,?)",
                       (str(workspace), speaker, text))

    def context(self, workspace, limit=24000):
        with self.connect() as db:
            rows = db.execute("SELECT speaker,text FROM history WHERE workspace=? ORDER BY id DESC LIMIT 12",
                              (str(workspace),)).fetchall()
        return "\n\n".join(f"{row['speaker']}: {row['text']}" for row in reversed(rows))[-limit:]

    def accept_update(self, update):
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO inbox(id,payload) VALUES(?,?)",
                       (update["update_id"], json.dumps(update, ensure_ascii=False)))
            db.execute("INSERT OR REPLACE INTO kv VALUES('update_offset',?)",
                       (json.dumps(update["update_id"] + 1),))

    def rows(self, query, args=()):
        with self.connect() as db:
            return [dict(row) for row in db.execute(query, args)]

    def execute(self, query, args=()):
        with self.connect() as db:
            return db.execute(query, args).rowcount

    def new_job(self, job):
        with self.connect() as db:
            cur = db.execute(
                "INSERT OR IGNORE INTO jobs(id,source,agent,prompt,workspace,topic,status,created,metadata) VALUES(?,?,?,?,?,?,'queued',?,?)",
                (job["id"], job["source"], job["agent"], job["prompt"], job["workspace"], job["topic"], time.time(), json.dumps(job.get("metadata", {}))),
            )
            return bool(cur.rowcount)

    def pending(self):
        return self.rows("SELECT * FROM jobs WHERE status IN ('queued','waiting') AND available_at<=? ORDER BY created LIMIT 1", (time.time(),))

    def finish(self, job_id, status, result="", error=""):
        self.execute("UPDATE jobs SET status=?,result=?,error=? WHERE id=?", (status, result, error, job_id))

