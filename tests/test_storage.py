import sqlite3
from pathlib import Path

from hacker_news_summary_channel.storage import Storage


def test_initialize_adds_no_text_diagnostic_columns_to_existing_database(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE posts (
                hn_id INTEGER PRIMARY KEY
            );
            CREATE TABLE gemini_calls (
                id INTEGER PRIMARY KEY
            );
            """
        )

    storage = Storage(str(db_path))
    storage.initialize()
    storage.initialize()

    with sqlite3.connect(db_path) as conn:
        post_columns = {row[1] for row in conn.execute("PRAGMA table_info(posts)")}
        call_columns = {row[1] for row in conn.execute("PRAGMA table_info(gemini_calls)")}

    assert {"article_summary_terminal_reason", "article_summary_terminal_at"} <= post_columns
    assert {"outcome", "error_reason", "diagnostic_metadata"} <= call_columns
