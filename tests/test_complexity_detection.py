"""Tests for planning gate complexity detection."""
from nerdvana_cli.core.agent_loop import _needs_planning


def test_single_signal_not_complex() -> None:
    assert _needs_planning("리팩터링 해줘") is False


def test_two_signals_triggers_planning() -> None:
    assert _needs_planning("전체 아키텍처 리팩터링 계획 세워줘") is True


def test_refactor_and_module_count() -> None:
    assert _needs_planning("5개 파일 refactoring 해줘") is True


def test_migration_and_architecture() -> None:
    assert _needs_planning("아키텍처 마이그레이션 계획") is True


def test_plain_task_not_complex() -> None:
    assert _needs_planning("이 함수 이름 바꿔줘") is False


def test_scratch_and_service() -> None:
    assert _needs_planning("처음부터 새로운 서비스 만들어줘") is True


def test_case_insensitive() -> None:
    assert _needs_planning("Refactor the entire Architecture from scratch") is True
