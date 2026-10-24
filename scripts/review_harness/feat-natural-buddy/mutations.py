"""One-place mutations of the new code; each must make at least one of the branch's tests fail.

    python mutations.py [NAME ...]          # the three new test files; exports HEAD to a temp dir
    python mutations.py --full NAME ...     # the whole suite (slow), for the survivors

Prints KILLED (a test failed) or SURVIVED (all tests still pass: the guard is untested).
"""
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
TESTS = ["tests/buddy/test_natural_voice.py", "tests/backend/test_natural_replies.py",
         "tests/web/test_reply_messages.py"]

M = {
    "check_reply_noop": ("buddy/claims.py", "    problems: list[Problem] = []\n    if not 1 <=",
                         "    return ()\n    problems: list[Problem] = []\n    if not 1 <="),
    "numbers_off": ("buddy/claims.py", "    problems += _check_numbers(segments, cited, sheet, user_message)\n", ""),
    "phase_ignored": ("buddy/claims.py", "    if number.phase and quantity.phase != number.phase:\n        return False\n", ""),
    "noun_ignored": ("buddy/claims.py",
                     "    if number.noun != NOUN_OTHER and quantity.noun not in (number.noun, NOUN_ANY):\n        return False\n", ""),
    "claims_off": ("buddy/claims.py", "    problems += _check_claims(segments, sheet)\n", ""),
    "chat_off": ("buddy/claims.py", "        problems += _check_chat(segments)\n", "        pass\n"),
    "needs_off": ("buddy/claims.py", "    problems += _check_needs(text, prepared, segments, sheet)\n", ""),
    "kept_off": ("buddy/claims.py", "    problems += _check_kept(segments)\n", ""),
    "file_used_off": ("buddy/claims.py", "    problems += _check_file_used(segments, sheet)\n", ""),
    "file_name_off": ("buddy/claims.py", "    if _FILE_LIKE.search(prepared):", "    if False:"),
    "internal_off": ("buddy/claims.py", "    if _INTERNAL_RE.search(text):", "    if False:"),
    "unknown_fact_off": ("buddy/claims.py", "    if unknown:\n", "    if False:\n"),
    "shape_off": ("buddy/claims.py", "    if not 1 <= len(messages) <= MAX_MESSAGES or any(", "    if False and any("),
    "negated_need_any_sentence": ("buddy/claims.py",
                                  "            if any(s.sentence in negated_sentences and regex.search(s.text) for s in segments):",
                                  "            if any(regex.search(s.text) for s in segments):"),
    "complete_allows_all": ("buddy/claims.py", "        if sheet.complete:\n            continue\n", "        continue\n"),
    "unsupported_off": ("buddy/claims.py", "        if \"unsupported\" in objects:\n", "        if False:\n"),
    "quote_setaside_all": ("buddy/claims.py", "            if inner and any(inner in source for source in file_texts):",
                           "            if inner:"),
    "user_number_echo_any": ("buddy/claims.py",
                             "        if number.noun == NOUN_OTHER and not number.phase and number.value in users:",
                             "        if number.value in users:"),
    "rewrite_returns_draft": ("buddy/writer.py",
                              "        logger.warning(\"Reply writer: using the template reply on a %s turn\", sheet.turn)\n        return self._fallback(request)",
                              "        return draft.messages if draft else self._fallback(request)"),
    "no_rewrite": ("buddy/writer.py", "            if attempt == 2 or self._clock() - started > self._budget:",
                   "            if True:"),
    "no_budget": ("buddy/writer.py", "            if attempt == 2 or self._clock() - started > self._budget:",
                  "            if attempt == 2:"),
    "user_msg_unquoted": ("buddy/writer.py",
                          "    lines.append(\"THEIR MESSAGE (quoted): \" + quoted(request.user_message, MAX_USER_MESSAGE_CHARS))",
                          "    lines.append(\"THEIR MESSAGE (quoted): \" + request.user_message)"),
    "file_text_uncapped": ("buddy/facts.py", "    return text if len(text) <= MAX_FILE_TEXT else", "    return text if True else"),
    "failure_sheet": ("buddy/facts.py", "    if request.failure is not None:\n        return None\n", ""),
    "stopped_chain_sheet": ("buddy/facts.py", "    if not _nothing_delivered(request):\n        return None\n", ""),
    "values_only_not_required": ("buddy/facts.py",
                                 "        VALUES_ONLY_SENTENCE,\n        required=True,",
                                 "        VALUES_ONLY_SENTENCE,\n        required=False,"),
    "not_done_not_required": ("buddy/facts.py",
                              "            KIND_NOT_DONE,\n            _not_done_sentence(action),\n            required=True,",
                              "            KIND_NOT_DONE,\n            _not_done_sentence(action),\n            required=False,"),
    "file_name_not_required": ("buddy/facts.py", "            f\"Your cleaned copy of {name} is ready.\",\n            required=True,",
                               "            f\"Your cleaned copy of {name} is ready.\",\n            required=False,"),
    "gemini_key_in_url": ("buddy/llm.py", "            _GEMINI_URL.format(model=self._model),",
                          "            _GEMINI_URL.format(model=self._model) + \"?key=\" + self._key,"),
    "fallback_on_everything": ("buddy/llm.py", "            if not should_fall_back(exc):\n                raise\n", ""),
    "refused_no_fallback": ("buddy/llm.py", "    return is_transient_failure(exc) or isinstance(exc, ProviderRefusedError)",
                            "    return is_transient_failure(exc)"),
    "writer_is_reasoning": ("backend/main.py", "        engine_factory = production_engine_factory(providers.reasoning, providers.writer)",
                            "        engine_factory = production_engine_factory(providers.reasoning, providers.reasoning)"),
    "store_messages_dropped": ("buddy/voice.py", "        state.reply_messages = messages\n", "        state.reply_messages = messages[:1]\n"),
}


def run(name):
    path, old, new = M[name]
    d = Path(tempfile.mkdtemp(prefix=f"mut-{name}-"))
    subprocess.run(f"git -C {REPO} archive HEAD | tar -x -C {d}", shell=True, check=True)
    f = d / path
    s = f.read_text()
    if s.count(old) != 1:
        return f"{name:28} NOT APPLIED (pattern found {s.count(old)} times)"
    f.write_text(s.replace(old, new))
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-rf", "-p", "no:cacheprovider", *TESTS],
                       cwd=d, capture_output=True, text=True, timeout=900)
    lines = r.stdout.strip().splitlines() or ["?"]
    failed = next((x.split(" - ")[0] for x in lines if x.startswith("FAILED ")), "")
    return f"{name:28} {'KILLED  ' if r.returncode else 'SURVIVED'} {lines[-1]} {failed}"


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["--full"]:  # the whole suite instead of the three new test files
        TESTS[:] = ["tests"]
        args = args[1:]
    for n in args or M:
        print(run(n), flush=True)
