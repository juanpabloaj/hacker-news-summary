"""Hacker News Summary Channel package."""

from importlib.metadata import PackageNotFoundError, version


__version__ = "0.2.1"


def get_version() -> str:
    return __version__


def get_installed_version() -> str | None:
    try:
        return version("hacker-news-summary-channel")
    except PackageNotFoundError:
        return None
