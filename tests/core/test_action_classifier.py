"""The action classifier: what it is shown, its two stages, how its answer is read, and that a failure is never an allow.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.safety.classifier import (
    ALLOW,
    ASK,
    DENY,
    ENFORCE,
    FILTER_TOKENS,
    SHADOW,
    VERDICT_TOKENS,
    ActionClassifier,
    ClassifierFeed,
    Completion,
    build_request,
    parse_filter,
    parse_verdict,
    provider_completion,
)


class _Fake:
    """A completion function that answers from a script and remembers what it was asked."""

    def __init__(self, *answers: str | Exception) -> None:
        self.answers = list(answers)
        self.calls:  list[tuple[str, str, int]] = []

    async def __call__(self, system: str, user: str, max_tokens: int) -> Completion:
        self.calls.append((system, user, max_tokens))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return Completion(answer, {"input_tokens": 100, "output_tokens": 5}, "acme", "small")


class _Books:
    """What a feed charges and says, in memory."""

    def __init__(self, prompts: list[str] | None = None, over: bool = False) -> None:
        self.prompts = prompts if prompts is not None else ["please run the tests"]
        self.over    = over
        self.charged: list[Completion] = []

    def feed(self) -> ClassifierFeed:
        return ClassifierFeed(lambda: self.prompts, self._charge, lambda: self.over)

    def _charge(self, answer: Completion) -> float:
        self.charged.append(answer)
        return 0.0


async def _classify(fake: Any, books: _Books | None = None, mode: str = SHADOW, timeout: float = 5.0) -> Any:
    books = books or _Books()
    return await ActionClassifier(mode, fake, timeout).classify("Bash", {"command": "pytest -q"}, "/proj", books.feed())


# ---------------------------------------------------------------------------
# What the classifier is shown
# ---------------------------------------------------------------------------


def test_the_request_holds_the_directory_the_users_words_and_the_call_only() -> None:
    request = build_request("Bash", {"command": "rm -rf build"}, "/proj", ["first ask", "second ask"])
    assert "/proj" in request and "rm -rf build" in request and '"tool": "Bash"' in request
    assert request.index("first ask") < request.index("second ask")


def test_long_history_keeps_the_newest_messages_and_long_calls_say_what_is_missing() -> None:
    prompts = [f"message {n} " + "x" * 1400 for n in range(20)]
    request = build_request("Bash", {"command": "y" * 20_000}, "/proj", prompts)
    assert "message 19 " in request and "message 0 " not in request
    assert "characters not shown" in request
    assert len(request) < 12_000


def test_no_user_messages_is_said_plainly() -> None:
    assert "(none)" in build_request("Bash", {}, "/proj", [])


# ---------------------------------------------------------------------------
# Reading the answers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("text", "ok"), [
    ("ok", True), ("OK", True), (" ok.\n", True), ('"ok"', True),
    ("check", False), ("Check", False), ("", False), ("okay then", False), ("sure", False), ("1", False),
])
def test_only_a_plain_ok_passes_the_pre_filter(text: str, ok: bool) -> None:
    assert parse_filter(text) is ok


@pytest.mark.parametrize("text", [
    '{"verdict": "deny", "reason": "wipes data"}',
    '```json\n{"verdict": "deny", "reason": "wipes data"}\n```',
    'My answer:\n{"verdict": "DENY", "reason": "wipes data"} Thanks.',
    "{'verdict': 'deny', 'reason': 'wipes data'}",
    'verdict: deny',
    "deny",
    ' "Deny". ',
])
def test_a_verdict_is_read_through_wrapping_and_quoting(text: str) -> None:
    result = parse_verdict(text)
    assert (result.verdict, result.stage, result.error) == (DENY, 2, False)


def test_the_reason_is_kept_and_bounded() -> None:
    assert parse_verdict('{"verdict": "ask", "reason": "outside the project"}').reason == "outside the project"
    assert len(parse_verdict(json.dumps({"verdict": "ask", "reason": "r" * 2000})).reason) == 300


def test_an_answer_that_names_several_verdicts_takes_the_strictest() -> None:
    text = 'first "verdict": "allow" then on reflection "verdict": "ask" and finally "verdict": "allow"'
    assert parse_verdict(text).verdict == ASK


@pytest.mark.parametrize("text", [
    "", "I think this is fine.", "maybe", '{"verdict": "maybe"}', '{"decision": "allow"}', "{", '{"verdict": ["allow"]}', "allow or deny",
])
def test_an_answer_that_cannot_be_read_is_an_ask_and_flagged(text: str) -> None:
    result = parse_verdict(text)
    assert (result.verdict, result.error, result.stage) == (ASK, True, 0)


# ---------------------------------------------------------------------------
# The two stages
# ---------------------------------------------------------------------------


async def test_ok_from_the_pre_filter_allows_after_one_cheap_request() -> None:
    fake   = _Fake("ok")
    result = await _classify(fake)
    assert (result.verdict, result.stage, result.error) == (ALLOW, 1, False)
    assert [call[2] for call in fake.calls] == [FILTER_TOKENS]


async def test_check_goes_on_to_the_full_request_and_its_verdict_decides() -> None:
    fake   = _Fake("check", '{"verdict": "deny", "reason": "reads credentials"}')
    result = await _classify(fake)
    assert (result.verdict, result.reason, result.stage) == (DENY, "reads credentials", 2)
    assert [call[2] for call in fake.calls] == [FILTER_TOKENS, VERDICT_TOKENS]
    assert fake.calls[0][1] == fake.calls[1][1]            # both stages see the same request
    assert "JSON" in fake.calls[1][0] and "JSON" not in fake.calls[0][0]


async def test_a_pre_filter_answer_that_is_neither_word_is_checked_not_trusted() -> None:
    fake   = _Fake("hmm, hard to say", '{"verdict": "allow", "reason": "routine"}')
    result = await _classify(fake)
    assert (result.verdict, result.stage) == (ALLOW, 2)
    assert len(fake.calls) == 2


async def test_the_full_stage_can_allow_ask_or_deny() -> None:
    for verdict in (ALLOW, ASK, DENY):
        result = await _classify(_Fake("check", json.dumps({"verdict": verdict, "reason": "r"})))
        assert result.verdict == verdict and not result.error


# ---------------------------------------------------------------------------
# A failed classifier never allows
# ---------------------------------------------------------------------------


async def test_every_kind_of_failure_is_an_ask_flagged_as_an_error() -> None:
    cases = {
        "request error":  _Fake(RuntimeError("boom")),
        "second error":   _Fake("check", ConnectionError("down")),
        "unreadable":     _Fake("check", "I would rather not say"),
        "empty":          _Fake("", ""),
        "no model":       None,
    }
    for name, fake in cases.items():
        result = await _classify(fake)
        assert (result.verdict, result.error) == (ASK, True), name


async def test_a_timeout_is_an_ask() -> None:
    async def slow(system: str, user: str, max_tokens: int) -> Completion:
        await asyncio.sleep(5)
        return Completion("ok", {}, "acme", "small")

    result = await _classify(slow, timeout=0.05)
    assert (result.verdict, result.error) == (ASK, True)


async def test_a_session_without_a_feed_or_over_its_cost_limit_is_an_ask_and_makes_no_request() -> None:
    fake = _Fake("ok")
    assert (await ActionClassifier(SHADOW, fake).classify("Bash", {}, "/p", None)).error
    over = await _classify(fake, _Books(over=True))
    assert (over.verdict, over.error) == (ASK, True) and "cost limit" in over.reason
    assert fake.calls == []


async def test_a_failure_to_book_the_cost_does_not_change_the_verdict() -> None:
    books = _Books()
    feed  = ClassifierFeed(lambda: [], lambda answer: 1 / 0, lambda: False)
    result = await ActionClassifier(ENFORCE, _Fake("ok")).classify("Bash", {}, "/p", feed)
    assert result.verdict == ALLOW and books.charged == []


async def test_every_request_is_charged_with_its_usage_and_model() -> None:
    books = _Books()
    await _classify(_Fake("check", '{"verdict": "ask", "reason": "r"}'), books)
    assert [(c.provider, c.model, c.usage["input_tokens"]) for c in books.charged] == [("acme", "small", 100)] * 2


# ---------------------------------------------------------------------------
# Settings and the model it runs on
# ---------------------------------------------------------------------------


def test_the_classifier_exists_only_when_the_setting_turns_it_on() -> None:
    settings = NerdvanaSettings()
    assert settings.permissions.classifier == "off" and ActionClassifier.from_settings(settings) is None
    assert ActionClassifier.from_settings(object()) is None
    settings.permissions.classifier = "shadow"
    assert ActionClassifier.from_settings(settings) is not None


def test_a_bare_off_in_yaml_means_off_and_a_typo_stops_the_start(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from nerdvana_cli.core.config.settings import SettingsLoadError

    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    config = tmp_path / "c.yml"
    config.write_text("permissions:\n  classifier: off\n", encoding="utf-8")
    assert NerdvanaSettings.load(str(config)).permissions.classifier == "off"
    config.write_text("permissions:\n  classifier: shadow\n  classifier_model: acme:small\n", encoding="utf-8")
    loaded = NerdvanaSettings.load(str(config)).permissions
    assert (loaded.classifier, loaded.classifier_model) == ("shadow", "acme:small")
    config.write_text("permissions:\n  classifier: enforcing\n", encoding="utf-8")
    with pytest.raises(SettingsLoadError):
        NerdvanaSettings.load(str(config))


def test_the_model_comes_from_the_setting_then_the_categories_then_the_session(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[dict[str, Any]] = []

    class _Provider:
        def __init__(self, **kwargs: Any) -> None:
            created.append(kwargs)

        async def send(self, system: str, messages: Any, tools: Any) -> dict[str, Any]:
            return {"content": "ok", "usage": {"input_tokens": 3}}

    monkeypatch.setattr("nerdvana_cli.core.safety.classifier.create_provider", lambda **kwargs: _Provider(**kwargs))
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    settings                = NerdvanaSettings()
    settings.model.provider = "anthropic"
    settings.model.model    = "session-model"
    settings.model.api_key  = "k"

    async def model_used() -> str:
        completion = provider_completion(settings)
        assert completion is not None
        answer = await completion("s", "u", FILTER_TOKENS)
        assert (answer.text, answer.usage) == ("ok", {"input_tokens": 3})
        return f"{answer.provider}:{answer.model}"

    assert asyncio.run(model_used()) == "anthropic:session-model"
    settings.agents.categories = {"quick": "quick-model"}
    assert asyncio.run(model_used()) == "anthropic:quick-model"
    settings.agents.categories = {"quick": "quick-model", "classifier": "openai:judge"}
    assert asyncio.run(model_used()) == "openai:judge"
    settings.permissions.classifier_model = "explicit-model"
    assert asyncio.run(model_used()) == "anthropic:explicit-model"
    assert created[-1]["max_tokens"] == FILTER_TOKENS and created[-1]["temperature"] == 0.0


def test_a_model_without_a_credential_leaves_no_completion_so_the_classifier_asks(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("OPENAI_API_KEY",):
        monkeypatch.delenv(name, raising=False)
    settings = NerdvanaSettings()
    settings.permissions.classifier_model = "openai:judge"
    assert provider_completion(settings) is None


async def test_a_provider_that_reports_an_error_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Broken:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def send(self, system: str, messages: Any, tools: Any) -> dict[str, Any]:
            return {"content": "rate limited", "is_error": True}

    monkeypatch.setattr("nerdvana_cli.core.safety.classifier.create_provider", lambda **kwargs: _Broken(**kwargs))
    settings = NerdvanaSettings()
    completion = provider_completion(settings)
    assert completion is not None
    result = await ActionClassifier(SHADOW, completion).classify("Bash", {}, "/p", _Books().feed())
    assert (result.verdict, result.error) == (ASK, True)
