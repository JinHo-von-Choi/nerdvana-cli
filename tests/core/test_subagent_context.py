"""create_shared_context 요약 분기 테스트 (subagent.py:53-94)."""

from __future__ import annotations

from nerdvana_cli.core.delegation.subagent import create_shared_context


def test_empty_messages_returns_empty() -> None:
    assert create_shared_context([], max_summary_tokens=100) == ""


def test_short_user_intent_ignored() -> None:
    summary = create_shared_context([{"role": "user", "content": "go now"}], 100)
    assert summary == ""


def test_user_question_mark_splits_sentence() -> None:
    summary = create_shared_context(
        [{"role": "user", "content": "Can you fix the ssl renewal? Then wait for me"}],
        100,
    )
    assert "Goals:" in summary
    assert "?" not in summary.split("Goals:")[1].split(";")[0]


def test_long_user_intent_collected() -> None:
    summary = create_shared_context(
        [{"role": "user", "content": "Find the root cause of the nginx ssl renewal failure. then wait"}],
        100,
    )
    assert "Goals:" in summary
    assert "nginx" in summary


def test_assistant_outcome_keywords_only() -> None:
    summary = create_shared_context(
        [
            {"role": "assistant", "content": "nothing relevant"},
            {"role": "assistant", "content": "Fixed the ssl renewal by reloading nginx"},
        ],
        100,
    )
    assert "Results:" in summary
    assert "Fixed" in summary


def test_truncates_by_max_summary_tokens() -> None:
    messages: list[dict[str, str]] = []
    for i in range(5):
        messages.append(
            {"role": "user", "content": f"Long goal sentence number {i} about rebuilding the cache now"}
        )
        messages.append(
            {"role": "assistant", "content": f"Updated component {i} and verified the output"}
        )
    summary = create_shared_context(messages, max_summary_tokens=4)
    assert len(summary) <= 16


def test_intents_capped_at_three() -> None:
    messages = [
        {"role": "user", "content": f"Distinct long goal statement number {i} for the roadmap"}
        for i in range(5)
    ]
    summary = create_shared_context(messages, 100)
    assert summary.count("; ") <= 2


def test_missing_content_defaults_empty() -> None:
    summary = create_shared_context(
        [{"role": "user"}, {"role": "assistant"}],
        100,
    )
    assert summary == ""


def test_unknown_role_ignored() -> None:
    summary = create_shared_context(
        [{"role": "system", "content": "Fixed everything created today"}],
        100,
    )
    assert summary == ""


def test_results_joined_after_goals() -> None:
    summary = create_shared_context(
        [
            {"role": "user", "content": "Investigate the ssl renewal failure path"},
            {"role": "assistant", "content": "Fixed the cert reload step"},
        ],
        100,
    )
    assert "Goals:" in summary
    assert " | " in summary
    assert summary.index("Goals:") < summary.index("Results:")
