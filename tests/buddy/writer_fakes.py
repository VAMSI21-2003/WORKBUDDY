"""Fake reply writers for tests: the writer's model, without a network.

``FactEchoWriter`` answers like a careful model: it reads the VERIFIED RESULT in the prompt and
sends back the code's own sentences for the facts it must say, in up to three messages, citing
every fact it lists. It is how the API and page tests get a real multi-message reply through the
real checker. ``ScriptedWriter`` returns set answers in order, for the checker's own tests.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from buddy.llm import LLMMessage

_FACT_LINE = re.compile(r"^- \[(F\d+)\] (MUST SAY, )?([^:]+): (.*)$")
_FILE_TEXT_LINE = re.compile(r'^- \[(F\d+)\] (".*")$')


def file_text_in(prompt: str) -> dict[str, str]:
    """The quoted FILE TEXT of a writer prompt, by fact id."""
    found = {}
    in_section = False
    for line in prompt.splitlines():
        if line.startswith("FILE TEXT"):
            in_section = True
            continue
        if in_section:
            match = _FILE_TEXT_LINE.match(line)
            if not match:
                break
            found[match.group(1)] = json.loads(match.group(2))
    return found


def facts_in(prompt: str) -> list[tuple[str, bool, str, str]]:
    """``(id, required, kind, text)`` for every fact line of a writer prompt."""
    found = []
    for line in prompt.splitlines():
        match = _FACT_LINE.match(line)
        if match:
            found.append((match.group(1), bool(match.group(2)), match.group(3), match.group(4)))
    return found


def echo_answer(prompt: str, *, chat_text: str = "hey! good to see you") -> str:
    """The answer a careful writer gives: the required facts' own sentences, all cited."""
    facts = facts_in(prompt)
    quoted = file_text_in(prompt)
    required = [
        (fid, quoted[fid] if fid in quoted else text) for fid, must, _kind, text in facts if must
    ]
    if not required:
        return json.dumps({"messages": [chat_text], "facts_used": []})
    texts = [text for _, text in required]
    if len(texts) > 3:
        texts = [texts[0], texts[1], " ".join(texts[2:])]
    return json.dumps({"messages": texts, "facts_used": [fid for fid, _ in required]})


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
