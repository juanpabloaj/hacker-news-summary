# Repository Guidelines

## Project Structure & Module Organization

This repository contains a scheduled service that scans the Hacker News front page, summarizes qualifying posts, and publishes results to Telegram. The current project contract lives in `README.md`.

Recommended layout for implementation:

- `src/hacker_news_summary_channel/`: application package
- `src/hacker_news_summary_channel/config.py`: environment-based settings
- `src/hacker_news_summary_channel/storage/`: SQLite schema and queries
- `src/hacker_news_summary_channel/clients/`: Hacker News, Gemini, and Telegram integrations
- `src/hacker_news_summary_channel/service/`: polling and orchestration logic
- `tests/`: unit and integration tests
- `data/`: local SQLite database files, ignored by Git

Keep source code, comments, prompts, and documentation in English.

## Build, Test, and Development Commands

The codebase is still being bootstrapped. Use `uv` for Python environment management, dependency installation, and command execution. Document any new commands in `README.md` when added.

Expected commands:

- `uv sync`: install project dependencies
- `uv run ruff format .`: format the repository with Ruff
- `uv run python -m hacker_news_summary_channel`: run one polling cycle locally
- `uv run python -m pytest`: run the full test suite
- `uv run python -m pytest -q`: run tests with concise output

Use `ruff format` before finishing code changes.

## Coding Style & Naming Conventions

Use Python with 4-space indentation and type hints for public functions. Prefer small modules and explicit names.

- modules: `snake_case`
- functions and variables: `snake_case`
- classes: `PascalCase`
- constants and env vars: `UPPER_SNAKE_CASE`

Favor deterministic behavior, short comments, and clear error handling. Do not hardcode machine-specific paths, secrets, or personal information.
Format Python code with Ruff and keep lines within the configured width.

## Testing Guidelines

Use `pytest`. Place tests under `tests/` and name files `test_<module>.py`.

Cover:

- front-page filtering
- duplicate prevention
- comment re-summary threshold logic
- Telegram message publish vs. edit behavior
- configuration parsing and secret masking

Prefer fast unit tests around pure logic, plus a smaller set of integration tests for API clients and SQLite flows.

## Commit & Pull Request Guidelines

There is no commit history yet, so use a simple imperative style:

- `Add SQLite schema for tracked posts`
- `Implement Telegram comments message editing`

Keep commits focused. Pull requests should include:

- a short description of behavior changes
- relevant config changes or new environment variables
- test coverage for new logic
- sample logs or message output when useful

## Security & Configuration Tips

Load runtime settings from environment variables only. Never print API keys or tokens. Log secret fields as `configured` or `missing`, and keep the SQLite database as the source of truth for publication state.
