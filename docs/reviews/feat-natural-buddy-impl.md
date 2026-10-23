# feat/natural-buddy: implementation notes

**Owner's request, 2026-10-23:** Buddy should text like a close friend and coworker. Replies are written by the model, with no fixed sentences, and Buddy can chat about anything. Rule 1 still holds absolutely. This reverses the decided behaviour "the Voice model never writes reply text". `CLAUDE.md`'s "Decided behaviour" is updated in this branch at the owner's request.

This change is rule-touching. Under the brief, the reviewer agent runs on Opus. No live runs happen in this session: the session can't reach Gemini, so the owner tests on the server.

## What a turn does now

1. Every stage up to Inspector is unchanged.
2. **Voice** calls `NaturalVoice` (`buddy/writer.py`), which is the app's speaker in `backend/main.py`:
   1. `build_fact_sheet` (`buddy/facts.py`) builds the turn's VERIFIED RESULT. It returns `None` for a turn the sheet doesn't cover: a stopped chain, a delivery the Inspector didn't pass, an unverified result, or a stage that raised. Voice still answers a stage failure itself, before any speaker runs. On `None`, the reply is the template reply (`PersonalityVoice`), as before.
   2. The writer model gets:
      - the system prompt: `buddy/character/buddy.md` followed by fixed answering rules;
      - the user's message;
      - the recent conversation (8 turns);
      - the mode, adjusted by `effective_mode` for urgent or frustrated users;
      - Brain's `user_state`;
      - the fact sheet.

      It answers with JSON: `{"messages": [1–3], "facts_used": [ids]}`.
   3. `check_reply` (`buddy/claims.py`) checks the draft. If it fails, the model gets the checker's reasons and writes **one** rewrite. If the rewrite also fails, the template reply is shown. The template reply is also shown when the call fails, the JSON is broken, or the first draft took longer than `REWRITE_BUDGET_S` (40 s).
3. `Voice._apply` stores the messages in `state.reply_messages`, and `state.reply` holds them one per line.

## The fact sheet (`buddy/facts.py`)

The sheet is built only from what the template reply already uses:
- `delivery_parts`, which was split out of `_verified_delivery_reply` so the two can't drift apart;
- `build_outcome_report`;
- `not_done_sentences`;
- `_final_state_sentence` and `_residual_sentences`;
- `_values_only_sentences`;
- `_file_used_sentence`;
- the no-change reply's own order (`TemplateSpeaker._no_change_reply`).

Each `Fact` has the code's own sentence. It also has:
- `quantities`: each number with what it counts (duplicate, empty, rows, formula, any) and whether it was removed or is left;
- `names`: exact file names;
- `needs`: what the checker must find when the fact is required.

Text from the file (analysis findings that name a column or quote a cell) is kept in `Fact.file_text`. The writer sees it only under `FILE TEXT`, as a JSON string capped at 160 characters. The user's message is quoted and capped at 1500 characters, and each conversation turn at 300.

The sheet covers four kinds of turn:
- **delivered:** what was done, what was done though not asked for, what wasn't done, what the file holds now, the file used, the values-only and formula disclosures, the order used, a resumed request, the report or findings, and a failed analysis or re-check;
- **nothing left:** the latest copy already had nothing to remove;
- **no change:** no file, a refused file, a question, an understanding failure, what wasn't done, what Buddy can do, the report or findings, and "no file was changed";
- **chat:** what Buddy can do, and whether a file has been uploaded. Nothing is required.

## The claim checker (`buddy/claims.py`)

The checker reads the draft and lists problems. Each problem has a code and a fixed-text detail containing no draft words and no file text.

1. **Shape:** 1–3 non-empty messages of at most 600 characters each, and fact ids that exist in the sheet.
2. **File names:** names from the sheet are replaced first. Any remaining token that looks like a file name (`*.xlsx`, `.csv`, `.pdf` and so on) is refused. Names are matched case-sensitively.
3. **Numbers:** every number counts as a number when written in digits, as a word from zero to twenty that is followed by a noun or a phase word, or as "no" followed by a noun. It must match a quantity of a **cited** fact, with the same noun and, when the draft states one, the same phase. The phase comes from the word right before the number ("removed 2", "still has 2") or the word right after its noun ("2 duplicates gone", "9 left"). A number with no noun may echo a number the user wrote ("5 mins"). On a chat turn, a number counts as a file claim only when it is attached to a file noun.
4. **Done-claims:** the draft is split into sentences and segments. A segment claims something is done if it holds a done-word that no "not" before it takes back, unless an "I" stands between the "not" and the done-word. Done-words are actions ("removed", "sorted", "emailed"…, and a few Hinglish ones) or states ("gone", "clean", "ready", "no more …"). A segment with no done-word of its own shares the nearest done-word in its sentence when no "not" segment lies between them. Each claim is then checked:
   - something no skill does (sort, email, format, colour…) or a near-duplicate is never done;
   - duplicates, empty rows or the report must have been done this turn, or, for state words only, be measured at zero;
   - "done" without naming what, or with only a generic object, is allowed only when the whole request was done. On a delivered turn, "the copy is ready" is allowed. On a chat turn, a claim with a file object, an "I", or nothing else at all is refused.
5. **Wrong file:** the draft can't call the original the "previous copy", or say a copy turn "worked on the original".
6. **Kept:** nothing may say the formatting, formulas, widths and so on were kept.
7. **Chat:** duplicates, empty rows, "your file" or a file name may appear only in a question, an offer or a "not".
8. **Required facts:** every `need` of every required fact must be met. A `need` marked negated must match in a sentence that says "not". This covers what wasn't done (the user's own words for an unsupported request, or the action's object), "no file was changed", the values-only disclosure, the file name, formulas, order, a resumed request, and the report's findings.

