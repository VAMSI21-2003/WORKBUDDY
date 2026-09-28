# Proactive File Analysis — Design

**Status:** Design pass — proposal only. No implementation code has been
written. This document exists to be reviewed and revised before any of it
is built.

**What this is:** A design for a new, automatic capability — Buddy looks
at a freshly uploaded spreadsheet on its own, before or alongside whatever
the user actually asked for, and tells them what it noticed. This is the
"analyze first, then present findings and ask what to prioritize" behavior
already decided on as the intended personality scenario; this document is
the technical design for making that real.

**Inputs this design is required to take as settled, not re-litigate**
(from `scripts/spike_tools_test.py`'s real output against a realistic
messy spreadsheet — see the transcript from that spike for the actual
numbers):

1. ydata-profiling (4.18.4) reliably flags missing values per column and
   computes real per-column distribution stats, but **this version has no
   dedicated outlier alert type at all** (`ydata_profiling.model.alerts.AlertType`
   has no `OUTLIER` member — the closest indirect signal is `SKEWED`, which
   is not the same claim).
2. ydata-profiling does **not** reconcile inconsistent formats. A column
   with dates written six different ways came back classified as
   `UNIQUE`/high-cardinality text, not flagged as an inconsistency — the
   library never attempted to recognize it as a date column at all once
   the values stopped being uniform.
3. Splink (4.0.17), given a fuzzy name comparison and an exact phone
   comparison, correctly separated true duplicates (spelling variants,
   same phone: match_probability 0.21–0.28) from name collisions (same or
   near-identical name, different phone: match_probability ≈ 0.0000,
   match_weight ≈ -18 to -20) — including the hardest case, two rows with
   *identical* name text but different phones. It used the phone column
   correctly.

Everything below is designed around those four facts, not against them.

---

## 0. Where this sits, in one sentence

A new Read-tier Skill (`SpreadsheetAnalysisSkill`, capability
`spreadsheet.analyze`) runs once per newly uploaded spreadsheet,
unconditionally, ahead of whatever the user's own message asks for in the
same turn — riding entirely on infrastructure already built for a
different reason (`ExecutionResult.steps` / `CompositeVerifier`, added in
the CompositeVerifier batch to let one turn cover several capability
requests — see `docs/design/hands-verifier-review.md`), plus a small
number of precedented additive extensions (Section 5) — each one the
same shape as `ExecutionResult.steps` itself: a new field, defaulted so
every existing caller is unaffected, added only where something this
design needs genuinely doesn't exist yet.

---

## 1. Pipeline placement

### 1.1 The capability and Skill

**New capability:** `spreadsheet.analyze` — matches the existing
dot-separated `domain.action` convention (`spreadsheet.deduplicate`,
`spreadsheet.remove_empty_rows`). Scoped to spreadsheets only: every tool
this design uses (pandas, ydata-profiling, Splink) is tabular. A future
PDF/Word proactive pass would be a separate capability behind a separate
Skill — out of scope here, not something this design tries to generalize
to.

**New Skill:** `SpreadsheetAnalysisSkill`, living in `skills/excel/`
alongside `ExcelDeduplicateSkill` and `ExcelRemoveEmptyRowsSkill`, same
shape (`Skill` ABC, `SkillTask` in, `SkillResult` out).

- `permission_tier` → `PermissionTier.READ`. It never modifies the upload
  and — per the revised Section 5.2 — never writes any new file either,
  so it's genuinely Read-tier by the codebase's own established
  definition, not just by intent. (This was investigated, not assumed —
  see Section 5.4.) This matters directly: it's what lets analysis run
  with zero confirmation friction, consistent with "automatic, independent
  of the user's stated request." A Write-tier gate here would contradict
  the whole premise.
- `supported_inputs` → `("spreadsheet.analyze",)`.
- `can_handle` → same `.xlsx`/`.xls` extension check `ExcelDeduplicateSkill`
  already uses.
- Internally composed of four independent analyzers (Sections 2–4), not
  one monolithic function — see Section 6 for why and how.

### 1.2 Making it run unconditionally, ahead of the user's request

This is the part that doesn't yet exist anywhere in the pipeline: every
current `PlanningStrategy` (`RuleBasedPlanner`, `ClaudePlanner`,
`CompositePlanner`) only ever produces steps *for what the user asked*.
Nothing today looks at "was a file just uploaded" independent of intent
classification.

**Proposed: `ProactiveAnalysisPlanner`**, a decorator-shaped
`PlanningStrategy` — not a replacement for the existing
`CompositePlanner(RuleBasedPlanner(), ClaudePlanner(...))` chain, a wrapper
around it:

