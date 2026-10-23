"""Engine factories for the API tests: the real pipeline with a fake LLM, plus spies.

``PipelineFactory`` builds the real engine (``build_engine``: real skills,
Hands, Inspector and Voice) on a queued fake provider, and records what
each run's state held *when ``engine.run`` returned* (the API resets the
state afterwards, so the values are copied, not the object).
"""

from __future__ import annotations

import dataclasses
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from buddy.engine_factory import build_engine
from buddy.hands import ConfirmationProvider
from buddy.llm import LLMMessage
from buddy.models import StepResult
from buddy.state import BuddyState
from buddy.voice import PersonalityVoice, TemplateSpeaker
from buddy.writer import NaturalVoice
from core.engine import CognitiveCycle, Engine
from tests.buddy.test_engine_factory import _chain_replies, _single_intent_replies

SPEAKERS = ("template", "friend", "focus")


class QueuedProvider:
    """Canned replies in call order; records every call. Thread-safe."""

    def __init__(self, replies: list[str] | None = None) -> None:
        self._replies = list(replies or [])
        self._lock = threading.Lock()
        self.calls: list[tuple[str, tuple[LLMMessage, ...]]] = []

    def add(self, replies: list[str]) -> None:
        with self._lock:
            self._replies.extend(replies)

    def complete(
        self, *, system: str, messages: tuple[LLMMessage, ...], max_tokens: int = 1024
    ) -> str:
        with self._lock:
            self.calls.append((system, messages))
            if not self._replies:
                raise AssertionError("no more canned LLM replies configured for this test")
            return self._replies.pop(0)


def single(intent: str, file_to_use: str | None = None) -> list[str]:
    """Brain's and Think's canned replies for one action; ``file_to_use="original"`` is Brain
    reading the person asking to start over from the upload."""
    brain, think = _single_intent_replies(intent)
    if file_to_use is not None:
        brain = json.dumps({**json.loads(brain), "file_to_use": file_to_use})
    return [brain, think]


def chain(*planned: tuple[str, str]) -> list[str]:
    return _chain_replies(*planned)


DEDUPE_THEN_EMPTY = (
    ("spreadsheet.deduplicate", "remove duplicate rows"),
    ("spreadsheet.remove_empty_rows", "then remove empty rows"),
)


@dataclass
class RunRecord:
    """What one ``engine.run`` returned, copied at that moment."""

    reply: str | None
    failure: Any
    outputs: tuple[str, ...]
    summaries: tuple[str, ...]
    findings: tuple[str, ...]
    user_message: str
    turn_output_dir: str | None
    steps: tuple[StepResult, ...] = ()


@dataclass
class PipelineFactory:
    """``engine_factory`` for ``create_app``: the real pipeline on a fake provider.

    Args:
        provider: The fake LLM.
        speaker: ``"template"`` (``TemplateSpeaker``), ``"friend"``/``"focus"``
            (``PersonalityVoice``; the mode comes from the session), or ``"natural"``
            (``NaturalVoice``, the app's own, on ``writer``).
        wrap: Optional ``(cycle) -> cycle`` to replace stages, e.g. with a raiser.
        writer: The fake writer model for ``"natural"``.
    """

    provider: QueuedProvider = field(default_factory=QueuedProvider)
    speaker: str = "friend"
    wrap: Callable[[CognitiveCycle], CognitiveCycle] | None = None
    runs: list[RunRecord] = field(default_factory=list)
    confirmation_providers: list[ConfirmationProvider] = field(default_factory=list)
    writer: Any = None

    def __call__(self, confirmation_provider: ConfirmationProvider) -> Any:
        self.confirmation_providers.append(confirmation_provider)
        if self.speaker == "template":
            speaker: Any = TemplateSpeaker()
        elif self.speaker == "natural":
            speaker = NaturalVoice(self.writer)
        else:
            speaker = PersonalityVoice(self.provider)
        engine = build_engine(
            llm_provider=self.provider,
            speaker=speaker,
            confirmation_provider=confirmation_provider,
        )
        if self.wrap is not None:
            engine = Engine(self.wrap(engine._cycle))
        return _SpyEngine(engine, self.runs)


class _SpyEngine:
    def __init__(self, engine: Engine, runs: list[RunRecord]) -> None:
        self._engine = engine
        self._runs = runs

    def run(self, state: BuddyState) -> BuddyState:
        result = self._engine.run(state)
        execution = result.execution_result
        self._runs.append(
            RunRecord(
                reply=result.reply,
                failure=result.failure,
                outputs=tuple(execution.outputs) if execution else (),
                summaries=tuple(s.summary for s in execution.steps) if execution else (),
                findings=tuple(
                    f for s in execution.steps for f in s.self_reported_findings
                )
                if execution
                else (),
                user_message=result.user_message,
                turn_output_dir=result.turn_output_dir,
                steps=tuple(execution.steps) if execution else (),
            )
        )
        return result


class Raising:
    """A stage that raises, before or after running the real stage."""

    def __init__(self, inner: Any, *, after: bool, exc: BaseException | None = None) -> None:
        self._inner = inner
        self._after = after
        self._exc = exc if exc is not None else RuntimeError("secret cell 4111 at /srv/data")

    def __call__(self, state: BuddyState) -> BuddyState:
        if self._after:
            self._inner(state)
        raise self._exc


def raise_in(stage: str, *, after: bool = True, exc: BaseException | None = None) -> Callable[
    [CognitiveCycle], CognitiveCycle
]:
    def wrap(cycle: CognitiveCycle) -> CognitiveCycle:
        inner = getattr(cycle, stage)
        return dataclasses.replace(cycle, **{stage: Raising(inner, after=after, exc=exc)})

    return wrap


class _TouchUploads:
    """A stage that runs the real one, then changes every upload (a rule-2 breach)."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def __call__(self, state: BuddyState) -> BuddyState:
        state = self._inner(state)
        for upload in state.uploaded_files:
            path = Path(upload)
            path.chmod(0o644)
            with open(path, "ab") as handle:
                handle.write(b"\0")
        return state


def touch_uploads_in(stage: str) -> Callable[[CognitiveCycle], CognitiveCycle]:
    def wrap(cycle: CognitiveCycle) -> CognitiveCycle:
        return dataclasses.replace(cycle, **{stage: _TouchUploads(getattr(cycle, stage))})

    return wrap


class BlockingFactory:
    """An engine that waits for ``release`` and then gives ``reply``, never touching files."""

    def __init__(self, reply: str = "Nothing was done.") -> None:
        self.release = threading.Event()
        self.started = threading.Event()
        self.finished = threading.Event()
        self.reply = reply

    def __call__(self, confirmation_provider: ConfirmationProvider) -> Any:
        return self

    def run(self, state: BuddyState) -> BuddyState:
        self.started.set()
        self.release.wait(timeout=30)
        state.reply = self.reply
        self.finished.set()
        return state
