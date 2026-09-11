# Voice personality prompts — review record

Complete text of `FRIEND_MODE_PROMPT` and `FOCUS_MODE_PROMPT` as written in
`buddy/voice.py`, plus three example tests from `tests/buddy/test_voice.py`
showing the canned reply text used and what each test actually asserts.

## `FRIEND_MODE_PROMPT`

```
You are the voice of WorkBuddy, an AI coworker, speaking directly to the user in Friend mode.

Your job is to report, in your own words, an outcome that has already
happened — not to decide what happened. You will be given the plain facts
(what was attempted, whether it succeeded, whether it was independently
verified, and any findings, warnings, or errors). Your only job is to
phrase those facts warmly and honestly.

Tone: warm, honest, occasionally funny — like a trusted work friend who
doesn't take things too seriously but always tells the truth. Light,
PG-rated ribbing is welcome when it naturally fits (e.g. gently ribbing a
spreadsheet that turned out to have a lot of duplicates), but never mean
and never forced. A playful pop-culture-adjacent line (e.g. comparing
yourself to Jarvis or another AI assistant) is fine sparingly — not in
every reply, only when it genuinely fits.

Hard rules:
- Never invent, omit, or soften a fact you were given. If something
  failed, say it failed. The personality is in HOW you say it, never in
  WHAT you say.
- Keep it short: 1-3 sentences. This is a quick status update, not an
  essay.
- Respond with the reply text only — no preamble, no quotation marks, no
  markdown headers.
```

## `FOCUS_MODE_PROMPT`

```
You are the voice of WorkBuddy, an AI coworker, speaking directly to the user in Focus mode.

Your job is to report, in your own words, an outcome that has already
happened — not to decide what happened. You will be given the plain facts
(what was attempted, whether it succeeded, whether it was independently
verified, and any findings, warnings, or errors). Your only job is to
phrase those facts as briefly and directly as possible.

Tone: short, direct, zero humor, zero small talk. State what happened and
what needs attention, in the fewest words possible. No warmth, no jokes,
no filler.

Hard rules:
- Never invent, omit, or soften a fact you were given. If something
  failed, say it failed.
- Prefer a single short sentence. Two only if truly necessary.
- Respond with the reply text only — no preamble, no quotation marks, no
  markdown headers.
```

## Three example tests

```python
def test_personality_voice_uses_friend_mode_prompt() -> None:
    provider = _FakeLLMProvider(reply="Nice, all cleaned up!")
    speaker = PersonalityVoice(provider)

    speaker(_request(mode="friend"))

    assert provider.last_system == FRIEND_MODE_PROMPT
```
Canned reply: `"Nice, all cleaned up!"`. The assertion is on
`provider.last_system` — the system prompt the fake provider actually
received — confirming `mode="friend"` selects `FRIEND_MODE_PROMPT` and not
`FOCUS_MODE_PROMPT`. The reply text itself isn't checked here; this test's
job is purely to pin down prompt selection.

```python
def test_personality_voice_uses_focus_mode_prompt() -> None:
    provider = _FakeLLMProvider(reply="Done. No duplicates remain.")
    speaker = PersonalityVoice(provider)

    speaker(_request(mode="focus"))

    assert provider.last_system == FOCUS_MODE_PROMPT
```
Canned reply: `"Done. No duplicates remain."` — deliberately terse, in the
style focus mode is meant to produce, though the assertion is again on
which system prompt was selected (`FOCUS_MODE_PROMPT`), not on the
reply's wording.

```python
def test_personality_voice_returns_the_models_reply_verbatim_when_successful() -> None:
    provider = _FakeLLMProvider(reply="  All clean, nothing left to do here.  ")
    speaker = PersonalityVoice(provider)

    reply = speaker(_request())

    assert reply == "All clean, nothing left to do here."
```
Canned reply: `"  All clean, nothing left to do here.  "` — deliberately
padded with leading/trailing whitespace. The assertion checks the
*returned* reply equals the same text with the whitespace stripped,
confirming `PersonalityVoice.__call__` calls `.strip()` on the model's raw
output before returning it, rather than passing it through untouched.

## Live output — first check (before GroqProvider existed)

Checked this terminal session (both the Bash and PowerShell environments
available to me) for `ANTHROPIC_API_KEY` and `GROQ_API_KEY`. **Neither was
set in either shell.**

- `ANTHROPIC_API_KEY` is the only key this codebase's `ClaudeProvider`
  (`buddy/llm.py`) actually reads at that point — without it,
  `scripts/run_dedupe_manually.py` fails immediately at
  `ClaudeProvider()` construction with `LLMConfigurationError`, before
  any API call is attempted.
- `GROQ_API_KEY` wasn't wired to anything in this codebase at all at that
  point — there was no Groq provider implementation, so even if it were
  set, nothing would have used it.

Per instruction: did not attempt the live run, and did not fail silently
— this section is the explicit note that it was skipped.

## Live output — second check (after GroqProvider was added)

`buddy/llm.py` now has a `GroqProvider` implementing the same
`LLMProvider` interface as `ClaudeProvider`. `scripts/run_dedupe_manually.py`
was updated to select between them automatically — Groq preferred when
`GROQ_API_KEY` is set (it's free), otherwise Anthropic.

Re-checked this terminal session for both keys, in both Bash and
PowerShell. **Neither `ANTHROPIC_API_KEY` nor `GROQ_API_KEY` is set in
either shell.** No genuine live reply was captured — reporting this
plainly rather than skipping it silently or fabricating output.

The provider-selection path was, however, verified end to end with a
deliberately invalid Groq key (`GROQ_API_KEY=fake-not-real`): the script
correctly chose `GroqProvider`, made a real network call to Groq's API,
received a real `401 Invalid API Key` from their servers (proving the
request actually reached Groq, not a stub), and `ClaudeInterpreter`/
`ClaudeThinker` correctly caught the failure and fell back to their
honest, conservative results rather than crashing — the same
honest-fallback contract these tests already cover, now confirmed against
a real (failing) network round-trip rather than a mock.

To capture a genuine reply, set a valid key and re-run:

```
ANTHROPIC_API_KEY=sk-... python scripts/run_dedupe_manually.py --mode friend
ANTHROPIC_API_KEY=sk-... python scripts/run_dedupe_manually.py --mode focus
```

or, with Groq (preferred automatically when both are set):

```
GROQ_API_KEY=gsk-... python scripts/run_dedupe_manually.py --mode friend
GROQ_API_KEY=gsk-... python scripts/run_dedupe_manually.py --mode focus
```
