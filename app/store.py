"""Хранилище сессий интервью на SQLite. Одна таблица ответов, ключ (session_id, question_id)."""

import os
import sqlite3
import uuid
from datetime import datetime, timezone

DB_PATH = os.environ.get(
    "INTERVIEW_DB",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "interview.db"),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute(
        """CREATE TABLE IF NOT EXISTS sessions (
               id TEXT PRIMARY KEY,
               created_at TEXT NOT NULL,
               finished_at TEXT
           )"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS answers (
               session_id TEXT NOT NULL,
               question_id TEXT NOT NULL,
               answer TEXT NOT NULL,
               created_at TEXT NOT NULL,
               PRIMARY KEY (session_id, question_id)
           )"""
    )
    con.commit()
    return con


def create_session() -> str:
    sid = uuid.uuid4().hex
    with connect() as con:
        con.execute("INSERT INTO sessions (id, created_at) VALUES (?, ?)", (sid, _now()))
        con.commit()
    return sid


def get_session(sid: str) -> dict | None:
    with connect() as con:
        row = con.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
        if row is None:
            return None
        answers = {
            r["question_id"]: r["answer"]
            for r in con.execute(
                "SELECT question_id, answer FROM answers WHERE session_id = ? ORDER BY created_at",
                (sid,),
            )
        }
    return {"id": row["id"], "created_at": row["created_at"], "finished_at": row["finished_at"], "answers": answers}


def save_answer(sid: str, question_id: str, answer: str) -> None:
    with connect() as con:
        con.execute(
            "INSERT INTO answers (session_id, question_id, answer, created_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(session_id, question_id) DO UPDATE SET answer = excluded.answer, created_at = excluded.created_at",
            (sid, question_id, answer, _now()),
        )
        con.commit()


def mark_finished(sid: str) -> None:
    with connect() as con:
        con.execute("UPDATE sessions SET finished_at = ? WHERE id = ? AND finished_at IS NULL", (_now(), sid))
        con.commit()


def reset() -> None:
    """Только для тестов: чистая база."""
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
