# Hacker News Summary Channel

`hacker_news_summary_channel` is a scheduled service that watches the Hacker News front page, selects high-signal posts, summarizes both the linked article and the Hacker News discussion, and publishes the results to a Telegram channel in English.

The service is designed to run from `cron` or any external scheduler. Each execution performs one polling cycle and exits.

When started from the repository or one of its subdirectories, the service automatically loads variables from a local `.env` file. For local runs, values from `.env` override inherited shell variables so the repository configuration remains the source of truth.

## Quick Start

```bash
uv sync
cp .env.example .env
uv run python -m hacker_news_summary_channel
```

For formatting:

```bash
uv run ruff format .
```

For test runs:

```bash
uv run python -m pytest
```

## Goals

- Monitor the Hacker News front page.
- Process posts with a score above a configurable threshold.
- Create one English summary for the linked content.
- Create one English summary for the Hacker News comments.
- Publish exactly two Telegram messages per tracked post:
  - one message for the article summary
  - one message for the comments summary
- Avoid duplicate publications by storing state in a local database.
- While a post remains on the front page, update the Telegram comments message only when the number of comments has increased significantly.

## Expected Behavior

On each polling cycle, the service should:

1. Fetch the current Hacker News front page.
2. Select posts with `score >= HN_MIN_POINTS`.
3. Persist the latest post metadata and snapshot values.
4. For a post not yet published:
   - fetch and extract the linked content
   - if local fetch or extraction fails and a URL exists, retry article summarization using Gemini URL context
   - summarize the linked content in English
   - fetch the Hacker News comments
   - summarize the comments in English
   - publish two Telegram messages
   - store the Telegram message IDs and summary metadata
5. For a post that was already published and is still on the front page:
   - compare the current comment count with the comment count used for the last published comments summary
   - if the increase is below the configured threshold, do nothing
   - if the increase meets or exceeds the configured threshold, generate a new comments summary and edit the existing Telegram comments message
6. For a post that is no longer on the front page:
   - mark it as inactive for front-page monitoring
   - keep historical data in the database

## Telegram Publishing Model

Each tracked post produces exactly two Telegram messages:

1. An article summary message.
2. A comments summary message.

The article summary message is published once and is never edited unless that behavior is explicitly added later.

If the linked page cannot be fetched or summarized because of timeouts, anti-bot protection, paywalls, unsupported rendering, or similar issues, the article message should still be published in the normal format. In that case, the summary section must explicitly state that the article summary could not be generated.

The comments summary message is published once and may be edited in place if:

- the post is still on the Hacker News front page, and
- the comment count increased by at least `COMMENT_RESUMMARY_THRESHOLD` since the last comments summary

### Message Format

The article message should stay compact and include only the minimum context needed to understand the summary:

```text
Example Post Title
example.com • 187 points • 64 comments
https://news.ycombinator.com/item?id=12345678

<summary>
```

If article summarization fails, the same format should be used and only the summary body should change:

```text
Example Post Title
example.com • 187 points • 64 comments
https://news.ycombinator.com/item?id=12345678

<could not generate article summary>
```

The comments message should assume it appears immediately after the article message and avoid repeating unnecessary metadata:

```text
Comments Summary for: Example Post Title
https://news.ycombinator.com/item?id=12345678

<summary>
```

The implementation should enforce Telegram's text-message limit before sending or editing a message. The official Bot API documentation for `sendMessage` and `editMessageText` specifies `1-4096 characters after entities parsing`, so summaries must be constrained accordingly.

## Proposed Architecture

The initial implementation should be a small Python service composed of these modules:

- `config`: environment-based configuration with defaults
- `hn_client`: Hacker News API integration
- `content_fetcher`: linked-page download and content extraction
- `summarizer`: Gemini API integration
- `publisher`: Telegram Bot API integration
- `storage`: SQLite schema and queries
- `service`: one polling cycle orchestration
- `main`: startup, configuration logging, and cycle execution

Each cycle should log a compact operational summary including:

- front-page posts seen
- qualifying posts
- processed posts
- initial publications
- comments-message updates
- skipped comment updates
- failures
- Gemini call count

## Data Model

The local database should store enough information to avoid duplicate work and support safe updates.

Suggested tables:

### `posts`

- Hacker News item ID
- title
- URL
- domain
- first seen timestamp
- last seen timestamp
- current score
- current comment count
- current front page rank
- front page active flag
- article Telegram message ID
- comments Telegram message ID

### `post_snapshots`

- post reference
- captured timestamp
- score
- comment count
- front page rank

### `article_summaries`

- post reference
- content hash
- model name
- summary text
- creation timestamp

### `article_fetches`

- post reference
- fetch method
- source URL
- raw fetched content
- Gemini input text
- content hash
- error message
- creation timestamp

### `comment_summaries`

- post reference
- comment tree hash
- comment count
- model name
- summary text
- creation timestamp

### `gemini_calls`

- optional post reference
- operation name
- model name
- Gemini response ID
- prompt token count
- output token count
- cached token count
- thoughts token count
- total token count
- creation timestamp

### `publications`

- post reference
- Telegram message ID
- publication type
- timestamp

## Hacker News Scope

The service should treat the Hacker News front page as the first page of `news.ycombinator.com`.

The default v1 scope should include:

