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
        max_retries: int | None = None,
    ) -> None:
        """Initialize the provider.

        Args:
            api_key: The Groq API key to use. Defaults to the
                ``GROQ_API_KEY`` environment variable. Only pass this
                explicitly in tests; production code should rely on the
                environment variable.
            model: The Groq model to call.
            timeout: Seconds one request may take before it's abandoned.
            max_retries: How often the SDK retries a failed request itself; ``None`` keeps the
                SDK's default. The reply writer passes 0: it has its own fallback and budget.

        Raises:
            LLMConfigurationError: If no API key is available from either
                ``api_key`` or the ``GROQ_API_KEY`` environment variable.
        """
        resolved_key = api_key if api_key is not None else os.environ.get("GROQ_API_KEY")
        if not resolved_key:
            raise LLMConfigurationError("GROQ_API_KEY is not set and no api_key was provided.")
        retries = {} if max_retries is None else {"max_retries": max_retries}
        self._client = groq.Groq(api_key=resolved_key, timeout=timeout, **retries)
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


DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
# Where the Gemini model name can be changed without a release: the variable's name only.
GEMINI_MODEL_ENV = "GEMINI_MODEL"
_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# Tokens the model may spend thinking before it writes, on top of ``max_tokens``. Gemini 2.5
# counts its thinking against the output limit, so without this a short limit can leave nothing
# for the answer.
GEMINI_THINKING_BUDGET = 512
# Only clearly harmful content is blocked: a person telling Buddy about a bad day, or about
# something worse, must still get an answer (the character's distress guidance).
_GEMINI_SAFETY = [
    {"category": category, "threshold": "BLOCK_ONLY_HIGH"}
    for category in (
        "HARM_CATEGORY_HARASSMENT",
        "HARM_CATEGORY_HATE_SPEECH",
        "HARM_CATEGORY_SEXUALLY_EXPLICIT",
        "HARM_CATEGORY_DANGEROUS_CONTENT",
    )
]


class ProviderHTTPError(Exception):
    """A provider answered with an HTTP error. Carries the status only, never the body."""

    def __init__(self, provider: str, status_code: int) -> None:
        super().__init__(f"{provider} answered HTTP {status_code}")
        self.status_code = status_code


class ProviderRefusedError(Exception):
    """A provider answered but gave no text (a safety block, or an empty candidate list).

    ``FallbackProvider`` asks the other provider then: the request itself was fine.
    """


