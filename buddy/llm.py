"""LLM — the minimal interface Buddy's stages use to call a language model."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Protocol

import anthropic
import groq

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"


@dataclass(frozen=True)
class LLMMessage:
    """A single message in a conversation sent to a language model.

    Attributes:
        role: Who is speaking this turn — ``"user"`` or ``"assistant"``.
        content: The message text.
    """

    role: str
    content: str


class LLMConfigurationError(Exception):
    """Raised when an ``LLMProvider`` cannot be constructed or configured."""


class LLMProvider(Protocol):
    """The minimal interface Buddy's stages use to call a language model.

    Deliberately narrow: a single request/response text completion. This is
    all Brain and Think need today (see ``buddy.brain.ClaudeInterpreter``
    and ``buddy.think.ClaudeThinker``) — send a system prompt and a
    conversation of messages, get the model's raw text reply back, and
    parse it themselves. Streaming, tool use, and multi-provider
    abstractions are not built here; they can be added to this protocol,
    or a successor, when a stage actually needs them.
    """

    def complete(
        self, *, system: str, messages: tuple[LLMMessage, ...], max_tokens: int = 1024
    ) -> str:
        """Send a completion request and return the model's raw text reply.

        Args:
            system: The system prompt describing the task.
            messages: The conversation to send, oldest first.
            max_tokens: The maximum number of tokens the model may generate.

        Returns:
            The model's raw text reply, unparsed.
        """
        ...


class ClaudeProvider:
    """An ``LLMProvider`` backed by the Anthropic Claude API.

    Reads its API key exclusively from the ``ANTHROPIC_API_KEY`` environment
    variable unless one is explicitly supplied. The ``api_key`` constructor
    argument exists only so tests can inject a dummy value without touching
    the environment — production code should rely on the environment
    variable. The key itself is never logged. Only the model name, elapsed
    time, and token usage counts are logged; prompt and response text are
    never logged, since spreadsheet content and other user data may pass
    through them.
    """

    def __init__(self, *, api_key: str | None = None, model: str = DEFAULT_MODEL) -> None:
        """Initialize the provider.

        Args:
            api_key: The Anthropic API key to use. Defaults to the
                ``ANTHROPIC_API_KEY`` environment variable. Only pass this
                explicitly in tests; production code should rely on the
                environment variable.
            model: The Claude model to call.

        Raises:
            LLMConfigurationError: If no API key is available from either
                ``api_key`` or the ``ANTHROPIC_API_KEY`` environment
                variable.
        """
        resolved_key = api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY")
        if not resolved_key:
            raise LLMConfigurationError(
                "ANTHROPIC_API_KEY is not set and no api_key was provided."
            )
        self._client = anthropic.Anthropic(api_key=resolved_key)
        self._model = model

    def complete(
        self, *, system: str, messages: tuple[LLMMessage, ...], max_tokens: int = 1024
    ) -> str:
        """Send ``messages`` to Claude and return its raw text reply.

        Args:
            system: The system prompt describing the task.
            messages: The conversation to send, oldest first.
            max_tokens: The maximum number of tokens Claude may generate.

        Returns:
            The concatenated text of Claude's reply.
        """
        started = time.monotonic()
        response = self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=system,
            messages=[
                {"role": message.role, "content": message.content} for message in messages
            ],
        )
        elapsed = time.monotonic() - started
        logger.debug(
            "Claude completion: model=%r elapsed=%.2fs input_tokens=%s output_tokens=%s",
            self._model,
            elapsed,
            getattr(response.usage, "input_tokens", None),
            getattr(response.usage, "output_tokens", None),
        )
        return "".join(block.text for block in response.content if block.type == "text")


class GroqProvider:
    """An ``LLMProvider`` backed by the Groq API.

    Same pattern as ``ClaudeProvider``, wrapping the official ``groq``
    package instead of ``anthropic``. Reads its API key exclusively from
    the ``GROQ_API_KEY`` environment variable unless one is explicitly
    supplied. The ``api_key`` constructor argument exists only so tests
    can inject a dummy value without touching the environment —
    production code should rely on the environment variable. The key
    itself is never logged. Only the model name, elapsed time, and token
    usage counts are logged; prompt and response text are never logged,
    since spreadsheet content and other user data may pass through them.
    """

    def __init__(self, *, api_key: str | None = None, model: str = DEFAULT_GROQ_MODEL) -> None:
        """Initialize the provider.

        Args:
            api_key: The Groq API key to use. Defaults to the
                ``GROQ_API_KEY`` environment variable. Only pass this
                explicitly in tests; production code should rely on the
                environment variable.
            model: The Groq model to call.

        Raises:
            LLMConfigurationError: If no API key is available from either
                ``api_key`` or the ``GROQ_API_KEY`` environment variable.
        """
        resolved_key = api_key if api_key is not None else os.environ.get("GROQ_API_KEY")
        if not resolved_key:
            raise LLMConfigurationError("GROQ_API_KEY is not set and no api_key was provided.")
        self._client = groq.Groq(api_key=resolved_key)
        self._model = model

    def complete(
        self, *, system: str, messages: tuple[LLMMessage, ...], max_tokens: int = 1024
    ) -> str:
        """Send ``messages`` to Groq and return its raw text reply.

        Groq's chat completions API is OpenAI-compatible: unlike
        Anthropic's Messages API, it has no separate top-level ``system``
        parameter — the system prompt is sent as the first message, with
        role ``"system"``.

        Args:
            system: The system prompt describing the task.
            messages: The conversation to send, oldest first.
            max_tokens: The maximum number of tokens Groq may generate.

        Returns:
            The model's raw text reply.
        """
        started = time.monotonic()
        chat_messages = [{"role": "system", "content": system}] + [
            {"role": message.role, "content": message.content} for message in messages
        ]
        response = self._client.chat.completions.create(
            model=self._model,
            max_tokens=max_tokens,
            messages=chat_messages,
        )
        elapsed = time.monotonic() - started
        usage = getattr(response, "usage", None)
        logger.debug(
            "Groq completion: model=%r elapsed=%.2fs input_tokens=%s output_tokens=%s",
            self._model,
            elapsed,
            getattr(usage, "prompt_tokens", None),
            getattr(usage, "completion_tokens", None),
        )
        return response.choices[0].message.content or ""