- normal link posts
- `Ask HN` posts
- `Show HN` posts

For posts without a meaningful external URL, the service may summarize the Hacker News post text instead of linked content.

## Content Extraction

The linked page should be converted into clean text before it is sent to the summarizer.

Important requirements:

- handle plain HTML pages well
- tolerate pages with noisy markup
- fail gracefully on inaccessible or unsupported pages
- store the fetched raw content and the exact text prepared for Gemini to support debugging
- store enough metadata to skip repeated work when the extracted content has not changed

If extraction fails, the service should still be able to publish the comments summary for the Hacker News thread, while clearly indicating that the article content could not be summarized.

The recommended strategy is hybrid:

- first try local HTTP fetch plus local text extraction
- if that fails and the post has a URL, try Gemini URL context as a fallback
- if both fail, publish the article message with the article-summary fallback text

## Summarization Strategy

The summaries should always be generated in English, regardless of the source language.

The initial plan is to use Gemini free tier, with the model controlled through environment variables.

Recommended default:

- `GEMINI_MODEL=gemini-2.5-flash-lite`

Reasons:

- higher daily request allowance than other free-tier Flash options
- sufficient quality for concise article and comment summaries
- lower risk of exhausting the daily quota
- per-call usage metadata can be persisted and summed for cycle-level token logs

### Suggested Prompting Rules

Article summary should emphasize:

- what the linked content is about
- the main claims or findings
- notable context or implications

Comments summary should emphasize:

- the main themes discussed by Hacker News users
- major disagreements or concerns
- useful links, corrections, or expert insights mentioned in the thread

The implementation should keep prompts deterministic, concise, and versioned where practical.

## Rate Limit Strategy

Free-tier usage is viable if the service is conservative about comment re-summarization.

The default strategy should be:

- summarize new qualifying posts once
- only re-summarize comments when the increase is significant
- cache article summaries by extracted content hash
- cache comment summaries by normalized comment tree hash
- never re-summarize comments more than once per polling cycle
- optionally cap the number of comments-message edits per post

Recommended defaults:

- `HN_MIN_POINTS=100`
- `COMMENT_RESUMMARY_THRESHOLD=50`
- `MAX_COMMENT_UPDATES_PER_POST=3`
- `POLL_INTERVAL_MINUTES=60`

These values should remain configurable through environment variables.

## Configuration

All operational settings should come from environment variables.

### Non-sensitive variables with defaults

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
ARTICLE_MAX_CHARS=20000
COMMENTS_MAX_CHARS=24000
ARTICLE_SUMMARY_MAX_CHARS=1400
COMMENTS_SUMMARY_MAX_CHARS=2200
```

### Required sensitive variables

```env
GEMINI_API_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHANNEL_ID=
```

## Startup Configuration Output

When the service starts, it should print the effective non-sensitive configuration values.

Sensitive values must never be printed. Instead, their presence should be reported as status only.

Example:

```text
Configuration:
  poll_interval_minutes: 60
  hn_min_points: 100
  comment_resummary_threshold: 50
  max_comment_updates_per_post: 3
  gemini_model: gemini-2.5-flash-lite
  db_path: data/app.db
  log_level: INFO
  telegram_parse_mode: HTML
  telegram_max_message_chars: 4096
  request_timeout_seconds: 20
  article_max_chars: 20000
  comments_max_chars: 24000
  article_summary_max_chars: 1400
  comments_summary_max_chars: 2200
  gemini_api_key: configured
  telegram_bot_token: configured
  telegram_channel_id: configured
```

## Failure Handling

The service should be robust to partial failures.

Expected failure cases:

- Hacker News API unavailable
- article download timeout
- extraction failure
- Gemini rate limiting or transient errors
- Telegram API errors
- malformed or deleted comments

Expected behavior:

- log failures with enough context for debugging
- avoid publishing duplicate messages
- preserve prior successful data in SQLite
- continue processing other posts if one post fails

## Idempotency

One polling cycle should be safe to run more than once.

Important idempotency rules:

- do not publish duplicate article messages for the same post
- do not publish duplicate comments messages for the same post
- only edit the stored comments message for updates
- use persisted message IDs and summary records as the source of truth

## Execution Model

This project is intended to be called externally, for example by `cron`.

Example schedule:

```cron
0 * * * * /path/to/venv/bin/python -m hacker_news_summary_channel
```

The service itself should execute a single cycle and exit with a meaningful status code.

## Initial Implementation Scope

The first implementation milestone should include:

- environment-based configuration
- SQLite persistence
- Hacker News front-page polling
- article extraction
- Gemini summarization
- Telegram publishing
- comments-message editing when the configured threshold is reached
- startup configuration display with secret masking

The first implementation may defer:

- advanced retry policies
- metrics export
- multi-model fallback
- admin dashboard
- background daemon mode

## Development Principles

- All source code, comments, logs, prompts, and documentation are written in English.
- Public repository content must not include machine-specific personal paths or user-specific information.
- Configuration should be explicit and easy to audit.
- Behavior should be deterministic where possible.
- The database should be considered the source of truth for publication state.

## Next Step

After this README, the next implementation step should define:

1. project structure
2. dependency list
3. SQLite schema
4. configuration loader
5. the first end-to-end polling cycle
