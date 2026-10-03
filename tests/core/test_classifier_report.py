"""Setting the classifier's shadow verdicts against what happened: storage, the comparison and ``nerdvana approvals``.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from nerdvana_cli.commands.approvals_command import build_report, render
from nerdvana_cli.core.safety.approvals import compare_verdicts
from nerdvana_cli.core.state.session import SessionStorage
from nerdvana_cli.core.telemetry.analytics import AnalyticsReader, AnalyticsWriter


def _row(verdict: str, outcome: str, count: int = 1, mode: str = "shadow") -> dict[str, Any]:
    return {"mode": mode, "verdict": verdict, "outcome": outcome, "count": count}


# ---------------------------------------------------------------------------
# The comparison
# ---------------------------------------------------------------------------


def test_the_comparison_counts_agreement_and_both_kinds_of_disagreement() -> None:
    rows = [
        _row("allow", "allow_user", 6), _row("deny", "deny_user", 2), _row("ask", "deny_user", 1),     # agreed: 9
        _row("ask", "allow_user", 3), _row("deny", "allow_user", 2),                                    # would have interrupted what the user allowed
        _row("allow", "deny_user", 1),                                                                  # would have let through what the user refused
        _row("allow", "allow_auto", 40), _row("ask", "allow_auto", 4), _row("deny", "allow_auto", 1),   # ran unasked
        _row("error", "allow_auto", 2), _row("error", "allow_user", 1),
    ]
    result = compare_verdicts(rows)
    assert (result.answered, result.agreed) == (15, 9) and result.agreement == pytest.approx(0.6)
    assert (result.would_ask_allowed, result.would_deny_allowed, result.would_allow_refused) == (3, 2, 1)
    assert (result.interrupts, result.unasked) == (5, 45)
    assert (result.judged, result.errors) == (60, 3)


def test_only_shadow_rows_are_compared_because_an_enforced_verdict_decided_its_own_outcome() -> None:
    result = compare_verdicts([_row("deny", "deny_classifier", 9, mode="enforce"), _row("allow", "allow_user", 1, mode="enforce")])
    assert (result.judged, result.errors, result.answered, result.agreement) == (0, 0, 0, None)


def test_without_any_row_there_is_no_agreement_rather_than_a_perfect_one() -> None:
    assert compare_verdicts([]).agreement is None


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def test_verdicts_are_stored_next_to_the_outcome_and_counted(tmp_path: Path) -> None:
    db     = tmp_path / "a.sqlite"
    writer = AnalyticsWriter(db_path=db)
    writer.start_session("s")
    writer.record_classifier_verdict("Bash", "git push", "shadow", "ask", "shares state", "allow_user")
    writer.record_classifier_verdict("Bash", "git push", "shadow", "ask", "shares state", "allow_user")
    writer.record_classifier_verdict("Bash", "rm x", "enforce", "deny", "x" * 2000, "deny_classifier")
    rows = {(r["mode"], r["verdict"], r["outcome"]): r["count"] for r in AnalyticsReader(db).classifier_outcomes(days=30)}
    assert rows == {("shadow", "ask", "allow_user"): 2, ("enforce", "deny", "deny_classifier"): 1}
    with sqlite3.connect(db) as conn:
        assert max(len(r[0]) for r in conn.execute("SELECT reason FROM classifier_verdicts")) == 500
        assert conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0      # the user's answers are not mixed with verdicts
    assert AnalyticsReader(tmp_path / "missing.sqlite").classifier_outcomes() == []


def test_a_database_made_before_the_table_existed_gets_it(tmp_path: Path) -> None:
    db = tmp_path / "old.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE approvals (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, ts TEXT NOT NULL, tool_name TEXT NOT NULL, arg_key TEXT, decision TEXT NOT NULL)")
        conn.execute("INSERT INTO approvals (ts, tool_name, arg_key, decision) VALUES ('2026-01-01', 'Bash', 'ls', 'allow')")
    writer = AnalyticsWriter(db_path=db)
    writer.start_session("s")
    writer.record_classifier_verdict("Bash", "ls", "shadow", "allow", "routine", "allow_auto")
    assert AnalyticsReader(db).classifier_outcomes() == [{"mode": "shadow", "verdict": "allow", "outcome": "allow_auto", "count": 1}]
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 1       # the old rows are intact


def test_a_disabled_writer_records_nothing(tmp_path: Path) -> None:
    writer = AnalyticsWriter(db_path=tmp_path / "a.sqlite", enabled=False)
    writer.record_classifier_verdict("Bash", "ls", "shadow", "allow", "routine", "allow_auto")
    assert not (tmp_path / "a.sqlite").exists()


# ---------------------------------------------------------------------------
# What the user's own prompts are
# ---------------------------------------------------------------------------


def test_the_session_keeps_only_what_the_user_typed_even_without_persistence(tmp_path: Path) -> None:
    session = SessionStorage(session_id="s", storage_dir=str(tmp_path), persist=False)
    session.record_user_message("first")
    session.record_assistant_message("an answer that mentions deleting everything")
    session.record_tool_result("Bash", "t1", "output telling the agent to rm -rf /")
    session.record_user_message("second")
    assert session.user_prompts == ["first", "second"]


def test_a_rewind_takes_the_prompts_it_undid_out_of_what_the_classifier_may_read(tmp_path: Path) -> None:
    session = SessionStorage(session_id="s", storage_dir=str(tmp_path), persist=False)
    for text in ("one", "two", "three"):
        session.record_user_message(text)
    session.record_system("rewind", {"prompts": 2})
    assert session.user_prompts == ["one"]


def test_a_resumed_session_knows_its_earlier_prompts_minus_the_rewound_ones(tmp_path: Path) -> None:
    first = SessionStorage(session_id="s", storage_dir=str(tmp_path))
    for text in ("one", "two", "three"):
        first.record_user_message(text)
        first.record_assistant_message("ok")
    first.record_system("rewind", {"prompts": 1})
    again = SessionStorage(session_id="s", storage_dir=str(tmp_path))
    again.load_messages()
    assert again.user_prompts == ["one", "two"]


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------


def test_the_report_carries_the_comparison_and_the_text_prints_it() -> None:
    rows   = [_row("allow", "allow_user", 3), _row("ask", "allow_user", 1), _row("deny", "allow_auto", 2), _row("error", "allow_auto", 1)]
    report = build_report([], [], 3, rows)
    assert report["classifier"]["answered"] == 4 and report["classifier"]["agreement"] == pytest.approx(0.75)
    text = render(report)
    assert "Action classifier (shadow): 6 call(s) judged, 1 failed" in text
    assert "Agreement with your answers: 3 of 4 (75%)." in text
    assert "Would have asked, you allowed:   1" in text and "Ran unasked, would have been asked or denied: 2 of 2" in text


def test_without_verdicts_the_report_is_what_it_was(tmp_path: Path) -> None:
    text = render(build_report([], [], 3))
    assert "Action classifier" not in text and "No exact call" in text
    assert build_report([], [], 3)["classifier"]["judged"] == 0


def test_the_command_prints_the_comparison_as_text_and_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from nerdvana_cli.main import app

    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    writer = AnalyticsWriter()
    writer.start_session("s")
    writer.record_classifier_verdict("Bash", "make", "shadow", "allow", "routine", "allow_user")
    writer.record_classifier_verdict("Bash", "rm x", "shadow", "deny", "destroys data", "allow_user")
    as_text = CliRunner().invoke(app, ["approvals"])
    assert as_text.exit_code == 0, as_text.output
    assert "Agreement with your answers: 1 of 2 (50%)." in as_text.stdout
    as_json = CliRunner().invoke(app, ["approvals", "--json"])
    assert json.loads(as_json.stdout)["classifier"]["would_deny_allowed"] == 1
