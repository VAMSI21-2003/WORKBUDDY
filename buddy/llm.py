"""LLM — the minimal interface Buddy's stages use to call a language model."""

from __future__ import annotations

import logging
import os
import random
import time
from dataclasses import dataclass
from typing import Protocol

import anthropic
import groq
import httpx

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-5"
# Groq periodically retires models on a schedule of its own — this exact
# default has already rotted once (llama-3.3-70b-versatile was retired
# 2026-08-16). Check https://console.groq.com/docs/deprecations before
# trusting this value; it's whatever Groq recommended as of 2026-09.
DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"

# Seconds one HTTP request to a provider may take before it's abandoned.
# Set explicitly rather than inherited from each SDK's default, which
# differs (``anthropic`` 1.11: 600 s read; ``groq`` 1.7: 60 s) and can
# change with an upgrade. The SDKs retry a timed-out request (twice by
# default), so one ``complete`` call can take up to about three times this.
# A timeout raises from ``complete``. Brain, Think and ``ClaudePlanner``
# already catch a failed call and degrade honestly (no intent, a
# clarification, no plan); any other caller's stage fails (R6).
DEFAULT_REQUEST_TIMEOUT_S = 60.0

# How long a stage waits before asking the model a second time, and how far that wait may drift
# either way (a fraction of it), so that many turns failing together don't all retry together.
RETRY_PAUSE_S = 1.0
RETRY_JITTER = 0.25
_TIMEOUT_ERRORS = (
    TimeoutError,
    httpx.TimeoutException,
    anthropic.APITimeoutError,
    groq.APITimeoutError,
)


def is_transient_failure(exc: BaseException) -> bool:
    """Whether a failed model call is worth asking again: a ``429``, a timeout or a ``5xx``.

    Everything else (a ``400``, ``401``, ``403`` or ``404``, a malformed request, a missing
    package) will fail the same way a second time, so a retry only spends the provider's
    patience and the turn's deadline. Read from the exception's type and its HTTP status, never
    from its message.
    """
    if isinstance(exc, _TIMEOUT_ERRORS):
        return True
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and (status == 429 or 500 <= status <= 599)


def retry_pause_seconds(draw: float | None = None) -> float:
    """The wait before a retry: ``RETRY_PAUSE_S``, give or take ``RETRY_JITTER`` of it.

    Args:
        draw: A number in ``[0, 1)`` to place the wait by; random when not given (tests pass one).
    """
    if draw is None:
        draw = random.random()
    return RETRY_PAUSE_S * (1 + RETRY_JITTER * (2 * draw - 1))


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

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        timeout: float = DEFAULT_REQUEST_TIMEOUT_S,
    ) -> None:
        """Initialize the provider.

        Args:
            api_key: The Anthropic API key to use. Defaults to the
                ``ANTHROPIC_API_KEY`` environment variable. Only pass this
                explicitly in tests; production code should rely on the
                environment variable.
            model: The Claude model to call.
            timeout: Seconds one request may take before it's abandoned.

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
        self._client = anthropic.Anthropic(api_key=resolved_key, timeout=timeout)
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

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_GROQ_MODEL,
        timeout: float = DEFAULT_REQUEST_TIMEOUT_S,
    ) -> None:
        """Initialize the provider.

        Args:
            api_key: The Groq API key to use. Defaults to the
                ``GROQ_API_KEY`` environment variable. Only pass this
                explicitly in tests; production code should rely on the
                environment variable.
            model: The Groq model to call.
            timeout: Seconds one request may take before it's abandoned.

        Raises:
            LLMConfigurationError: If no API key is available from either
                ``api_key`` or the ``GROQ_API_KEY`` environment variable.
        """
        resolved_key = api_key if api_key is not None else os.environ.get("GROQ_API_KEY")
        if not resolved_key:
            raise LLMConfigurationError("GROQ_API_KEY is not set and no api_key was provided.")
        self._client = groq.Groq(api_key=resolved_key, timeout=timeout)
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
