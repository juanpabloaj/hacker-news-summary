# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows semantic versioning for repository tags.

## [Unreleased]

### Changed

- Added one URL-context fallback when Gemini returns a successful article response without text.
- Skipped comment summarization when the article summary is unavailable.
- Included no-text Gemini calls in cycle call, usage, and failure metrics.

### Fixed

- Retried transient Telegram API failures (429/5xx, connection errors) instead of failing the post,
  honoring the `retry_after` hint on rate limits.
- Classified no-text Gemini responses using prompt-block and candidate-finish metadata.
- Persisted sanitized no-text diagnostics and terminal article failures to prevent repeated blocked
  requests in later cycles.

## [0.2.1] - 2026-05-22

### Changed

- Bumped the package version to `0.2.1`.
- Logged the current service version at startup together with the effective configuration.
- Added a per-cycle circuit breaker for repeated transient Gemini failures such as `503` responses and timeouts.
- Made startup version logging prefer the repository version over stale installed package metadata.

### Fixed

- Handled remote article fetch disconnects as recoverable fetch failures so posts can continue through fallback summarization.

## [0.2.0] - 2026-04-16

### Changed

- Made initial Telegram publication atomic for new posts.
- Published new posts only when both article and comments summaries are available.
- Stopped persisting fallback summaries as final state for initial publication.

### Fixed

- Cleared partial publication state so failed posts are retried on later cycles.
- Rolled back the first Telegram message if the second send fails.
- Added tests for partial-state recovery and rollback behavior.
