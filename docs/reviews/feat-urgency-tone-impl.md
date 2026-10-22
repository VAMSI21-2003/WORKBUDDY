# feat/urgency-tone: implementation notes

Branch `feat/urgency-tone`, from `origin/main` (`d818409`). Task: urgency and frustration detection
(`docs/BUDDY_PERSONALITY.md` section 9), plus three small items. This change is rule-touching (it
changes reply text, and a tone switch that dropped a disclosure would break rule 1), so it goes
through the review loop (`docs/process/review-loop.md`).

## What changed

1. **Brain: `user_state`** (`buddy/brain.py`, `buddy/models.py`). One new closed-vocabulary field,
   `"urgent" | "frustrated" | "calm"`, on `Understanding` (default `calm`). The prompt asks the
   model to decide it from the message and the recent conversation (already in the prompt) and to
   answer `calm` when unsure. `_parse_user_state` keeps a value in the vocabulary (spelling slips
   of casing and whitespace are accepted, as for `file_to_use`); anything else, a missing field,
   or another type is `calm` (logged at WARNING for a present but unknown value). No code reads
   the user's words for it. `MentionCrossCheck` copies it through (`dataclasses.replace`).
2. **Voice** (`buddy/voice.py`, `buddy/voice_catalog.py`). `effective_mode(mode, user_state)`
   returns `"focus"` (`BRIEF_MODE`) for an urgent or frustrated person and the chosen mode
   otherwise; `PersonalityVoice` uses it for both the delivered reply (opener and closer from the
   catalog) and the no-change replies (greeting and Think's question, keyed by style). Nothing
   else in a reply depends on the mode, so every fact, count, disclosure and "I worked on ..."
   sentence is the calm reply's; only the opener, the closer, and the fixed greeting and question
   wording change. The focus catalog has no opener and at most a two-word closer. Replies are
   fixed text chosen by code and none apologises or names a feeling, so "frustrated" needs no
   extra rule: it is checked by a lint over every catalog text and every reply in the tests.
   `TemplateSpeaker` has no mode and keeps its plain wording (it is the speaker with no opener,
   no humor and no mode to switch); it is covered in the tests as the third speaker.
3. **Plan card and questions** (`backend/confirmation.py`, `buddy/hands.py`). Clarifying questions
   follow item 2 (they are keyed by style). The confirmation card is built by code in
   `backend/confirmation.py`; `Hands` sets the new field `ConfirmationStep.brief` on every step of
   a plan whose person Brain read as urgent or frustrated, and `WebConfirmationProvider` builds
   the card with `build_plan(..., brief=True)`: a later step reads "Then remove empty rows from
   step 1's result." instead of "... from the result of step 1.", and the chain note reads "In
   order, each on the previous result. Only the final file is handed back." The first step's text
   is already as short as it gets and is unchanged. Same steps, same order, asked once. A field on
   the step (not a new argument of `expect_plan`) keeps every existing plan-aware provider valid.
4. **Small items.**
   - `HEAD /healthz` (`backend/routes.py`): a second route, `include_in_schema=False` (the
     committed `backend/openapi.json` is unchanged), same `healthz` bucket and no credential;
     it returns 200 with an empty body, the status `GET` gives.
   - The docker command in `scripts/measure_workbook_check.py`: the `scripts` mount is writable
     and the container runs as the caller's user. Importing the review harness creates an `out/`
     folder beside it, which the unprivileged `app` user couldn't do on a read-only mount. Not
     run here: this session has no container runtime.
   - `docs/backlog.md`: P9 refused on the VM; the 250,000-cell dedupe turn at about 135 s against
     the 180 s deadline (measure where the time goes before changing limits). Both figures are
     the owner's measurements; neither was re-measured in this session.

`docs/BUDDY_PERSONALITY.md` section 9 describes how it works in the product.

## On-disk formats

None changes. `user_state` lives on `Understanding`, which is not saved. `ConfirmationStep.brief`
is not saved. `PlanStep` and `PendingPlan` keep their fields (only the text differs). No migration
and no new fixture.

## Tests

- `tests/buddy/test_user_state.py` (new): Brain's vocabulary (every value, 10 kinds of other
  value, absent, spelling slips, a failed reading, the prompt carries the conversation and the
  state comes from the reply and not from "URGENT" in the message, `MentionCrossCheck` keeps it);
  `effective_mode`; and **13 scenarios x 3 speakers x calm, urgent, frustrated** through the real
  `build_engine`: delivered, delivered on a clean file ("nothing to remove"), chain, dedupe then
  report, partly done, greeting with and without a file, two clarifying questions (which action, general), no file,
  unsupported, refused file, Brain failed. For each, an urgent or frustrated reply equals the
  calm *focus* reply exactly (template: the calm reply), and holds the calm reply's numbers and
  file names in the same order; for a delivered turn the calm reply minus its opener and closer
  equals the urgent reply minus its closer, it starts with the outcome and has no friend phrase.
  Counts, the "I worked on" sentence, the values-only disclosure and "Partly done" with what
  wasn't done are asserted by text. A lint over every catalog text, greeting, question, failure
  reply and every frustrated reply checks there is no apology or feeling word. Hands marks the
  plan brief exactly for urgent and frustrated.
- `tests/backend/test_confirmation_provider.py`, `test_confirmation_api.py`: the brief card has
  the same steps, order and note facts; through the API an urgent and a frustrated person get
  the brief card for the same plan that then runs, and a calm one the usual card.
- `tests/backend/test_app_basics.py`, `test_ratelimit.py`: `HEAD /healthz` is 200 like `GET`
  with an empty body and the security headers, and spends the same bucket.

## Results

See the head of `docs/reviews/feat-urgency-tone-review.md` for the reviewer's run. In this
session: the full suite and `ruff check buddy tests backend` are compared with the base by file
and rule (no new lint errors).
