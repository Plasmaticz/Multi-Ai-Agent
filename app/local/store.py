from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class LocalAppStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA foreign_keys = ON;

                CREATE TABLE IF NOT EXISTS threads (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    run_id TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (thread_id) REFERENCES threads(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    user_message_id TEXT,
                    goal TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY (thread_id) REFERENCES threads(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS agent_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id TEXT NOT NULL,
                    run_id TEXT,
                    agent_name TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (thread_id) REFERENCES threads(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS user_settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    openai_api_key TEXT,
                    openai_model TEXT,
                    max_concurrent_research INTEGER,
                    updated_at TEXT NOT NULL
                );
                """
            )
            connection.commit()

    def list_threads(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    t.id,
                    t.title,
                    t.created_at,
                    t.updated_at,
                    (
                        SELECT content
                        FROM messages m
                        WHERE m.thread_id = t.id
                        ORDER BY m.created_at DESC
                        LIMIT 1
                    ) AS last_message,
                    (
                        SELECT COUNT(*)
                        FROM messages m
                        WHERE m.thread_id = t.id
                    ) AS message_count
                FROM threads t
                ORDER BY t.updated_at DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def create_thread(self, title: str = "New Thread") -> dict[str, Any]:
        thread_id = str(uuid4())
        now = utcnow_iso()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO threads (id, title, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (thread_id, title, now, now),
            )
            connection.commit()
        return self.get_thread(thread_id)

    def get_thread(self, thread_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, title, created_at, updated_at
                FROM threads
                WHERE id = ?
                """,
                (thread_id,),
            ).fetchone()
        return dict(row) if row else None

    def rename_thread_if_placeholder(self, thread_id: str, content: str) -> None:
        thread = self.get_thread(thread_id)
        if not thread or thread["title"] != "New Thread":
            return

        title = " ".join(content.strip().split())
        title = title[:60] or "New Thread"
        self.update_thread(thread_id, title=title)

    def update_thread(self, thread_id: str, *, title: str | None = None) -> None:
        updates: list[str] = ["updated_at = ?"]
        values: list[Any] = [utcnow_iso()]
        if title is not None:
            updates.append("title = ?")
            values.append(title)
        values.append(thread_id)

        with self._connect() as connection:
            connection.execute(
                f"""
                UPDATE threads
                SET {", ".join(updates)}
                WHERE id = ?
                """,
                values,
            )
            connection.commit()

    def list_messages(self, thread_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, thread_id, role, content, run_id, created_at
                FROM messages
                WHERE thread_id = ?
                ORDER BY created_at ASC
                """,
                (thread_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_message(
        self,
        thread_id: str,
        role: str,
        content: str,
        *,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        message_id = str(uuid4())
        now = utcnow_iso()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO messages (id, thread_id, role, content, run_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (message_id, thread_id, role, content, run_id, now),
            )
            connection.execute(
                """
                UPDATE threads
                SET updated_at = ?
                WHERE id = ?
                """,
                (now, thread_id),
            )
            connection.commit()

        return {
            "id": message_id,
            "thread_id": thread_id,
            "role": role,
            "content": content,
            "run_id": run_id,
            "created_at": now,
        }

    def create_run(self, thread_id: str, goal: str, user_message_id: str) -> dict[str, Any]:
        run_id = str(uuid4())
        now = utcnow_iso()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO runs (id, thread_id, user_message_id, goal, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (run_id, thread_id, user_message_id, goal, "running", now),
            )
            connection.commit()
        return {
            "id": run_id,
            "thread_id": thread_id,
            "user_message_id": user_message_id,
            "goal": goal,
            "status": "running",
            "created_at": now,
        }

    def complete_run(self, run_id: str, status: str, result: dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE runs
                SET status = ?, result_json = ?, completed_at = ?
                WHERE id = ?
                """,
                (status, json.dumps(result), utcnow_iso(), run_id),
            )
            connection.commit()

    def list_logs(self, thread_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        query = """
            SELECT id, thread_id, run_id, agent_name, event_type, status, message, created_at
            FROM agent_logs
        """
        params: list[Any] = []
        if thread_id is not None:
            query += " WHERE thread_id = ?"
            params.append(thread_id)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def add_log(
        self,
        *,
        thread_id: str,
        run_id: str | None,
        agent_name: str,
        event_type: str,
        status: str,
        message: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_logs (thread_id, run_id, agent_name, event_type, status, message, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (thread_id, run_id, agent_name, event_type, status, message, utcnow_iso()),
            )
            connection.commit()

    def get_settings(self) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT openai_api_key, openai_model, max_concurrent_research, updated_at
                FROM user_settings
                WHERE id = 1
                """
            ).fetchone()
        return dict(row) if row else {}

    def save_settings(
        self,
        *,
        openai_api_key: str | None,
        openai_model: str,
        max_concurrent_research: int,
    ) -> dict[str, Any]:
        now = utcnow_iso()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO user_settings (id, openai_api_key, openai_model, max_concurrent_research, updated_at)
                VALUES (1, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    openai_api_key = excluded.openai_api_key,
                    openai_model = excluded.openai_model,
                    max_concurrent_research = excluded.max_concurrent_research,
                    updated_at = excluded.updated_at
                """,
                (openai_api_key, openai_model, max_concurrent_research, now),
            )
            connection.commit()
        return self.get_settings()
