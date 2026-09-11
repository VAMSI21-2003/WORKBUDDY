"""Unit tests for buddy.llm."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from buddy.llm import ClaudeProvider, GroqProvider, LLMConfigurationError, LLMMessage


class _FakeTextBlock:
    """Stand-in for an ``anthropic`` text content block."""

    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _FakeUsage:
    """Stand-in for an ``anthropic`` response's token usage."""

    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeResponse:
    """Stand-in for an ``anthropic`` ``Message`` response."""

    def __init__(self, text: str) -> None:
        self.content: list[Any] = [_FakeTextBlock(text)]
        self.usage = _FakeUsage(input_tokens=12, output_tokens=34)


class _FakeMessages:
    """Stand-in for ``anthropic.Anthropic().messages``."""

    def __init__(self, response_text: str) -> None:
        self._response = _FakeResponse(response_text)
        self.last_kwargs: dict[str, Any] | None = None

    def create(self, **kwargs: Any) -> _FakeResponse:
        self.last_kwargs = kwargs
        return self._response


class _FakeAnthropicClient:
    """Stand-in for ``anthropic.Anthropic()`` — no real network calls."""

    def __init__(self, response_text: str = "") -> None:
        self.messages = _FakeMessages(response_text)


def _provider_with_fake_client(response_text: str = "") -> tuple[ClaudeProvider, _FakeAnthropicClient]:
    """Build a ``ClaudeProvider`` with its Anthropic client swapped for a fake."""
    provider = ClaudeProvider(api_key="sk-test-explicit")
    fake_client = _FakeAnthropicClient(response_text)
    provider._client = fake_client  # type: ignore[attr-defined]
    return provider, fake_client


def test_claude_provider_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(LLMConfigurationError):
        ClaudeProvider()


def test_claude_provider_reads_key_from_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-from-env")

    # Should not raise; constructing the client makes no network call.
    ClaudeProvider()


def test_claude_provider_accepts_explicit_key_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    # Should not raise even with no env var set, since a key is passed explicitly.
    ClaudeProvider(api_key="sk-test-explicit")


def test_complete_sends_system_and_messages_and_returns_text() -> None:
    provider, fake_client = _provider_with_fake_client(response_text="hello from claude")

    result = provider.complete(
        system="Classify intent.",
        messages=(LLMMessage(role="user", content="remove duplicate rows"),),
        max_tokens=256,
    )

    assert result == "hello from claude"
    assert fake_client.messages.last_kwargs is not None
    assert fake_client.messages.last_kwargs["system"] == "Classify intent."
    assert fake_client.messages.last_kwargs["max_tokens"] == 256
    assert fake_client.messages.last_kwargs["messages"] == [
        {"role": "user", "content": "remove duplicate rows"}
    ]


def test_complete_uses_default_max_tokens_when_not_specified() -> None:
    provider, fake_client = _provider_with_fake_client(response_text="ok")

    provider.complete(system="s", messages=(LLMMessage(role="user", content="u"),))

    assert fake_client.messages.last_kwargs is not None
    assert fake_client.messages.last_kwargs["max_tokens"] == 1024


def test_complete_concatenates_multiple_text_blocks() -> None:
    provider, fake_client = _provider_with_fake_client()
    fake_client.messages._response.content = [
        _FakeTextBlock("part one "),
        _FakeTextBlock("part two"),
    ]

    result = provider.complete(system="s", messages=(LLMMessage(role="user", content="u"),))

    assert result == "part one part two"


def test_complete_ignores_non_text_content_blocks() -> None:
    provider, fake_client = _provider_with_fake_client()
    non_text_block = _FakeTextBlock("should be ignored")
    non_text_block.type = "tool_use"
    fake_client.messages._response.content = [non_text_block, _FakeTextBlock("kept")]

    result = provider.complete(system="s", messages=(LLMMessage(role="user", content="u"),))

    assert result == "kept"


def test_api_key_is_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    provider, _ = _provider_with_fake_client(response_text="reply text")

    provider.complete(
        system="a sensitive system prompt",
        messages=(LLMMessage(role="user", content="sensitive cell data"),),
    )

    log_text = caplog.text
    assert "sk-test-explicit" not in log_text
    assert "sensitive cell data" not in log_text
    assert "a sensitive system prompt" not in log_text
    assert "reply text" not in log_text


# -- GroqProvider -----------------------------------------------------------------
# Mirrors the ClaudeProvider test structure above, mocking the groq client so
# no real network call is made by the automated suite.


class _FakeGroqMessage:
    """Stand-in for a ``groq`` chat completion's message."""

    def __init__(self, content: str | None) -> None:
        self.content = content


class _FakeGroqChoice:
    """Stand-in for a ``groq`` chat completion's choice."""

    def __init__(self, content: str | None) -> None:
        self.message = _FakeGroqMessage(content)


