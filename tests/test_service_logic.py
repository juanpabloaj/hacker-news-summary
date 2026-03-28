from hacker_news_summary_channel.models import GeminiUsage
from hacker_news_summary_channel.service import (
    CycleStats,
    _format_cycle_stats,
    _format_usage_log,
    _usage_delta,
    should_refresh_comments,
)


def test_should_refresh_comments_when_threshold_is_met() -> None:
    assert should_refresh_comments(
        current_comment_count=150,
        last_summarized_comment_count=100,
        threshold=50,
        updates_done=0,
        max_updates=3,
    )


def test_should_not_refresh_comments_below_threshold() -> None:
    assert not should_refresh_comments(
        current_comment_count=149,
        last_summarized_comment_count=100,
        threshold=50,
        updates_done=0,
        max_updates=3,
    )


def test_should_not_refresh_comments_after_max_updates() -> None:
    assert not should_refresh_comments(
        current_comment_count=200,
        last_summarized_comment_count=100,
        threshold=50,
        updates_done=3,
        max_updates=3,
    )


def test_usage_delta_and_formatting() -> None:
    before = GeminiUsage(prompt_token_count=10, candidates_token_count=5, total_token_count=15)
    after = GeminiUsage(prompt_token_count=40, candidates_token_count=11, total_token_count=51)
    delta = _usage_delta(before, after)
    assert delta.prompt_token_count == 30
    assert delta.candidates_token_count == 6
    assert delta.total_token_count == 36
    assert _format_usage_log(delta) == (
        "prompt_tokens=30, output_tokens=6, cached_tokens=0, thought_tokens=0, total_tokens=36"
    )


def test_cycle_stats_formatting() -> None:
    stats = CycleStats(
        frontpage_posts_seen=30,
        qualifying_posts=10,
        processed_posts=10,
        initial_publications=4,
        comments_updates=2,
        skipped_comment_updates=4,
        failures=1,
        gemini_calls=9,
    )
    assert _format_cycle_stats(stats) == (
        "frontpage_posts_seen=30, qualifying_posts=10, processed_posts=10, "
        "initial_publications=4, comments_updates=2, skipped_comment_updates=4, "
        "failures=1, gemini_calls=9"
    )