```python
class ProactiveAnalysisPlanner:
    """Prepends a spreadsheet.analyze step for every not-yet-analyzed
    spreadsheet upload, unconditionally, then defers to `inner` for
    whatever the user's own request calls for.
    """
    def __init__(self, inner: PlanningStrategy) -> None:
        self._inner = inner

    def __call__(self, request: PlanningRequest) -> tuple[PlanningStep, ...]:
        analysis_steps = tuple(
            PlanningStep(
                id=f"analyze-{i}",
                description=f"Look over {f} before doing anything else",
                capability="spreadsheet.analyze",
                priority=0,
            )
            for i, f in enumerate(request.unanalyzed_spreadsheet_files)
        )
        return analysis_steps + self._inner(request)
```

Because `Hands` executes `state.delegation` strictly in list order (it
does not consult `PlanningStep.priority` or `.dependencies` today —
`MechanicalDelegator` drops that information when it flattens steps into
`CapabilityRequest`s), prepending is sufficient on its own to guarantee
"analyze first." No dependency-tracking machinery needs to be added for
this to work correctly.

**What "not-yet-analyzed" needs, that doesn't exist yet:** `PlanningRequest`
today has no concept of "which uploaded files have already been looked
at." Proposed additions, both additive (existing callers unaffected,
same pattern `ExecutionResult.steps` was added under):

- `Context` gains `analyzed_files: frozenset[str]` (mirrors the existing
  `uploaded_files: list[str]` field exactly) and a `mark_analyzed(file)`
  mutator (mirrors `add_uploaded_file`).
