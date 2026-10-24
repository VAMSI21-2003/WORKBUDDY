"""Fake reply writers for tests: the writer's model, without a network.

``FactEchoWriter`` answers like a careful model: it reads the facts in the prompt and shows every
one it must, by its slot, in up to three messages. It is how the API and page tests get a real
multi-message reply through the real checker. ``ScriptedWriter`` returns set answers in order,
and ``PromptWriter`` answers from the prompt, for the checker's own tests.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from buddy.llm import LLMMessage

_FACT_LINE = re.compile(r"^- (F\d+) \((MUST SHOW, )?([^)]*)\):$")
_SLOT_LINE = re.compile(r"^    \{(F\d+[a-z])\} (.*)$")


def facts_in(prompt: str) -> list[tuple[str, bool, str, list[str]]]:
    """``(id, required, kind, slots)`` for every fact of a writer prompt, in prompt order."""
    found: list[tuple[str, bool, str, list[str]]] = []
    for line in prompt.splitlines():
        match = _FACT_LINE.match(line)
        if match:
            found.append((match.group(1), bool(match.group(2)), match.group(3), []))
            continue
        slot = _SLOT_LINE.match(line)
        if slot and found:
            found[-1][3].append(slot.group(1))
    return found


def echo_answer(prompt: str, *, chat_text: str = "hey! good to see you") -> str:
    """The answer a careful writer gives: every required fact in its first phrasing, in order."""
    facts = facts_in(prompt)
    if not facts:
        return json.dumps({"messages": [chat_text]})
    slots = [f"{{{fact[3][0]}}}" for fact in facts if fact[1]]
    if len(slots) > 3:
        slots = [slots[0], slots[1], " ".join(slots[2:])]
    return json.dumps({"messages": slots or [chat_text]})


class FactEchoWriter:
    """A writer model that echoes the required facts (see ``echo_answer``). Records each call."""

    def __init__(self, *, chat_text: str = "hey! good to see you") -> None:
        self.calls: list[tuple[str, tuple[LLMMessage, ...]]] = []
        self._chat_text = chat_text

    def complete(
        self, *, system: str, messages: tuple[LLMMessage, ...], max_tokens: int = 1024
    ) -> str:
        self.calls.append((system, messages))
        return echo_answer(messages[0].content, chat_text=self._chat_text)


class ScriptedWriter:
    """A writer model that returns ``answers`` in order (a dict is sent as JSON; an exception
    is raised). Records each call."""

    def __init__(self, answers: Sequence[Any]) -> None:
        self._answers = list(answers)
        self.calls: list[tuple[str, tuple[LLMMessage, ...]]] = []

    def complete(
        self, *, system: str, messages: tuple[LLMMessage, ...], max_tokens: int = 1024
    ) -> str:
        self.calls.append((system, messages))
        if not self._answers:
            raise AssertionError("the writer was asked more often than the test expected")
        answer = self._answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer if isinstance(answer, str) else json.dumps(answer)


def required_slots(prompt: str) -> list[str]:
    """The first-phrasing slot of every fact the prompt marks MUST SHOW, as ``{F1a}``."""
    return [f"{{{fact[3][0]}}}" for fact in facts_in(prompt) if fact[1]]


class PromptWriter:
    """A writer model that answers each call with ``answer(prompt, attempt)``. Records calls."""

    def __init__(self, answer: Any) -> None:
        self._answer = answer
        self.calls: list[tuple[str, tuple[LLMMessage, ...]]] = []

    def complete(
        self, *, system: str, messages: tuple[LLMMessage, ...], max_tokens: int = 1024
    ) -> str:
        self.calls.append((system, messages))
        result = self._answer(messages[0].content, len(self.calls))
        return result if isinstance(result, str) else json.dumps(result)
