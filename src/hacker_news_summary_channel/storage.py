from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from .models import FrontPagePost, GeminiUsage, PostRecord


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Storage:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._ensure_parent_dir()

    def initialize(self) -> None:
        with self.connection() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;

                CREATE TABLE IF NOT EXISTS posts (
                    hn_id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    url TEXT,
                    domain TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    current_score INTEGER NOT NULL,
                    current_comment_count INTEGER NOT NULL,
                    current_frontpage_rank INTEGER NOT NULL,
                    is_frontpage_active INTEGER NOT NULL,
                    article_message_id INTEGER,
                    comments_message_id INTEGER,
                    comment_update_count INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS post_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    hn_id INTEGER NOT NULL,
                    captured_at TEXT NOT NULL,
                    score INTEGER NOT NULL,
                    comment_count INTEGER NOT NULL,
                    frontpage_rank INTEGER NOT NULL,
                    FOREIGN KEY (hn_id) REFERENCES posts (hn_id)
                );

                CREATE TABLE IF NOT EXISTS article_summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    hn_id INTEGER NOT NULL,
                    content_hash TEXT,
                    model_name TEXT NOT NULL,
                    summary_text TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (hn_id) REFERENCES posts (hn_id)
                );

                CREATE TABLE IF NOT EXISTS comment_summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    hn_id INTEGER NOT NULL,
                    comment_tree_hash TEXT,
                    comment_count INTEGER NOT NULL,
                    model_name TEXT NOT NULL,
                    summary_text TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (hn_id) REFERENCES posts (hn_id)
                );

                CREATE TABLE IF NOT EXISTS article_fetches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    hn_id INTEGER NOT NULL,
                    fetch_method TEXT NOT NULL,
                    source_url TEXT,
                    raw_content TEXT,
                    gemini_input_text TEXT,
                    content_hash TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (hn_id) REFERENCES posts (hn_id)
                );

                CREATE TABLE IF NOT EXISTS gemini_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    hn_id INTEGER,
                    operation TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    response_id TEXT,
                    prompt_token_count INTEGER NOT NULL DEFAULT 0,
                    candidates_token_count INTEGER NOT NULL DEFAULT 0,
                    cached_content_token_count INTEGER NOT NULL DEFAULT 0,
                    thoughts_token_count INTEGER NOT NULL DEFAULT 0,
                    total_token_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (hn_id) REFERENCES posts (hn_id)
                );
                """
            )

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def upsert_post(self, post: FrontPagePost) -> PostRecord:
        now = utc_now()
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO posts (
                    hn_id, title, url, domain, first_seen_at, last_seen_at,
                    current_score, current_comment_count, current_frontpage_rank,
                    is_frontpage_active
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(hn_id) DO UPDATE SET
                    title = excluded.title,
                    url = excluded.url,
                    domain = excluded.domain,
                    last_seen_at = excluded.last_seen_at,
                    current_score = excluded.current_score,
                    current_comment_count = excluded.current_comment_count,
                    current_frontpage_rank = excluded.current_frontpage_rank,
                    is_frontpage_active = 1
                """,
                (
                    post.hn_id,
                    post.title,
                    post.url,
                    post.domain,
                    now,
                    now,
                    post.score,
                    post.comment_count,
                    post.rank,
                ),
            )
            conn.execute(
                """
                INSERT INTO post_snapshots (hn_id, captured_at, score, comment_count, frontpage_rank)
                VALUES (?, ?, ?, ?, ?)
                """,
                (post.hn_id, now, post.score, post.comment_count, post.rank),
            )
            row = conn.execute("SELECT * FROM posts WHERE hn_id = ?", (post.hn_id,)).fetchone()
        return _row_to_post_record(row)

    def mark_missing_posts_inactive(self, active_ids: set[int]) -> None:
        with self.connection() as conn:
            if active_ids:
                placeholders = ", ".join("?" for _ in active_ids)
                conn.execute(
                    f"UPDATE posts SET is_frontpage_active = 0 WHERE hn_id NOT IN ({placeholders})",
                    tuple(active_ids),
                )
            else:
                conn.execute("UPDATE posts SET is_frontpage_active = 0")

    def set_article_message_id(self, hn_id: int, message_id: int) -> None:
        with self.connection() as conn:
            conn.execute(
                "UPDATE posts SET article_message_id = ? WHERE hn_id = ?",
                (message_id, hn_id),
            )

    def set_comments_message_id(self, hn_id: int, message_id: int) -> None:
        with self.connection() as conn:
            conn.execute(
                "UPDATE posts SET comments_message_id = ? WHERE hn_id = ?",
                (message_id, hn_id),
            )

    def increment_comment_update_count(self, hn_id: int) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                UPDATE posts
                SET comment_update_count = comment_update_count + 1
                WHERE hn_id = ?
                """,
                (hn_id,),
            )

    def store_article_summary(
        self, hn_id: int, content_hash: str | None, model_name: str, summary_text: str
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO article_summaries (hn_id, content_hash, model_name, summary_text, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (hn_id, content_hash, model_name, summary_text, utc_now()),
            )

    def store_article_fetch(
        self,
        hn_id: int,
        fetch_method: str,
        source_url: str | None,
        raw_content: str | None,
        gemini_input_text: str | None,
        content_hash: str | None,
        error_message: str | None,
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO article_fetches (
                    hn_id, fetch_method, source_url, raw_content, gemini_input_text,
                    content_hash, error_message, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    hn_id,
                    fetch_method,
                    source_url,
                    raw_content,
                    gemini_input_text,
                    content_hash,
                    error_message,
                    utc_now(),
                ),
            )

    def store_comment_summary(
        self,
        hn_id: int,
        comment_tree_hash: str | None,
        comment_count: int,
        model_name: str,
        summary_text: str,
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO comment_summaries (
                    hn_id, comment_tree_hash, comment_count, model_name, summary_text, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (hn_id, comment_tree_hash, comment_count, model_name, summary_text, utc_now()),
            )

    def store_gemini_call(
        self,
        hn_id: int | None,
        operation: str,
        model_name: str,
        response_id: str | None,
        usage: GeminiUsage,
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO gemini_calls (
                    hn_id, operation, model_name, response_id,
                    prompt_token_count, candidates_token_count,
                    cached_content_token_count, thoughts_token_count,
                    total_token_count, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    hn_id,
                    operation,
                    model_name,
                    response_id,
                    usage.prompt_token_count,
                    usage.candidates_token_count,
                    usage.cached_content_token_count,
                    usage.thoughts_token_count,
                    usage.total_token_count,
                    utc_now(),
                ),
            )

    def get_gemini_usage_totals(self) -> GeminiUsage:
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT
                    COALESCE(SUM(prompt_token_count), 0) AS prompt_token_count,
                    COALESCE(SUM(candidates_token_count), 0) AS candidates_token_count,
                    COALESCE(SUM(cached_content_token_count), 0) AS cached_content_token_count,
                    COALESCE(SUM(thoughts_token_count), 0) AS thoughts_token_count,
                    COALESCE(SUM(total_token_count), 0) AS total_token_count
                FROM gemini_calls
                """
            ).fetchone()
        return GeminiUsage(
            prompt_token_count=int(row["prompt_token_count"]),
            candidates_token_count=int(row["candidates_token_count"]),
            cached_content_token_count=int(row["cached_content_token_count"]),
            thoughts_token_count=int(row["thoughts_token_count"]),
            total_token_count=int(row["total_token_count"]),
        )

    def get_gemini_call_count(self) -> int:
        with self.connection() as conn:
            row = conn.execute("SELECT COUNT(*) AS call_count FROM gemini_calls").fetchone()
        return int(row["call_count"])

    def get_latest_article_summary(self, hn_id: int) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute(
                """
                SELECT * FROM article_summaries
                WHERE hn_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (hn_id,),
            ).fetchone()

    def get_latest_comment_summary(self, hn_id: int) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute(
                """
                SELECT * FROM comment_summaries
                WHERE hn_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (hn_id,),
            ).fetchone()

    def get_post(self, hn_id: int) -> PostRecord | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM posts WHERE hn_id = ?", (hn_id,)).fetchone()
        return _row_to_post_record(row) if row else None

    def _ensure_parent_dir(self) -> None:
        parent = os.path.dirname(self.db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)


def _row_to_post_record(row: sqlite3.Row) -> PostRecord:
    return PostRecord(
        hn_id=int(row["hn_id"]),
        title=str(row["title"]),
        url=row["url"],
        domain=row["domain"],
        current_score=int(row["current_score"]),
        current_comment_count=int(row["current_comment_count"]),
        current_frontpage_rank=int(row["current_frontpage_rank"]),
        article_message_id=row["article_message_id"],
        comments_message_id=row["comments_message_id"],
        comment_update_count=int(row["comment_update_count"]),
    )