class _FakeGroqUsage:
    """Stand-in for a ``groq`` chat completion's token usage."""

    def __init__(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class _FakeGroqResponse:
    """Stand-in for a ``groq`` ``ChatCompletion`` response."""

    def __init__(self, text: str | None) -> None:
        self.choices: list[Any] = [_FakeGroqChoice(text)]
        self.usage = _FakeGroqUsage(prompt_tokens=12, completion_tokens=34)


class _FakeGroqCompletions:
    """Stand-in for ``groq.Groq().chat.completions``."""

    def __init__(self, response_text: str | None) -> None:
        self._response = _FakeGroqResponse(response_text)
        self.last_kwargs: dict[str, Any] | None = None

    def create(self, **kwargs: Any) -> _FakeGroqResponse:
        self.last_kwargs = kwargs
        return self._response


class _FakeGroqChat:
    """Stand-in for ``groq.Groq().chat``."""

    def __init__(self, response_text: str | None) -> None:
        self.completions = _FakeGroqCompletions(response_text)


class _FakeGroqClient:
    """Stand-in for ``groq.Groq()`` — no real network calls."""

    def __init__(self, response_text: str | None = "") -> None:
        self.chat = _FakeGroqChat(response_text)


def _groq_provider_with_fake_client(
    response_text: str | None = "",
) -> tuple[GroqProvider, _FakeGroqClient]:
    """Build a ``GroqProvider`` with its Groq client swapped for a fake."""
    provider = GroqProvider(api_key="gsk-test-explicit")
    fake_client = _FakeGroqClient(response_text)
    provider._client = fake_client  # type: ignore[attr-defined]
    return provider, fake_client


def test_groq_provider_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    with pytest.raises(LLMConfigurationError):
        GroqProvider()


def test_groq_provider_reads_key_from_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test-from-env")

    # Should not raise; constructing the client makes no network call.
    GroqProvider()


def test_groq_provider_accepts_explicit_key_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    # Should not raise even with no env var set, since a key is passed explicitly.
    GroqProvider(api_key="gsk-test-explicit")


def test_groq_provider_defaults_to_llama_3_3_70b_versatile() -> None:
    provider, fake_client = _groq_provider_with_fake_client(response_text="ok")

    provider.complete(system="s", messages=(LLMMessage(role="user", content="u"),))

    assert fake_client.chat.completions.last_kwargs is not None
    assert fake_client.chat.completions.last_kwargs["model"] == "llama-3.3-70b-versatile"


def test_groq_complete_sends_system_as_first_message_and_returns_text() -> None:
    provider, fake_client = _groq_provider_with_fake_client(response_text="hello from groq")

    result = provider.complete(
        system="Classify intent.",
        messages=(LLMMessage(role="user", content="remove duplicate rows"),),
        max_tokens=256,
    )

    assert result == "hello from groq"
    assert fake_client.chat.completions.last_kwargs is not None
    assert fake_client.chat.completions.last_kwargs["max_tokens"] == 256
    assert fake_client.chat.completions.last_kwargs["messages"] == [
        {"role": "system", "content": "Classify intent."},
        {"role": "user", "content": "remove duplicate rows"},
    ]


def test_groq_complete_uses_default_max_tokens_when_not_specified() -> None:
    provider, fake_client = _groq_provider_with_fake_client(response_text="ok")

    provider.complete(system="s", messages=(LLMMessage(role="user", content="u"),))

    assert fake_client.chat.completions.last_kwargs is not None
    assert fake_client.chat.completions.last_kwargs["max_tokens"] == 1024


def test_groq_complete_returns_empty_string_for_none_content() -> None:
    provider, fake_client = _groq_provider_with_fake_client(response_text=None)

    result = provider.complete(system="s", messages=(LLMMessage(role="user", content="u"),))

    assert result == ""


def test_groq_api_key_is_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    provider, _ = _groq_provider_with_fake_client(response_text="reply text")

    provider.complete(
        system="a sensitive system prompt",
        messages=(LLMMessage(role="user", content="sensitive cell data"),),
    )

    log_text = caplog.text
    assert "gsk-test-explicit" not in log_text
    assert "sensitive cell data" not in log_text
    assert "a sensitive system prompt" not in log_text
    assert "reply text" not in log_text


def test_groq_provider_satisfies_the_llmprovider_contract_used_by_the_other_stages() -> None:
    """``ClaudeInterpreter``/``ClaudeThinker``/``PersonalityVoice`` only
    depend on ``LLMProvider.complete``'s signature and return type — not
    on anything Claude-specific — so a ``GroqProvider`` instance must be
    usable in their place via dependency injection, with no code changes.
    This is a structural check that the contract is satisfied, not a
    full integration test of those stages.
    """
    provider, _ = _groq_provider_with_fake_client(response_text="a reply")

    reply = provider.complete(
        system="a system prompt",
        messages=(LLMMessage(role="user", content="a user message"),),
    )

    assert isinstance(reply, str)