The checker never reads the user's words to decide what they asked; that comes from Brain's reading, through the ledger. It matches the model's output. It reads the user's message in only one place: to allow an echoed number. The not-done check for an unsupported request looks for the content words of Brain's `source_phrase` (four-letter stems) in the draft.

**Known limits (stated, not hidden):**
- The checker reads English and a little Hinglish. Elsewhere, done-claims and number words in other languages are not caught. On a file turn, the required English anchors fail in practice ("values only", the not-done statements, "haven't touched"), so such a draft is refused, rewritten, and then replaced by the template reply. On a chat turn, a claim in another language with no file noun and no file name would pass.
- Word lists can't be complete. The checker leans towards refusing: a refusal costs a rewrite or a flat template reply, never a false claim. Some true replies are refused, for example "the duplicates stay gone" on a follow-up that didn't measure duplicates.

## Models and keys (`buddy/llm.py`)

- `GeminiProvider` calls the REST API over `httpx`, which is already a dependency, so no new package is needed. The key goes in the `x-goog-api-key` header, never the URL. An HTTP error raises `ProviderHTTPError` with the status only. A reply with no text (a safety block) raises `ProviderRefusedError`. The model comes from `GEMINI_MODEL`, default `gemini-2.5-flash`. Safety is set to `BLOCK_ONLY_HIGH`, so a person describing a bad day still gets an answer. There is a fixed thinking budget.
- `FallbackProvider` asks the other provider on a 429, a timeout or a 5xx (`is_transient_failure`, unchanged), and also on `ProviderRefusedError`. That last one is beyond the brief: a blocked crisis message shouldn't fall to the greeting template. Any other failure is raised as it is.
- `build_providers_from_env`:
  - Groq and Gemini keys: reasoning is Groq then Gemini; the writer is Gemini then Groq.
  - One key only: that provider does both.
  - Neither: Anthropic does both, as before.
  - None of the three: no provider. `backend.main.build_llm_provider_from_env` returns the reasoning provider, so `/healthz` and older callers behave as before.

## A reply is a list of messages

- **API:** `FinishedTurnSummary.messages: list[str]`, required; it is `null` while running. `reply` stays and holds the messages one per line, so `smoke.sh`'s `.reply` and every older client still work. `backend/openapi.json` is regenerated.
- **Turn record:** an optional `messages` key, written only when there are two or more messages. A one-message record is byte-identical to the older format, so an older server can open it. On read, the key must be 2–3 non-empty single-line strings joined to `reply`; when it is absent, the reply is the one message (the migration). The new fixture is `tests/deploy/fixtures/data-v5`, written by `make_data_fixture.py 5` with the scenario now running `NaturalVoice` on `FactEchoWriter`. v1–v4 are untouched. `test_saved_data_survives` now also checks that `messages` reads back, with `[reply]` for the older fixtures.
- **Page:**
  - one bubble per message, each with its own reply slot (`data-message`);
  - downloads under the last message;
  - on a turn that just finished (`turn_item(t, fresh=True)` from `turn_fragment.html`), later messages appear after a three-dot typing pause, through CSS only, with no new script;
  - `prefers-reduced-motion` and a full page load show them all at once;
  - the UI-copy snapshot is updated (`{t.reply}` became `{m}`).

## Deploy

- `docker-compose.yml` passes `GEMINI_API_KEY` and `GEMINI_MODEL`, both optional with empty defaults.
- `.env.example` adds `GEMINI_API_KEY=`, plus a commented `GEMINI_MODEL`.
- `deploy.md`'s settings table is updated.
- `require_settings` still requires only `GROQ_API_KEY`. With only that key, the app starts, `/healthz` shows `llm_configured: true`, and Groq writes the replies (tested in `tests/backend/test_natural_replies.py`). The `smoke.sh` flow passes because `.reply` still holds the reply.

## Tests added

- `tests/buddy/test_natural_voice.py`:
  - the checker refuses each of the following:
    - a wrong count (four variants);
    - a wrong or invented file name (four);
    - claims of things not done (seven, Hinglish included);
    - the wrong file named;
    - a missing disclosure;
    - a missing file name;
    - a missing not-done statement;
    - a bare "done" when part wasn't done;
    - file claims on a chat turn (five);
    - "formatting is kept";
  - ordinary chat passes;
  - the code's own fact sentences pass their own check across four turn kinds;
  - the rewrite path and the template path, including broken JSON, a raised call and the time budget;
  - a stopped chain never calls the writer;
  - an injection through a column header is quoted and capped, an obeying model is refused twice, and the template reply is shown;
  - the user message is quoted;
  - provider fallback both ways, including through `build_providers_from_env`, with no fallback on 400 or 401;
  - Gemini over a mock transport: the key is in the header and never in the URL or the logs, and the model comes from the environment;
  - with only the Groq key, Groq does both.
- `tests/backend/test_natural_replies.py`: messages through the API, on disk and after a restart; a one-message reply saves in the old format; a failed stage gives one message and never asks the writer; the app works through the production factory with only the Groq key.
- `tests/web/test_reply_messages.py`: bubbles in order, downloads under the last message, the typing pause only when fresh, and reduced motion.

## Not done here

- **Live runs:** none, by the owner's instruction. Gemini isn't reachable from the session; the owner tests on the server.
- **Turns the sheet doesn't cover** (a stopped chain, a failed check, a stage that raised) keep the template reply. The owner asked for "never fixed sentences"; these are rare error paths where a fixed, honest reply was judged safer. The owner can decide whether to extend the writer to them.
