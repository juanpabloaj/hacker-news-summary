"""Hacker News Summary Channel package."""

from importlib.metadata import PackageNotFoundError, version


__version__ = "0.2.1"


def get_version() -> str:
    try:
        return version("hacker-news-summary-channel")
    except PackageNotFoundError:
        return __version__
