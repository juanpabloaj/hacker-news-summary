# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows semantic versioning for repository tags.

## [Unreleased]

### Changed

- Bumped the package version to `0.2.1`.
- Logged the current service version at startup together with the effective configuration.

## [0.2.0] - 2026-04-16

### Changed

- Made initial Telegram publication atomic for new posts.
- Published new posts only when both article and comments summaries are available.
- Stopped persisting fallback summaries as final state for initial publication.

### Fixed

- Cleared partial publication state so failed posts are retried on later cycles.
- Rolled back the first Telegram message if the second send fails.
- Added tests for partial-state recovery and rollback behavior.
