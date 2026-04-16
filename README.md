# Hacker News Summary Channel

`hacker_news_summary_channel` is a scheduled Python service that watches the Hacker News front page, summarizes selected posts and their comment threads in English, and publishes the results to a Telegram channel.

The service is designed to run from `cron` or another external scheduler. Each run performs a single polling cycle and exits.

When started from the repository or one of its subdirectories, the service automatically loads variables from a local `.env` file. For local runs, values from `.env` override inherited shell variables.

## Quick Start

```bash
uv sync --extra dev
cp .env.example .env
uv run python -m hacker_news_summary_channel
```

Useful commands:

```bash
uv run ruff format .
uv run python -m pytest
```

## Configuration

All runtime settings come from environment variables.

Common non-sensitive settings:

```env
POLL_INTERVAL_MINUTES=60
HN_MIN_POINTS=100
COMMENT_RESUMMARY_THRESHOLD=50
MAX_COMMENT_UPDATES_PER_POST=3
GEMINI_MODEL=gemini-2.5-flash-lite
DB_PATH=data/app.db
LOG_LEVEL=INFO
TELEGRAM_PARSE_MODE=HTML
TELEGRAM_MAX_MESSAGE_CHARS=4096
REQUEST_TIMEOUT_SECONDS=20
GEMINI_TIMEOUT_SECONDS=60
GEMINI_MAX_RETRIES=4
GEMINI_RETRY_DELAY_SECONDS=4
ARTICLE_MAX_CHARS=20000
COMMENTS_MAX_CHARS=24000
ARTICLE_SUMMARY_MAX_CHARS=1400
COMMENTS_SUMMARY_MAX_CHARS=2200
```

Required sensitive settings:

```env
GEMINI_API_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHANNEL_ID=
```

## Runtime Behavior

On each cycle, the service:

1. Fetches the Hacker News front page.
2. Filters posts with `score >= HN_MIN_POINTS`.
3. Persists state in SQLite.
4. Publishes two Telegram messages for new qualifying posts:
   - one article summary message
   - one comments summary message
   - only after both summaries have been generated successfully
5. Edits only the comments message when the comment increase reaches `COMMENT_RESUMMARY_THRESHOLD`.
6. Marks posts as inactive when they leave the front page.

For article content, the service uses a hybrid strategy:

- local HTTP fetch plus local text extraction first
- Gemini URL context as a fallback if local extraction fails and a URL exists
- article-summary fallback text if both fail

If Gemini daily quota is exhausted, the service stops making new Gemini requests for the rest of the current cycle and falls back gracefully. Initial publications are deferred until both summaries are available; fallback placeholder summaries are not published for new posts.

## Telegram Output

Each tracked post produces exactly two Telegram messages.

Article message:

```text
Example Post Title
example.com • 187 points • 64 comments
https://news.ycombinator.com/item?id=12345678

<summary>
```

Comments message:

```text
Comments Summary for: Example Post Title
https://news.ycombinator.com/item?id=12345678

<summary>
```

The service enforces Telegram's message-size limit and sanitizes summaries to remove Markdown-style formatting that hurts readability in Telegram.

## Model and Quota Notes

The service uses Gemini via `GEMINI_MODEL`. Do not assume the model name shown in Google AI Studio is the exact API model ID.

Before changing models, verify both:

- the exact model ID supported by your API key
- the effective quotas for that key and project

Reference:

- https://aistudio.google.com/rate-limit

Published quota tables are only general guidance. The effective quota for a specific project can differ from the public documentation.

## Logging and Failure Behavior

The service logs:

- effective startup configuration, with secrets masked
- per-cycle Gemini token usage totals and deltas
- per-cycle operational counts such as qualifying posts, publications, updates, failures, and Gemini call count

Expected failures are handled defensively:

- article fetch failures do not block comment summaries
- Gemini failures fall back internally and defer initial publication to a later cycle
- duplicate Telegram publications are avoided through SQLite state
- partial initial Telegram publications are rolled back if the second send fails
- one post failure does not stop the rest of the cycle

## Execution

Example `cron` entry:

```cron
0 * * * * /path/to/venv/bin/python -m hacker_news_summary_channel
```

The process runs one cycle and exits.

## Development

- All source code, comments, prompts, logs, and documentation must be in English.
- Use `uv` for dependency management and execution.
- Format Python code with `uv run ruff format .`.
- Run tests with `uv run python -m pytest`.

## Appendix

### Data Stored in SQLite

The local database keeps enough state to support idempotency, auditing, and updates. Main record groups:

- `posts`: tracked post state and Telegram message IDs
- `post_snapshots`: score and comment snapshots over time
- `article_summaries`: stored article summaries by content hash
- `article_fetches`: fetched raw content, prepared Gemini input, and fetch errors
- `comment_summaries`: stored comments summaries by comment-tree hash
- `gemini_calls`: per-call token usage and model metadata

### Operational Scope

The service treats the Hacker News front page as the first page of `news.ycombinator.com` and supports normal links, `Ask HN`, and `Show HN` posts. For posts without a meaningful external URL, it may summarize the Hacker News post text instead.