- `PlanningRequest` gains `unanalyzed_spreadsheet_files: tuple[str, ...]`,
  computed by `Planner._build_request` as `uploaded_files - analyzed_files`,
  filtered to `.xlsx`/`.xls` (skip anything `SpreadsheetAnalysisSkill`
  couldn't handle anyway — never queue a step no Skill can perform).

**When does a file get marked analyzed?** Proposed: at **Inspector time**,
not Hands time — specifically, only once `SpreadsheetAnalysisVerifier`
(Section 5) has *independently confirmed* the analysis step actually
produced something real, not merely once the Skill self-reported success.
This is a deliberate echo of the "a Skill's self-report is necessary but
never sufficient" rule (`BUDDY_SKILLS.md` Section 9): if the analysis
step fails in a way the Skill's own `verify()` didn't catch, marking it
"analyzed" anyway would mean it silently never gets looked at again. Not
marking it lets the next turn retry. This means `Inspector._apply` grows
one small new responsibility (call `state.context.mark_analyzed(target)`
for each successfully-verified `spreadsheet.analyze` step) — the simpler
alternative, marking at Hands time right after a well-formed
`SkillResult`, is defensible too and is noted here as the fallback if the
team prefers not to couple Inspector to `Context`.

### 1.3 Wiring

In `buddy/engine_factory.py::build_engine`:

- `registry.register(SpreadsheetAnalysisSkill())`, alongside the two
  existing Excel skills.
- `Planner(planner=ProactiveAnalysisPlanner(CompositePlanner(RuleBasedPlanner(), ClaudePlanner(...))))`
  — wraps the existing chain rather than changing it.
- `CompositeVerifier({..., "spreadsheet.analyze": SpreadsheetAnalysisVerifier()})`.

Nothing about `Hands`, `Delegate`, `Dispatcher`, `Inspector`, or `Voice`
as *stages* changes — every one of them is already a thin, mechanical
runner over an injected, swappable collaborator (Sections 18/21 of
`WORKBUDDY_ARCHITECTURE.md`). This is exactly the "additive extensibility"
principle the architecture already commits to: a new capability is
supposed to be addable without touching the stages themselves.

---

## 2. Structural checks (ydata-profiling)

**What it's good for, per the spike:** missing-value detection and basic
per-column distribution stats. Use it for exactly that, nothing more.

**Proposed shape:**

- Call `ProfileReport(df, minimal=True, progress_bar=False)` — `minimal`
  disables correlations/interactions/sample rendering, none of which this
  use case needs, and keeps a proactive, must-run-on-every-upload check
  cheap.
- **Do not call `.to_file()` on every upload.** The spike's HTML report is
  useful for a human eyeballing it once; generating and persisting a full
  HTML report synchronously on every single upload is pure overhead for a
  check whose whole output is a handful of plain-language sentences. If a
  "give me the detailed report" capability is wanted later, that's a
  separate, explicitly-requested action, not something proactive analysis
  should pay for by default.
- Read `report.get_description()` (a `BaseDescription`) directly — its
  `.alerts` for `AlertType.MISSING` (per-column count + percentage, ready
  to use as-is, per the spike output), and its `.variables[column]` dict
  for basic distribution facts (count, `n_missing`, `mean`, `std`, `min`,
  `max` for numeric columns; type and cardinality for the rest). Reusing
  profiling's own already-computed stats object here — rather than a
  second, separate `df.describe()` call — means there's exactly one
  source of truth for "what does this column look like," matching the
  general principle of not re-deriving something a library you're already
  paying for already computed.

**Explicitly not attempted here:** outlier flagging (Section 3) and
date-format reconciliation (Section 4) — this section's analyzer produces
missing-value and basic-shape findings only.

---

## 3. Numeric outlier detection

**The choice, and why:** simple custom IQR logic, run as its own
independent analyzer over the same DataFrame — **not** wired into
ydata-profiling as an extension/plugin.

Both were considered:

- **Extending ydata-profiling itself** (hooking into its `Summarizer`/
  `describe_numeric_1d` machinery to add an outlier alert type) would mean
  depending on internal, undocumented APIs of a library that is *already*
  mid-deprecation in this exact version — the spike's own run printed:
  `` `import ydata_profiling` is deprecated ... install fg-data-profiling ``
  and use `data_profiling` instead. Coupling custom logic to internals of
  a library that's about to be renamed/restructured is a maintenance
  liability with no offsetting benefit, since nothing about outlier
  detection actually requires profiling's machinery.
- **Simple custom IQR logic** (`Q1 - 1.5*IQR`, `Q3 + 1.5*IQR`, flag values
  outside that band, per numeric column) is a well-understood, dependency-
  free technique. It's a few lines over a pandas Series, easy to explain
  to a user in plain language ("this amount is much higher than the rest
  of the column"), trivially unit-testable in isolation, and — critically
  — stays correct even if ydata-profiling is later swapped out or
  upgraded, since it never touches profiling's internals at all.

**Decision: simple custom IQR logic**, decoupled entirely from
ydata-profiling. Structural checks (Section 2) and outlier detection are
two independent analyzers that both happen to read the same DataFrame,
not one check depending on the other's internals.

**Concrete scope:** for every numeric column already identified by
Section 2's pass, compute Q1/Q3/IQR, flag values below `Q1 - 1.5*IQR` or
above `Q3 + 1.5*IQR`, and report per column: how many outlier values, and
what they are (bounded to a handful of examples, not every one, for a
wide column) — e.g. "2 unusually large values in `amount` (999,999.99 and
500,000.00)," matching the spike's own two planted outliers exactly.

---

## 4. Semantic/logical checks

No library covers this — everything here is custom pandas logic. Two
checks, both scoped concretely; one thing considered and explicitly
excluded.

### 4.1 Date-format consistency

**Signal that a column needs this check at all:** per the spike, a column
ydata-profiling did *not* classify as a native date/datetime type, but
whose name or sampled values look date-shaped, is itself the evidence of
inconsistency (`signup_date` came back `UNIQUE`/text, not `TYPE_DATE`,
precisely because the formats were inconsistent enough that pandas/
profiling's own date inference gave up on it).

**Concrete algorithm:**

1. For every column not already a native datetime dtype, attempt to parse
   each non-null value against a small, fixed list of explicit format
   strings: `%Y-%m-%d`, `%m/%d/%Y`, `%d/%m/%Y`, `%Y/%m/%d`, `%d-%m-%Y`,
   `%B %d, %Y`, `%b %d, %Y`, `%b %d %Y`. Record, per value, which format
   (if any) matched.
2. A column only qualifies as "date-like" if at least half its non-null
   values match at least one of these formats — this excludes ordinary
   text columns from being dragged into this check.
3. For a qualifying column: if the *set* of matched formats has more than
   one distinct member, or any non-null value matched none of them, flag
   it — reporting which formats were observed ("4 different formats:
   YYYY-MM-DD, MM/DD/YYYY, Month D, YYYY, and DD-MM-YYYY") and any
   outright unparseable values separately ("1 value that isn't a
   recognizable date at all: 'not a date'").

This is a cheap, per-column pass over data already loaded into memory —
no new dependency, pure pandas/`datetime.strptime`.

### 4.2 Values not reconciling — scoped concretely

The phrase "values not reconciling" is vague as stated, so pinning it
down to one specific, safe, generic check rather than something
open-ended:

**In scope — grouped-key conflict detection:** group rows by a candidate
identity column, and flag any group where another column takes on more
than one distinct non-null value. This is a plain `groupby(...).nunique()`
check — no domain knowledge of what the spreadsheet "means" is required,
just "the same identity shouldn't disagree with itself." It catches real
messy-data problems (someone's phone number updated in one row but not
another) without needing Splink's fuzzy-matching machinery, since it only
fires on *exact* key matches.

**Implementation note — narrowed from this section's original wording,
deliberately:** this section originally proposed falling back to a
combined key (its own example: `name`+`phone` together) "when no single
column looks like a stable key." The shipped `GroupedKeyConflictAnalyzer`
(`skills/excel/analyze.py`) drops that fallback and groups only on a
single email/phone/tel-like column (via `_KEY_COLUMN_PATTERN` —
`email|phone|tel`), never on `name` alone or in combination. Two reasons,
found once the code was actually written rather than assumed at design
time:

- **`name` is never safe as part of a grouping key.** Two different
  people sharing a name is exactly the scenario `NearDuplicateAnalyzer`
  (Section 4.3) exists to handle carefully, via Splink, precisely
  *because* an exact-match key can't tell them apart. Grouping by
  `name`+`phone` as a composite key would silently reproduce the same
  "can't tell two different people apart by name" problem for any row
  where the *other* half of the composite (here, `phone`) happens to
  differ too — which is the ordinary case, not an edge case.
- **The `name`+`phone` example doesn't add coverage over `phone` alone.**
  The doc's own example presupposes a `phone` column exists — but if it
  does, grouping by `phone` alone (what the shipped analyzer already
  does) catches every conflict grouping by `name`+`phone` together would,
  *and more*: two rows with the same phone but a different name are a
  conflict either way, but the composite key would put them in different
  groups (different `name` half) and miss it entirely, while single-column
  `phone` grouping still catches it. The fallback was strictly weaker than
  the case it was meant to strengthen, once actually worked through.

Net effect: single-column email/phone/tel grouping isn't a reduced
version of the original proposal — it's a corrected one. The genuinely
unhandled case (no email/phone/tel column at all, only `name` plus some
other column with no reliable per-person identifier anywhere in the
sheet) has no safe exact-match key to fall back to at all, so contributing
no finding in that case — the same "never guess" scoping Section 4.3
already applies to `NearDuplicateAnalyzer` — is the right behavior, not a
gap.

**Explicitly out of scope — arithmetic/derived-column reconciliation**
(e.g., confirming a `total` column equals the sum of line items, or
`amount == quantity * unit_price`). This would require guessing which
columns are semantically related to which — a Skill has no principled,
safe way to guess that (`BUDDY_SKILLS.md` Section 5: "fail loud, not
silent" — silently assuming a relationship that isn't there is exactly
the kind of guess this codebase's Skills are built not to make). If this
is wanted later, it needs an explicit, user-specified relationship to
check against, which makes it a different, requested capability — not
something proactive analysis should attempt unprompted.

### 4.3 Near-duplicate detection (Splink) — the fourth analyzer

Not asked for as its own numbered section, but the spike's second finding
was specifically about this, so it belongs in the same lineup as
Sections 2–4.1: a `NearDuplicateAnalyzer`, backed by Splink exactly as
validated in the spike — `NameComparison` (fuzzy) plus `ExactMatch` on a
discriminating column, two-blocking-rule EM training, `dedupe_only`.

**The one thing the spike's fixture doesn't have to solve, that a real
upload does:** the spike hardcoded which column was "name" and which was
"phone." A generic Skill, looking at an arbitrary uploaded spreadsheet,
has no such knowledge. Proposed scoping, in the same "never guess" spirit
as Section 4.2's exclusion:

- Identify an "identity-like" column via a simple header-name heuristic
  (matches `/name/i`) and a "discriminating" column the same way
  (`/phone|email|tel/i`).
- **If no such column pair is found, `NearDuplicateAnalyzer` contributes
  no findings at all** — it does not fall back to guessing which columns
  might be relevant. An honest "couldn't attempt this check" is
  consistent with every other Skill's stated behavior in this codebase;
  a wrong guess here (comparing the wrong columns) is worse than silence.
- Blocking rules: the same two-rule union validated in the spike (block
  on a name-column prefix, block on the discriminating column) — not the
  spike's earlier `"1=1"` cartesian-join exploration, which only worked
  because the spike's fixture was 24 rows. A real upload needs real
  blocking to stay fast.
- **Explicit scale guard:** skip (or cap) near-duplicate analysis above a
  fixed row-count threshold (proposed starting point: 5,000–10,000 rows)
  without a smarter blocking strategy than this design covers. This is a
  named, honest limitation, not a promise this scales unconditionally —
  in the same spirit as the two watch-items already flagged in
  `docs/design/enterprise-readiness-notes.md`.

---

## 5. Findings → Voice

### 5.1 The established path this rides on, and the one gap in it

Every existing `Verifier`
(`SpreadsheetDedupeVerifier`/`SpreadsheetEmptyRowsVerifier`, in
`buddy/inspector.py`) is **deliberately blind to anything the Skill said
about itself** — `VerificationRequest`/`ExecutionResult`/`StepResult`
don't even carry `SkillResult.metadata` or `.summary` through to the
Verifier. A Verifier re-opens the *output* file fresh and recomputes its
claim from scratch. This is the established, and correct, path for
findings to reach Voice: through `VerificationResult.findings` — set by
an independent check, never by trusting the Skill's own prose — which
`CompositeVerifier._aggregate_verdicts` already prefixes per capability
(`[spreadsheet.analyze] ...`) and concatenates across every step in the
turn, and which `TemplateSpeaker`/`PersonalityVoice` already read.

**The gap:** `SpreadsheetDedupeVerifier` re-derives its claim entirely
from the *output* file, because for dedupe/empty-rows the output *is* the
proof (no duplicates left, no empty rows left). An analysis check is
different: verifying it means re-deriving facts about the *original
upload*, not a cleaned copy of it — and today's `ExecutionResult`/
`StepResult` shape has no path back to the original file at all (only
`outputs`, "produced by the skill," which the original wasn't).

**Proposed fix — the one small additive extension this design needs:**
`VerificationRequest` gains `active_file: str | None`, populated by
`Inspector._build_request` from `state.context.active_file` — the exact
same "pull straight from `state.context`" pattern `Delegate._build_request`
and `Planner._build_request` already use for the same field. Existing
Verifiers ignore the new field entirely (default `None`, unused);
`SpreadsheetAnalysisVerifier` is the first to read it.

### 5.2 `SpreadsheetAnalysisSkill` / `SpreadsheetAnalysisVerifier` shape

**Two different verification treatments for two different kinds of
claim, stated explicitly:**

- **Fully re-derived, independently (missing values, outliers,
  date-format consistency):** `SpreadsheetAnalysisVerifier` does not
  trust the Skill's numbers for these three at all. Using the new
  `active_file` (Section 5.1) to open the *original* upload fresh, it
  runs its own copy of the missing-value count, the IQR outlier check
  (Section 3), and the date-format-consistency check (Section 4.1) —
  completely independently of whatever `SpreadsheetAnalysisSkill`
  computed. This is not a spot-check or a sample cross-reference; it's
  the exact same "deliberately blind to what the Skill said, recompute
  from scratch" discipline `SpreadsheetDedupeVerifier`/
  `SpreadsheetEmptyRowsVerifier` already apply — justified here because
  all three checks are cheap, dependency-free pandas/`datetime` logic
  (Sections 2–4.1 are deliberately built that way), so full independent
  recomputation costs almost nothing and gives a genuine second opinion,
  not a token one. *(Superseded (chaining): the verifier now reads the
  file the step actually analyzed, `StepResult.input_reference`, falling
  back to `active_file` only for older results. That's the original for
  the proactive step and the final file for its re-check; see
  `docs/design/multi-step-planner-design.md`, "In-loop verification, the
  final-file re-check, and the Voice safeguards".)* The Skill's own copy of these three checks still runs
  (it needs *something* to build `SkillResult.summary` and its own
  `verify()` self-check from), but nothing the Skill says about them
  reaches `VerificationResult.findings` directly — only the Verifier's
  own recomputation does.
- **Self-reported, not independently re-run (near-duplicates):** Splink's
  near-duplicate detection is genuinely expensive and non-trivial to
  redo (EM training, blocking rules, a fuzzy comparison model) — and
  critically, re-running the *identical* algorithm a second time inside
  the Verifier would not be independent verification at all, just the
  same computation paying for itself twice. There is no cheap, different
  technique that could stand in as a "second opinion" the way IQR/date-
  parsing can for the other three checks. So near-duplicate findings are
  carried through as the Skill's own self-report, explicitly labeled as
  such rather than silently presented with the same confidence as the
  fully-independent ones.
  - **Mechanism:** `SkillResult` gains a new field,
    `self_reported_findings: tuple[str, ...] = ()` — populated by
    `NearDuplicateAnalyzer`'s output specifically. `Hands` copies it
    across onto a mirrored new field on `StepResult` (the same 1:1
    field-copy it already does for `status`/`outputs`/`summary`), so it
    reaches `CompositeVerifier`'s per-step scoping (`_scoped_execution_result`
    already includes the full `StepResult` via `steps=(step,)`) without
    needing any file to be written and re-opened.
  - `SpreadsheetAnalysisVerifier` passes these straight into
    `VerificationResult.findings`, unmodified, at a capped, honest
    confidence (lower than the fully-re-derived findings' confidence —
    see Section 5.5 for how confidence is now tracked per-step rather
    than only in aggregate) and does not claim to have checked them.

**No file write, anywhere in this Skill.** The JSON-artifact idea from
an earlier pass of this design is dropped: once the three cheap checks
are fully re-derivable straight from the original upload (no need to
read anything the Skill wrote) and the one expensive check is carried
via `self_reported_findings` (an in-memory field, not a file), there is
nothing left for the Skill to write to `workspace/outputs/` at all. This
turns out to matter for more than tidiness — see Section 5.4.

`passed` is `True` whenever the analysis itself ran and every
fully-re-derived check the Verifier attempted came back consistent with
what the Skill reported — there's no pass/fail "intent" being judged
here, only "did this check actually happen and hold up." A failed read
of the upload, a Skill-reported failure, or a mismatch between the
Skill's and the Verifier's own numbers on a fully-re-derived check is
what drives `passed=False` for this capability.

### 5.3 Delivering it as "explain warmly, ask what to prioritize," not pass/fail

`TemplateSpeaker`'s existing `_success_message`/`_failure_message` split
is a binary the analysis findings don't fit — there's no request being
judged as satisfied or not. Proposed handling, in both speakers:

- **`TemplateSpeaker`:** add one dedicated branch — when
  `execution_result.steps` contains a `spreadsheet.analyze` step, pull its
  findings (via the `[spreadsheet.analyze]`-prefixed entries in
  `verification_result.findings`) and render them as a leading
  "Before anything else, here's what I noticed: ..." sentence, followed
  by the ordinary success/failure message for whatever other step(s) ran
  in the same turn, if any.
- **`PersonalityVoice`:** no code change to the prompt-building mechanism
  — just new guidance added to both `FRIEND_MODE_PROMPT` and
  `FOCUS_MODE_PROMPT` telling the model that facts prefixed
  `[spreadsheet.analyze]` are an unprompted observation, not an outcome to
  report pass/fail on: lead with them, phrase them warmly (Friend) or
  tersely (Focus), and close with a question about which one to tackle —
  directly applying `BUDDY_PERSONALITY.md` Section 12's existing
  clarifying-question style ("Do you want this as a summary... or the
  full detailed data too?") to this new situation, not inventing a new
  personality rule.
- **Don't ask when the answer is already obvious — but "obvious" means the
  same capability, not just a related one.** A finding is folded into "I
  also handled X while I was in there" only when this same turn actually
  ran the step whose capability *specifically* resolves that finding —
  never merely a step that sounds related. Concretely: a
  `spreadsheet.deduplicate` step running in the same turn does **not**
  resolve a near-duplicate finding from `NearDuplicateAnalyzer` —
  `spreadsheet.deduplicate` is exact-match only
  (`ExcelDeduplicateSkill.execute()` calls
  `pandas.DataFrame.drop_duplicates()`, a full-row exact match); a fuzzy
  "these two rows look like the same person, spelled slightly
  differently" finding is never something an exact-match dedupe pass
  touches, no matter how related the two capabilities sound. The same
  goes for a `GroupedKeyConflictAnalyzer` finding ("this email has two
  different phone numbers on file") — nothing about running
  `spreadsheet.deduplicate` or `spreadsheet.remove_empty_rows` addresses
  it either. Folding a finding in anyway would tell the user something
  was fixed that wasn't. The rule is: fold a finding into "already
  handled" only when a step whose capability is the one that finding's
  own analyzer maps to (`spreadsheet.analyze`'s own sub-checks have no
  fixing capability at all today — every one of their findings stays
  open unless a *separate*, matching capability happens to exist and ran)
  ran and passed in the same turn; otherwise it stays on the list of
  things to ask about. (This requires knowing, per step, whether it
  specifically passed — which `VerificationResult` doesn't expose today;
  see Section 5.5.)

### 5.4 Investigation: does "writes any new file" already mean WRITE tier here?

**Checked directly against the code, not assumed.** `skills/base.py`'s
`PermissionTier` docstring reads: "read-only work requires no
confirmation, work that writes within the workspace requires confirmation
before overwriting an original, and work with an effect outside the
workspace always requires explicit confirmation" — language that leaves
room for a "first write of a brand-new file, never touching the original"
case to plausibly still count as low-friction. But the one real precedent
in this codebase says otherwise: `ExcelDeduplicateSkill` — which **never**
opens its input with write intent and **never** touches the original
upload, only ever writes a fresh path under
`workspace/outputs/{workspace_id}/` — still declares
`permission_tier -> PermissionTier.WRITE` (`skills/excel/dedupe.py`).
`PermissionTier` itself is a flat three-value enum with no "internal
artifact vs. user-facing deliverable" case, and `Hands`'s
`_CONFIRMATION_REQUIRED_TIERS = (PermissionTier.WRITE, PermissionTier.EXTERNAL_EFFECT)`
gates on WRITE with no carve-out for "first write of a new file" vs.
"overwrite." **So yes: in this codebase's actual, established practice —
not just its philosophical table — writing any new file at all means
WRITE tier, full stop, regardless of whether the original is touched.**

That set up a real conflict for the *original* version of this design
(Section 5.2, before this revision): it had `SpreadsheetAnalysisSkill`
write a small JSON findings artifact, which — per the precedent above —
would make it WRITE tier. That's not free today (`NullConfirmationProvider`
approves everything, so nothing breaks *yet*), but it would be a live
problem the moment a real `ConfirmationProvider` exists: every WRITE-tier
task pauses for a yes/no per `Hands.__call__`'s existing gate, which would
mean asking the user "may I write this analysis file?" *before* even
telling them what it found — directly defeating the "runs automatically,
independent of the user's stated request" premise this whole design
exists to satisfy. This isn't hypothetical friction to wave away: real
confirmation arriving with the auth/backend phase is already a named
future watch-item in `docs/design/enterprise-readiness-notes.md`.

**Two ways to resolve it, weighed:**

1. **Invent a new, principled `PermissionTier` distinction** ("internal
   artifact" vs. "user-facing deliverable"), so a Skill can write a file
   without triggering confirmation. Rejected: this adds a new permission
   concept to a codebase that currently has exactly three, flat and
   simple, for one caller's benefit — the kind of premature abstraction
   this project's own conventions ask not to add "beyond what the task
   requires." It would also need real design work of its own (how does
   `Hands` distinguish the two kinds of write? does every future Skill
   need to classify each output file it produces?) that this document
   has no concrete need to do.
2. **Make the premise (a file write) not true in the first place.**
   Section 5.2's revision — three checks fully re-derived directly
   against `active_file`, the fourth (near-duplicates) carried via the
   new in-memory `SkillResult.self_reported_findings` /
   `StepResult.self_reported_findings` fields — means
   `SpreadsheetAnalysisSkill` never writes to `workspace/outputs/` at
   all. With zero file writes, it isn't "creating or modifying files
   within the workspace" (`BUDDY_SKILLS.md` Section 13's own definition
   of Write tier) by any reading of that definition, established
   precedent included. No new tier concept is needed because the thing
   that would have required one no longer happens.

**Resolution: (2).** `SpreadsheetAnalysisSkill` stays `PermissionTier.READ`
— not by asserting an exception to the codebase's established WRITE-tier
convention, but because the revised design genuinely never writes a file,
so that convention simply doesn't apply. This is a better outcome than
(1) on its own terms too: a Skill needing zero new PermissionTier
machinery to run automatically is a stronger design than one that needs a
new confirmation-bypass rule carved out for it.

### 5.5 Investigation: does `VerificationResult` retain per-step pass/fail?

**Checked directly against `buddy/inspector.py`.** `CompositeVerifier
._aggregate_verdicts` collapses every step's verdict into **one** boolean
and **one** confidence float:

```python
return VerificationResult(
    passed=all(result.passed for _, result in per_step),
    confidence=min(result.confidence for _, result in per_step),
    findings=tuple(f"[{capability}] {finding}" ... ),
    recommendations=tuple(...),
)
```

`findings`/`recommendations` do retain per-step *text*, tagged with a
`[capability]` prefix — so a human reading the aggregate can tell which
step said what. But there is no structured field anywhere on
`VerificationResult` that says, programmatically, "did capability X's own
step specifically pass." Nothing today lets code ask that question
without parsing prefixed strings. **Confirmed: aggregation collapses to
one bool (and one confidence), full stop — the per-step text survives,
the per-step verdict does not.**

This isn't an abstract gap — Section 5.3's corrected "fold into already
handled" rule needs exactly this: to decide whether a `spreadsheet
.analyze` finding should be presented as "already handled" or "still
needs a decision," Voice needs to know whether a *specific other
capability's* step *specifically passed* in this same turn, not just
whether the turn as a whole passed.

**Proposed smallest additive fix**, mirroring the exact precedent
`ExecutionResult.steps`/`StepResult` already set (see
`docs/design/hands-verifier-review.md` Sections 2–3, where this problem —
"the data a new dispatch/aggregation need doesn't exist yet" — was solved
by adding one new frozen dataclass and one new field, with every existing
field, consumer, and shape left untouched):

```python
@dataclass(frozen=True)
class StepVerification:
    """One step's own, unaggregated verification verdict."""
    capability: str
    passed: bool
    confidence: float
    findings: tuple[str, ...] = ()
    recommendations: tuple[str, ...] = ()

@dataclass(frozen=True)
class VerificationResult:
    passed: bool
    confidence: float = 0.0
    findings: tuple[str, ...] = ()
    recommendations: tuple[str, ...] = ()
    steps: tuple[StepVerification, ...] = ()   # NEW — per-step, unaggregated
```

`_aggregate_verdicts` gains one line — building `steps` from the same
`per_step` sequence it already has in hand — alongside the existing
flattened fields, which stay exactly as they are today for every current
consumer (`TemplateSpeaker`, `PersonalityVoice`, and anything else reading
`.passed`/`.confidence`/`.findings` directly is unaffected). Voice's new
`spreadsheet.analyze`-aware branch (Section 5.3) is the first consumer of
`.steps` — it looks up whether a `VerificationResult.steps` entry for a
given capability exists with `passed=True` before deciding whether to
fold a finding in as "already handled."

---

## 6. Why one Skill, four analyzers — not four Skills

`BUDDY_SKILLS.md` Section 3's single-responsibility rule is about domain
boundaries, not technique count: these four checks (missing/distribution,
outliers, format consistency + grouped-key conflicts, near-duplicates) are
four techniques inside one responsibility — "read-only quality analysis of
an uploaded spreadsheet" — not four different domains. Splitting them into
four Skills would mean four separate capability requests, four separate
`StepResult`s, and four separate Verifier entries for what the user
experiences as a single "here's what I noticed" moment.

Proposed internal shape (internal to the Skill — not a new Buddy-core
protocol, so it doesn't need Registry/Dispatcher awareness):

```python
class Analyzer(Protocol):
    def __call__(self, df: pd.DataFrame) -> tuple[str, ...]:
        """Return this analyzer's plain-language findings for df."""
        ...
```

`SpreadsheetAnalysisSkill.execute()` runs `MissingValueAndDistributionAnalyzer`,
`IQROutlierAnalyzer`, `DateFormatConsistencyAnalyzer`,
`GroupedKeyConflictAnalyzer`, and `NearDuplicateAnalyzer` in turn. Per
Section 5.2, the first four's findings feed only `SkillResult.summary`
and the Skill's own `verify()` self-check — `SpreadsheetAnalysisVerifier`
fully recomputes those independently and never reads the Skill's copy.
Only `NearDuplicateAnalyzer`'s findings additionally go into the new
`SkillResult.self_reported_findings`, since that's the one category
carried through as-reported rather than re-derived. No file is written
(Section 5.2/5.4). This is the same swappable-strategy idiom already used
everywhere else in this codebase (`Delegator`, `PlanningStrategy`,
`Verifier`, `Speaker`, `ConfirmationProvider`) — a sixth analyzer becomes
addable later without touching the driver loop.

---

## 7. Explicitly out of scope for this design

- Non-spreadsheet proactive analysis (PDF, Word) — no Skill exists for
  either yet; this design doesn't try to generalize past what
  ydata-profiling/Splink/pandas can do.
- Making the analysis step asynchronous / non-blocking. Sprint 1 runs the
  whole cognitive cycle as one synchronous pass
  (`WORKBUDDY_ARCHITECTURE.md` Section 16: "how Buddy handles... is
  intentionally left for a future sprint"), and this design doesn't
  change that. For a large upload, this means the first reply after
  upload could be noticeably slower than today — a real cost, named here
  rather than hidden, and left for whenever long-running-task handling is
  actually designed.
- Arithmetic/derived-column reconciliation (Section 4.2).
- Any UI/UX for the frontend to render "findings" richly (chips, a
  checklist to tick off priorities) — that's a future-sprint frontend
  concern; this design only goes as far as the plain-text reply Voice
  already produces today.
- **Analyzing more than one file per turn.** `Planner._resolve_unanalyzed_spreadsheet_files`
  (Section 1.2) deliberately narrows analysis to the turn's single
  resolved target file, even when several unanalyzed spreadsheets sit in
  `uploaded_files` — a direct consequence of `MechanicalDelegator`
  resolving one shared target for the whole plan today. A user who
  uploads several spreadsheets in one turn gets only the one Buddy would
  have acted on anyway analyzed; the others wait until a later turn makes
  one of them the active file. Accepted for now, not a silent gap: fixing
  it properly means teaching `MechanicalDelegator` (or its successor) to
  resolve a target per step rather than once per turn, which is a
  multi-step-delegation change well beyond this design's scope — a
  follow-up for whenever multi-file turns themselves are designed for,
  not something to special-case inside proactive analysis alone.

  > **Superseded (chaining), in part.** `MechanicalDelegator` still
  > resolves one target per turn, but steps no longer all *read* it:
  > file-changing steps chain, each reading the previous step's output,
  > and read-only analysis reads the original and is re-run on the final
  > file. Analyzing several uploaded files per turn is still unaddressed.

---

## 8. Summary of new/changed pieces, for a future implementation pass

- **New:** `skills/excel/analyze.py` — `SpreadsheetAnalysisSkill` +
  `Analyzer` protocol + 5 analyzer implementations.
- **New:** `SpreadsheetAnalysisVerifier` in `buddy/inspector.py`.
- **New:** `ProactiveAnalysisPlanner` in `buddy/planner.py`.
- **Additive:** `Context.analyzed_files` + `Context.mark_analyzed()` in
  `buddy/context.py`.
- **Additive:** `PlanningRequest.unanalyzed_spreadsheet_files` in
  `buddy/planner.py`.
- **Additive:** `VerificationRequest.active_file` in `buddy/inspector.py`
  — now load-bearing for three of the four analyzers (Section 5.2/5.4),
  not just a nice-to-have.
- **Additive:** `SkillResult.self_reported_findings: tuple[str, ...] = ()`
  in `skills/base.py`, mirrored onto a new
  `StepResult.self_reported_findings: tuple[str, ...] = ()` in
  `buddy/models.py`, copied across 1:1 by `Hands` the same way
  `status`/`outputs`/`summary` already are (Section 5.2).
- **Additive:** `StepVerification` dataclass + `VerificationResult.steps:
  tuple[StepVerification, ...] = ()` in `buddy/models.py`, populated by
  `CompositeVerifier._aggregate_verdicts` alongside the existing
  flattened fields (Section 5.5).
- **Small addition:** `Inspector._apply` marks successfully-verified
  `spreadsheet.analyze` steps as analyzed via `state.context`.
- **Small addition:** one new branch in `TemplateSpeaker`, one prompt
  addition in `FRIEND_MODE_PROMPT`/`FOCUS_MODE_PROMPT`, both now reading
  `VerificationResult.steps` to decide whether a finding is already
  handled (Section 5.3/5.5).
- **Wiring only, no new stage-level code:** `buddy/engine_factory.py`
  registers the new Skill, wraps the existing planner chain, and adds one
  entry to `CompositeVerifier`'s mapping.
- **No new file-write path, no new `PermissionTier` value** — investigated
  and deliberately not added (Section 5.4).
- **New dependency status:** `ydata-profiling`/`splink` move from
  requirements.txt's "SPIKE ONLY" section to real dependencies, once this
  design is approved and implemented.

No implementation has been done against this document.