class GeminiProvider:
    """An ``LLMProvider`` backed by Google's Gemini API, over plain HTTPS (``httpx``).

    Reads its API key exclusively from the ``GEMINI_API_KEY`` environment variable unless one is
    explicitly supplied (tests only). The key is sent in the ``x-goog-api-key`` header, never in
    the URL, so no request log can show it; it is never logged. Only the model name, elapsed
    time and token counts are logged, never prompt or reply text.

    An HTTP error raises ``ProviderHTTPError`` with the status (so ``is_transient_failure``
    reads a ``429`` or ``5xx`` the same way as for the SDK providers); a timeout raises
    ``httpx.TimeoutException``; a reply with no text raises ``ProviderRefusedError``. There are no
    retries here: ``FallbackProvider`` and the stages decide what happens next.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        """Initialize the provider.

        Args:
            api_key: Defaults to the ``GEMINI_API_KEY`` environment variable. Tests only.
            model: Defaults to ``GEMINI_MODEL`` from the environment, else
                ``DEFAULT_GEMINI_MODEL``.
            timeout: Seconds one request may take before it's abandoned.
            client: The HTTP client to send with (tests pass one with a mock transport).

        Raises:
            LLMConfigurationError: If no API key is available.
        """
        resolved_key = api_key if api_key is not None else os.environ.get("GEMINI_API_KEY")
        if not resolved_key:
            raise LLMConfigurationError("GEMINI_API_KEY is not set and no api_key was provided.")
        self._key = resolved_key
        self._model = model or os.environ.get(GEMINI_MODEL_ENV) or DEFAULT_GEMINI_MODEL
        self._timeout = timeout
        self._client = client if client is not None else httpx.Client(timeout=timeout)

    def complete(
        self, *, system: str, messages: tuple[LLMMessage, ...], max_tokens: int = 1024
    ) -> str:
        """Send ``messages`` to Gemini and return its raw text reply.

        Gemini calls the assistant's role ``"model"`` and takes the system prompt as
        ``systemInstruction``.
        """
        started = time.monotonic()
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [
                {
                    "role": "model" if message.role == "assistant" else "user",
                    "parts": [{"text": message.content}],
                }
                for message in messages
            ],
            "generationConfig": {
                "maxOutputTokens": max_tokens + GEMINI_THINKING_BUDGET,
                "thinkingConfig": {"thinkingBudget": GEMINI_THINKING_BUDGET},
            },
            "safetySettings": _GEMINI_SAFETY,
        }
        response = self._client.post(
            _GEMINI_URL.format(model=self._model),
            headers={"x-goog-api-key": self._key, "Content-Type": "application/json"},
            json=body,
            timeout=self._timeout,
        )
        if response.status_code >= 400:
            raise ProviderHTTPError("Gemini", response.status_code)
        try:
            data = response.json()
            candidates = data.get("candidates") or []
            parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
            text = "".join(
                part.get("text", "") for part in parts if not part.get("thought", False)
            )
        except (ValueError, AttributeError, IndexError, TypeError) as exc:
            raise ProviderRefusedError("Gemini's reply had an unexpected shape") from exc
        usage = data.get("usageMetadata") or {}
        logger.debug(
            "Gemini completion: model=%r elapsed=%.2fs input_tokens=%s output_tokens=%s",
            self._model,
            time.monotonic() - started,
            usage.get("promptTokenCount"),
            usage.get("candidatesTokenCount"),
        )
        if not text.strip():
            raise ProviderRefusedError("Gemini gave no text")
        return text


def should_fall_back(exc: BaseException) -> bool:
    """Whether a failed call is worth sending to the other provider.

    A ``429``, a timeout or a ``5xx`` (``is_transient_failure``), and a provider that answered
    with no text (``ProviderRefusedError``: the request was fine, that provider wouldn't answer
    it). Anything else (a ``400``, a ``401``, a bad request) is the caller's problem, and the other
    provider would only fail the same way or hide it.
    """
    return is_transient_failure(exc) or isinstance(exc, ProviderRefusedError)


class FallbackProvider:
    """An ``LLMProvider`` that asks ``primary`` and, when that fails, ``secondary``.

    Falls back only when ``should_fall_back`` says so; any other failure, and the secondary's
    own failure, is raised as it is. Logs the kind of failure (the exception's class and HTTP
    status), never its message, the prompt or a key.
    """

    def __init__(
        self,
        primary: LLMProvider,
        secondary: LLMProvider,
        *,
        names: tuple[str, str] = ("primary", "secondary"),
    ) -> None:
        self._primary = primary
        self._secondary = secondary
        self._names = names

    def complete(
        self, *, system: str, messages: tuple[LLMMessage, ...], max_tokens: int = 1024
    ) -> str:
        try:
            return self._primary.complete(system=system, messages=messages, max_tokens=max_tokens)
        except Exception as exc:
            if not should_fall_back(exc):
                raise
            logger.warning(
                "LLM %s failed (%s, status %s); asking %s",
                self._names[0],
                type(exc).__name__,
                getattr(exc, "status_code", None),
                self._names[1],
            )
        return self._secondary.complete(system=system, messages=messages, max_tokens=max_tokens)


@dataclass(frozen=True)
class Providers:
    """The two providers a turn uses.

    Attributes:
        reasoning: Brain, Think and the planner: Groq first, Gemini when Groq can't answer.
        writer: The reply writer: Gemini first, Groq when Gemini can't answer.
    """

    reasoning: LLMProvider | None
    writer: LLMProvider | None


def _chain(
    first: LLMProvider | None, second: LLMProvider | None, names: tuple[str, str]
) -> LLMProvider | None:
    if first is not None and second is not None:
        return FallbackProvider(first, second, names=names)
    return first if first is not None else second


# Seconds one writer request may take, per provider, with no SDK retries: the reply comes at the
# end of a turn that may already have used most of its deadline (a large file can take 2 minutes),
# and the template reply is always there when the writer is slow.
WRITER_TIMEOUT_S = 12.0


def build_providers_from_env() -> Providers:
    """The providers, from the keys in the environment. Keys are read only by the constructors.

    ``GROQ_API_KEY`` and ``GEMINI_API_KEY``: Groq reasons and Gemini writes, each falling back to
    the other. The writer's providers are their own instances, with ``WRITER_TIMEOUT_S`` and no
    SDK retries, so a slow model costs the turn at most about two such waits per draft. With only
    one of them, that one does both. With neither, ``ANTHROPIC_API_KEY`` does both (the older
    setup). With none, both are ``None``.
    """
    groq = gemini = groq_writer = gemini_writer = None
    try:
        if os.environ.get("GROQ_API_KEY"):
            groq = GroqProvider()
            groq_writer = GroqProvider(timeout=WRITER_TIMEOUT_S, max_retries=0)
    except LLMConfigurationError:
        logger.warning("GROQ_API_KEY is set but the Groq provider couldn't be configured")
    try:
        if os.environ.get("GEMINI_API_KEY"):
            gemini = GeminiProvider()
            gemini_writer = GeminiProvider(timeout=WRITER_TIMEOUT_S)
    except LLMConfigurationError:
        logger.warning("GEMINI_API_KEY is set but the Gemini provider couldn't be configured")
    if groq is None and gemini is None:
        try:
            if os.environ.get("ANTHROPIC_API_KEY"):
                claude = ClaudeProvider()
                return Providers(reasoning=claude, writer=claude)
        except LLMConfigurationError:
            logger.warning("ANTHROPIC_API_KEY is set but the provider couldn't be configured")
        return Providers(reasoning=None, writer=None)
    return Providers(
        reasoning=_chain(groq, gemini, ("Groq", "Gemini")),
        writer=_chain(gemini_writer, groq_writer, ("Gemini", "Groq")),
    )
