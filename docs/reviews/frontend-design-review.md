# Review: frontend design (`design/frontend`, `docs/design/frontend-design.md`)

Review only. No code was changed. This file is the only file written or committed.

> **Reviewer independence.** I didn't write the frontend design or the backend API design, and I have no memory of either being written. I reviewed the design as written, against `CLAUDE.md` rule 1, and against the demo's real constraints: a solo developer, a public link, free hosting and minimal cost.

## 0. Method

- **Documents.** `docs/design/frontend-design.md` at `origin/design/frontend` (`fecafc3`), read in full. `docs/design/backend-api-design.md` at `origin/design/backend-api` (`dc2bf88`), read in full, including "Decisions (2026-09-30)". I also read `docs/backlog.md` and `docs/reviews/verified-outcome-a-review.md` for the pipeline's known rule-1 gaps.
- **Code checked, where the design depends on pipeline behaviour:**
  - `buddy/hands.py:208`: `input_reference = chain_input or request.target`. A chain step reads the previous step's output, not the upload.
  - `buddy/hands.py:240-252`: a task's permission tier is only known after dispatch. `CapabilityRequest` (`buddy/models.py:74`) has no tier.
  - `buddy/outcome.py` `lead_word`: "Done" comes from Brain's single classified intent.
- **Not done:**
  - Nothing is prototyped, so every line-count comparison below is an estimate.
  - I couldn't verify Hugging Face Spaces' terms: `huggingface.co` is blocked by this session's network policy. Hosting claims are tagged with how sure I am.
  - No live runs. This is a design review, and there's no UI to run yet.

Tags: **[Certain]** means checked in code or text. **[Likely]** is a strong inference. **[Possible]** is plausible but unverified.

## 1. Summary

| ID | Severity | Section | Finding |
|---|---|---|---|
| **H1** | **High** | 1.3, 1.9, 6 | Client state isn't scoped to a session. After a `404 session_not_found`, the old transcript, download buttons and details cache can survive. Turn ids restart at `t0001` in the new session, so a new rejected turn can show an old turn's steps or files. |
| **H2** | **High** | 1.6, 1.10, 14 | Polling every 1 s uses the whole 60 requests/min/IP budget from the backend design. What a poll does on `429` isn't specified. Rate limiting will routinely produce the "I don't know how it ended" state, or hide a plan card until it times out. It's unresolved between the two designs. |
| **H3** | **High** | 13 | The go-live gate leaves out known rule-1 breaches that the public link will hit: multi-sheet workbooks (backlog 1a / M3: "Your cleaned copy … is ready" while sheets were dropped), and "Done —" on a misclassified request (M2, deferred to PR-B). Rendering the reply verbatim doesn't protect against these. |
| M1 | Medium | 1.9 | The "What happened" panel shows `status: success` and `verified: true` beside a reply that says nothing could be confirmed (Inspector raised after output, or `integrity_failure`). The heading presents raw data, including a skill's self-reported status, as what happened. |
| M2 | Medium | 1.7 (and backend 1.5) | Plan text for a chain misdescribes the work. Step 2 says "from report.xlsx" but actually runs on step 1's output, and each step says "save the result as a new file" although only the final file is handed back. The design's own mockup shows this. |
| M3 | Medium | 1.7 | Nothing stops the Approve button from rendering when `steps` is missing. The backend keeps a step-1-only `prompt` "for compatibility". A user could approve a whole plan after seeing only step 1. |
| M4 | Medium | 1.7 | The countdown and the "Approval window closed" text come from the client's clock. A network failure on the confirmation POST isn't handled. An out-of-order poll can bring back an answered card. |
| M5 | Medium | 1.3, 1.6 | UI-written copy in the first person ("I couldn't reach the server… I don't know how it ended") sits in Buddy's reply position. Readers will attribute it to Buddy, and Buddy may in fact have finished and delivered. |
| M6 | Medium | 4.3 | The banned-strings check gives false confidence. It misses reworded claims, CSS, `index.html`, icon components and outcome-keyed class names (Section 3.2). |
| M7 | Medium | 11.3 | Hugging Face Spaces: a public Space [Likely] publishes the code of a `Proprietary` project. The disk is [Likely] ephemeral, so the backend's restart repair doesn't apply, contrary to the design's claim. The code-visibility question is a go/no-go decision for the owner, not a caveat. |
| L1 | Low | 1.3 vs 10.1 | `session_id` goes in `localStorage`, but the gate test asserts nothing is stored there. It's also a privacy leak on shared machines, since everyone shares the one demo password. |
| L2 | Low | 1.6, 1.10 | The ambiguous-POST rule doesn't cover a `5xx` after the turn was created, a failed refetch, or another tab's turn being taken as this one. |
| L3 | Low | 1.8 | The downloaded bytes aren't checked against the API's `sha256`, although that's cheap to do. |
| L4 | Low | 1.6 | Polling pauses in a hidden tab, so a plan card can appear and time out unseen. The result is honest, but it wastes the user's turn. |

**Overall:** the rule-1 core is sound. Section 4.1's rules are right: the reply is shown verbatim, `outcome` only controls layout, downloads come only from `delivered_files` and only when `delivered`, text is rendered as text only, and nothing optimistic appears on Buddy's side. No path I found makes the UI invent a completion claim within one session. The high findings are about state across sessions, the rate-limit conflict, and a deployment gate that ignores rule-1 breaches the UI can't fix. Separately, the stack and the PR split are too heavy for the goal (Sections 3.5 and 3.6).

---

## 2. Findings

### H1 (High): state isn't scoped to the session, and turn ids collide across sessions

**Section:** 1.3 (session lifecycle), 1.9 (lazy details), 6 (state), 1.10 (`404 session_not_found` row).

**What the design says:** on `404 session_not_found` the UI "starts a new session and shows a static notice". It doesn't say that the transcript, file list and lazily fetched `detail` are cleared. Its reducer holds `turns`, and details are fetched "on first open".

**Why it matters:** backend turn ids are sequential per session (`t0001`, `t0002`, backend 3.3), and delivered-file ids are `d_{turn}_{n}`. Every new session reuses `t0001` and `d_t0001_1`.

**Failure scenario:**
1. A user in session A runs a turn `t0001` that delivers. They open "What happened", which caches `steps: [{status: success, verified: true, output_names: [report_deduplicated_….xlsx]}]`.
2. The Space restarts (ephemeral disk, M7) or the 24 h sweep runs. The user's next action gets `404 session_not_found`, and the UI creates session B.
3. If the old transcript isn't cleared, A's reply and its **Download** button stay on screen. The design itself calls that button "a claim that a file exists" (4.1.4). The file no longer exists.
4. In session B the user sends a request and **rejects** the plan. B's turn is also `t0001`. A details cache or a download list keyed by `turn_id` shows A's successful steps and output name under B's rejected turn. A poll for A's `t0001` that was already in flight at reset time can land on B's `t0001` in the same way.

That is the UI showing a result for a rejected turn, which the API never said.

**Fix:**
- Key every per-turn and per-file piece of state by `(session_id, turn_id)` / `(session_id, file_id)`.
- On any change of `session_id`, reset the reducer to empty, keeping only the draft and the password.
- Drop any response whose request was made for a different `session_id` than the current one.
- Tests:
  - a `404` reset clears the transcript, the downloads and the details;
  - B's `t0001` never renders data fetched for A's `t0001`;
  - a late poll for A is ignored after the reset.

### H2 (High): polling and the per-IP rate limit are in direct conflict

**Section:** 1.6 (poll every 1 s), 1.10 (`429` rows), 14 (the design flags the conflict but doesn't resolve it). Backend "Decisions" item 1 (60 requests/min/IP).

**Failure scenarios:**
1. **One user.** One tab polls 60 times a minute. Any other request (the confirmation POST, a details fetch, a download) exceeds the budget. The design doesn't say what a poll does on `429`. If it counts as a "failed poll", five in a row end in "I don't know how it ended" on a turn that is running normally.
2. **Awaiting confirmation.** The polls that would reveal `awaiting_confirmation` are the ones being throttled. The user may never see the plan card before the 120 s timeout, and the timeout counts as a reject.
3. **Shared IP.** At a meetup or on office Wi-Fi, everyone shares one IP. On the host, if `WORKBUDDY_TRUSTED_PROXY_HOPS` is wrong, everyone shares the proxy's IP. Then one polling user throttles all users.

**Fix (both designs, before the chat PR):**
- Poll every 2 s, and every 3 s after 20 s. A turn takes tens of seconds, so 1 s buys nothing visible.
- A `429` on a poll waits `Retry-After`, is **not** counted as a connection failure, and doesn't change the row's text.
- The backend counts `GET …/turns/{id}` for the session's own unfinished turn in a separate, higher bucket (or exempts it), and keeps the strict limits on mutating routes and turn creation.
- The PR-4 tests assert the hop configuration for the chosen host.
- Add an e2e case: one full turn at the production limits never shows the unknown-outcome state.

### H3 (High): the go-live gate omits rule-1 breaches the public link will hit

**Section:** 13, last paragraph ("Deployment happens only after backend PR-1 through PR-4 and FE-1 through FE-4").

**Facts:** backlog item 1a (M3, high) is open. Dedupe and remove-empty-rows keep only the first sheet, and the reply says "Your cleaned copy of report.xlsx is ready." Backlog item 1 (PR-B) is open: M2 in `verified-outcome-a-review.md` shows that "Done —" is derived from Brain's single intent, so a misclassified request still gets "Done —" for work the user didn't ask for (`buddy/outcome.py` `lead_word`).

**Failure scenario:** a member of the public uploads a real two-sheet workbook ([Likely] common in real spreadsheets) and asks to remove duplicates. The UI faithfully shows "Your cleaned copy of report.xlsx is ready." with a Download button, and the delivered file has lost sheet two. The frontend kept every one of its own rules, and the product still made a false claim to a stranger.

**Fix:** add to Section 13's preconditions:
- **(a) Multi-sheet.** Either backlog 1a is closed, or a stopgap: the backend refuses a multi-sheet `.xlsx` at upload with `422 invalid_file` and a fixed message. The stopgap counts the `xl/worksheets/*.xml` entries in the ZIP directory, which the zip-bomb guard already reads, so no parsing is needed. Until sheets can be counted for `.xls`, leave `.xls` out of the demo, which also removes the untested `.xls` path (backend Decisions item 4).
- **(b) PR-B.** An explicit decision from the user to go public with M2 / PR-B still open, written in the backend Decisions section. That is the user's call, not the implementer's.

### M1 (Medium): the details panel presents unverified data as "What happened"

**Section:** 1.9.

**Failure scenarios:**
- In backend 7.3 case 3, Inspector raises after Hands produced a real output. The `outcome` is `failed`, and the reply says nothing could be confirmed. If `detail` is built for that turn, the panel shows step 1 with `status: success` next to it.
- In an `integrity_failure` turn, the steps can be `success` / `verified: true` while the reply says "I can't vouch for this result."
- In both cases a panel labelled "What happened" contradicts the reply. A step's `status` is also the skill's **self-report** (`StepResult.status`), exactly the kind of unverified claim the pipeline works to keep out of replies.

**Fix:** drop the panel from the demo. It isn't needed to use the product, and the live-run reports read the API directly. If it's kept:
- label it "Technical details (raw pipeline data)";
- head the status column "Skill reported";
- show `verified` before it;
- don't render it for `failed` and `integrity_failure` turns;
- add a component test for both of those outcomes.

### M2 (Medium): plan-card text for a chain misdescribes the work

**Section:** 1.7 and the mockup in 1.3. The text comes from backend 1.5 (the prompt template filled with `input_name`).

**Facts:** [Certain] step 2 of a chain reads step 1's output (`buddy/hands.py:208`), and only the chain's final file is handed back (`CLAUDE.md`). [Certain] `spreadsheet.analyze` (READ tier) runs on the original whether or not the plan is approved. It isn't in the plan.

**Failure scenario:** the design's own mockup shows:
1. Remove duplicate rows from report.xlsx
2. Remove empty rows from report.xlsx

With the template's "…and save the result as a new file." on each line, the user reasonably expects two new files, one per operation, both from the original. They get one file with both operations applied. The UI shows this text verbatim, so the UI isn't at fault, but it's rule-1-adjacent: the user approves one thing and receives another, and a reply saying "one file" then reads as a contradiction.

**Fix (backend PR-3 template, then the FE mockup):**
- Steps after the first read "Then remove empty rows from the result of step 1."
- The backend adds one fixed plan-level line (so the UI still writes nothing): "The steps run in this order, each on the previous step's result. Only the final file is handed back."
- No per-step "save as a new file".
- Don't add a promise such as "report.xlsx won't change". It's true by design, but it's a claim about the future that the card can't verify.

The plan card is also useful for rule 1: with N1 open, it is the only place a user can see "Remove empty rows" when they asked about duplicates, before anything runs. Keep it prominent.

### M3 (Medium): Approve can be shown without the whole plan

**Section:** 1.7 (renders from `pending_confirmation.steps`, ask 1).

**Facts:** backend 1.5 keeps top-level `capability`, `permission_tier` and `prompt` "describe the first step for compatibility", and approval "covers every step of that turn's plan".

**Failure scenario:** `steps` is absent (ask 1 not implemented yet, or a backend regression) or empty. An implementation that falls back to the top-level `prompt`, which is natural given the compatibility field, shows one step. The user approves, and two write steps run.

**Fix:**
- The UI renders **Approve** only when `steps` is a non-empty array.
- Otherwise it shows only **Reject**, plus fixed text: "The server didn't send the plan, so it can't be approved here."
- Add a component test for that case.
- Ask the backend to drop the compatibility fields from the public schema, since nothing else reads them.

### M4 (Medium): the approval window depends on the client's clock, and answer failures aren't handled

**Section:** 1.7.

**Failure scenarios:**
- **Clock skew.** The client's clock is 3 minutes fast. The card says "Approval window closed" as soon as it appears, while the server is still waiting. That is a false statement about server state. If the buttons disable at client expiry, the user can't approve, and the turn times out as a reject.
- **Lost POST.** The user clicks Reject and the network drops the POST. The design doesn't say what happens. If the buttons re-enable, the user can't tell whether the reject landed.
- **Stale poll.** A poll sent before the click arrives after the `200` and re-renders the card as awaiting, with its buttons live.

**Fix:**
- The backend sends `expires_in_seconds` (or the server's `now`), and the UI shows "about N s left" from that.
- The UI never disables Approve or Reject on its own clock. Only a server state change or a `409` removes the card.
- On a network failure of the confirmation POST, keep both buttons disabled, refetch the turn, and show the card again only if the **same** `confirmation_id` is still pending, with the fixed line "Your answer may not have reached the server."
- Once a `confirmation_id` has been answered locally, never render its buttons again. Put the confirmation response and the polls in one sequence, so the newest server state wins.

### M5 (Medium): UI copy in Buddy's voice and Buddy's position

**Section:** 1.6 (connection loss), 1.3 (404 notice).

**Failure scenario:**
1. After five failed polls, the transcript row where Buddy's reply goes reads "I couldn't reach the server to check this turn, so I don't know how it ended."
2. Users read "I" as Buddy. On the server, Buddy finished and delivered a verified file.
3. The page is effectively telling the user that Buddy doesn't know the outcome, which is false for Buddy. What's true is that the page doesn't know.

That is the UI putting a statement about the work in the pipeline's mouth, which is what Section 4 exists to prevent.

**Fix:**
- All UI copy uses an impersonal page voice and is rendered as a page notice, styled differently from replies and never in the reply slot. For example: "This page lost contact with the server before the turn finished, so its result isn't shown. Reload the page to check." and "That session no longer exists on the server, so this is a new one."
- Add a test that the reply slot's text is always exactly `turn.reply` or absent.

### M6 (Medium): the banned-strings check gives false confidence

**Section:** 4.3, first bullet. Details and missed wordings are in Section 3.2 below.

**Fix, in short:** keep the scan, but make the guarantee structural rather than lexical:
- `outcome` is read in exactly one function, `shouldShowDownloads(turn)`, and nowhere else; a lint rule enforces this.
- The outcome-is-layout test compares attributes as well as text.
- The scan covers CSS and `index.html`.
- Adopt the design's own Section 14 fallback now: a committed snapshot of every UI-authored string, which the reviewer signs off change by change.

### M7 (Medium): hosting facts in 11.3 that change the decision

**Section:** 11.3.

**Findings:**
- [Likely] A Docker Space must be public to be reachable without a Hugging Face login, and a public Space shows the Space repository's files, meaning the code that goes into the image. The project is licensed `Proprietary`. The design calls this a caveat. It's a **go/no-go decision for the owner** before FE-4, and the alternative (a private GitHub pull during the image build, using a build secret) adds setup the design doesn't cost.
- [Likely] The free tier's disk is ephemeral, and the Space sleeps when idle. The design says "A restart mid-turn is handled by the backend's restart repair (its Section 7.5, run 8)". That repair reads the turn records **from disk**. On an ephemeral disk they're gone, so the user gets `404 session_not_found` and a new session, not a turn reported as failed. This isn't unsafe, since the 404 path is honest, but the stated behaviour is wrong, and it makes H1 the common path rather than an edge case.
- The RAM figure ("roughly 16 GB") is from memory and unverified. I couldn't check it either.

**Fix:** correct the text; add the owner's code-visibility decision as a precondition in Section 13; test the 404-reset path (H1) as the primary restart behaviour on this host.

### L1 (Low): `session_id` in `localStorage`

**Section:** 1.3 vs 10.1 (the gate test asserts "stores nothing in `localStorage`").

**Failure scenario:** the design contradicts itself, and there's also a privacy problem. With one shared password, the session id is the only per-user secret. On a lab or library computer, `localStorage` survives closing the browser. The next person enters the public password and resumes the previous user's session, including the download of their cleaned data.

**Fix:** keep `session_id` in `sessionStorage`. It survives a reload in the same tab, which is all the "reload to check" path needs.

### L2 (Low): gaps in the ambiguous-POST rule

**Section:** 1.6 and 1.10.

**Failure scenarios:**
- A `500` on `POST …/turns` after the backend created the turn and its record is shown as a plain error, but the turn is running.
- If the refetch after a network failure also fails, the design doesn't say what to show.
- "If a new turn exists it is shown" can pick up a turn started in another tab.

**Fix:**
- Treat any non-`4xx` failure of `POST …/turns` as ambiguous.
- Look for a turn with a higher `seq` than the last one known before the POST and the same `message`.
- If the refetch fails, show the connection notice and don't restore or re-enable Send until the session has been fetched.

### L3 (Low): the download isn't checked

**Section:** 1.8.

**Failure scenario:** a truncated or proxy-mangled body is saved as the user's file. The UI already has the API's `sha256`.

**Fix:** hash the `Blob` with `crypto.subtle.digest` (available over HTTPS) before saving it. On a mismatch, show a fixed "The download didn't match the server's copy; try again." and don't save. With cookie auth (Section 3.5), a plain link skips the blob entirely; then this check is dropped and the backend's `410` hash check is what remains.

### L4 (Low): a hidden tab misses the plan card

**Section:** 1.6 ("Polling pauses when the tab is hidden").

**Failure scenario:** the user switches tabs during a 20 s turn. The plan card appears while polling is paused and expires after 120 s. The reply honestly says the step wasn't confirmed, but the turn is wasted, and it counts against the 10 turns/hour quota.

**Fix:** keep polling at 10 s while the tab is hidden, and set `document.title` to a fixed "Approval needed – WorkBuddy" while a card is pending.

---

## 3. Answers to the specific questions

### 3.1 Can any UI path show a claim about the user's file that the API didn't make?

| Surface | Verdict | Notes |
|---|---|---|
| Reply | Safe | Verbatim text node, `pre-wrap`, no markdown; the verbatim test is right |
| Banners | Safe as designed | "Can't reach the server" is about the request; `503` shows the API's message |
| Toasts | None designed | Add "no toasts" to 4.1 so one isn't added later |
| Icons and colours | Safe as designed, weakly enforced | See M6: CSS and icon components escape the check |
| Disabled states | One problem | Plan buttons disabled by the client's clock (M4). Composer and upload disabling imply nothing about the file |
| Error text | Safe | The API's `error.message`, which is about requests (backend 1.0). Check `5xx` on `POST …/turns` (L2) |
| Plan card | UI safe; text not | Verbatim rendering is right. The backend's chain text misleads (M2), and Approve without the full plan (M3) |
| Details panel | **Not safe** | M1 |
| Download buttons | Safe within a session; **not across sessions** | H1 |
| Sidebar "Files from Buddy" | Same as above | H1: stale after the sweep or a restart unless cleared |
| Unknown-outcome state | Content true, attribution wrong | M5; also reached far too often because of H2 |
| Live region | Safe | Announces `reply` verbatim; "Buddy is working" is true while `running` |
| Optimistic UI | Safe | Only the user's own message |

### 3.2 Does the banned-strings test give false confidence?

Yes, as written. It's a lexical filter over `src/**/*.{ts,tsx}` for about ten words. Three wordings it would miss:

1. **"Your file is ready."** No banned word. Put above the download area, it restates delivery in the UI's voice.
2. **"All clean — no duplicates left."** "clean" doesn't match the word-bounded "Cleaned", and "left" isn't on the list.
3. **"Updated your spreadsheet"** (or "Fixed it!", "Tidied up"). None of these verbs is on the list.

Non-text routes it can't see at all:
- `styles.css` with `.turn--delivered::after { content: "✓" }` or a green border (CSS isn't scanned);
- an imported `<CheckIcon/>` component (no string literal to match);
- a `<meta name="description" content="Buddy cleans your spreadsheets">` in `index.html` (outside `src/`);
- template-literal parts, which the design doesn't say it scans.

The outcome-is-layout test would catch a class name only if it compares attributes, which the design doesn't specify. Component tests in jsdom don't apply `styles.css`, so they can't catch CSS-based treatment either.

It also has a cost in the other direction. "Failed" is banned, but "Upload failed" is legitimate request copy, so the allowlist fills with exceptions and loses its meaning.

What gives real confidence:
- restrict **data flow**: one function reads `outcome`, and a lint rule (`no-restricted-syntax` in ESLint, or an equivalent grep test) forbids reading `.outcome` anywhere else;
- the attribute-level DOM comparison across the four outcomes;
- a committed snapshot of every UI string (including CSS `content` and `index.html`) that the reviewer approves, so any new sentence shows up in the diff.

Keep the word scan as a cheap tripwire, not as the guarantee.

### 3.3 Is the plan card text safe under rule 1?

- **As rendered by the UI: yes.** It shows the API's `prompt` strings verbatim, adds no steps, reworks nothing, and is removed when answered. The UI's own strings ("Plan for your approval", "Approve plan", "Reject plan", "Sending your answer…") claim nothing about the file.
- **The text it renders is not fully safe:**
  - it misdescribes chains (M2);
  - it can be approved when incomplete (M3);
  - "Approval window closed" can be false (M4).
- It also leaves out `spreadsheet.analyze`, which runs regardless. That's acceptable because analyze changes no file, but the fixed plan-level line from M2 should not say "nothing runs if you reject".

### 3.4 Can the approve/reject flow leave a state where the UI shows a result for a rejected or timed-out turn?

**Within one session, no, provided `outcome` gating is implemented as designed.** Downloads need `outcome == "delivered"` from the server, and a rejected or timed-out turn never gets that (backend Decisions item 2, step 4, and 5.3). The `409` refetch path is right.

The states that can go wrong:

- **(a) Session reset plus turn-id reuse (H1).** This is the one real path to showing a result for a rejected turn: the old session's details or downloads appear under the new session's `t0001`.
- **(b) Stale poll after answering (M4).** It shows the card again, not a result. That's confusing but not a false claim.
- **(c) Lost confirmation POST (M4).** The state is unspecified, and the user can't tell whether the reject landed. The server state stays honest.
- **(d) Client-clock expiry (M4).** The UI says the window closed when it hasn't.

### 3.5 React + Vite, or htmx?

**Recommendation: htmx with Jinja templates, served by the same FastAPI process, with the demo password exchanged once for an HttpOnly, Secure, SameSite=Strict cookie.**

- **Reason:**
  - The React path adds a second ecosystem (Node, npm lockfile, TypeScript, Vitest, jsdom, ESLint plugins, `openapi-typescript`, Playwright in TypeScript, a two-stage Docker build) to a repository that is 100% Python and tested with pytest.
  - The design estimates about 2,200 app lines plus about 1,800 test lines. That's more than the ~1,700-line backend it sits on, for one screen.
  - The design's own test for this decision (Section 14: "if the htmx alternative's missing pieces turn out not to matter…") can be applied now. Each of its five htmx objections either goes away or costs a few lines:

| Objection in the design | With htmx plus a cookie |
|---|---|
| Draft kept across a `401` | A `401` is rare, because the cookie lasts the session. Keeping the draft is about 10 lines of JS (`htmx:responseError` → stash the textarea) |
| One in-flight poll, sequence numbers | `hx-trigger="every 2s"` on the turn fragment with `hx-sync="this:drop"`. The server renders the whole fragment from its own truth each time, and returns HTTP 286 [Likely: documented htmx behaviour] or a fragment without the trigger to stop polling. There is no client state machine to get wrong |
| Per-turn lazy details | `<details hx-get=… hx-trigger="toggle once">`, if the panel is kept at all (M1 says drop it) |
| Blob download carrying a header | Goes away: with a cookie, a plain `<a href download>` works. That removes L3's gap and the mobile memory risk |
| Countdown timers | Render the server's `expires_in_seconds` as static text, refreshed by the poll every 2 s. No timer code |

- **Rule 1 improves rather than weakens:**
  - All UI text lives in `templates/`, rendered by Python with Jinja autoescape.
  - The verbatim, outcome-is-layout and no-download-unless-delivered tests become ordinary pytest tests through `TestClient`, in the same suite and CI as the backend's rule-1 tests. No second test runner is needed.
  - Session resume on reload comes free: the server renders the current state.
  - H1's cross-session cache problem mostly disappears, because there's no client cache.
  - The HttpOnly cookie is also a **stronger** secret store than `sessionStorage`, since a script can't read it.
- **Costs of htmx, stated plainly:**
  - The HTML routes are a second route layer over the JSON API. They must call the same service functions that build the turn model, not re-derive `outcome`, and one test asserts the HTML reply equals the JSON `reply` for the same turn.
  - The cookie changes backend "Decisions" item 1 (header auth). That's the user's decision, with CSRF handled by SameSite=Strict plus an `Origin` check on non-GET routes.
  - Under the strict CSP, set `htmx.config.includeIndicatorStyles = false` (htmx injects an inline style otherwise [Likely]) and avoid `hx-on` / `js:` attributes.
  - The e2e tests need `pytest-playwright`, which isn't installed. Chromium is.
- **Confidence:** Medium.
- **The biggest unknown** is the developer's own fluency. If they already ship React comfortably and don't know htmx, the toolchain cost drops a lot and the case narrows.
- **What would change my mind:**
  - the developer is fluent in React and not in Jinja/htmx;
  - the UI must grow within about 3 months into something highly interactive (in-browser sheet previews, editing, multi-file drag-and-drop);
  - a spike shows the htmx polling fragment can't meet H2's rate budget.
- **Fastest test:** a half-day spike of the turn fragment alone (poll, plan card, reply, download) against a fake engine, with its three rule-1 pytest tests.

### 3.6 Is FE-1..FE-4 too big or too slow?

**Too slow for "a working demo soon", mainly because it adds three more review cycles on top of the backend's four.** FE-2, FE-3 and FE-4 each need an independent review, and FE-4 also needs live runs. The critical path is already backend PR-1 → PR-4, three of them rule-touching with live runs. The frontend shouldn't lengthen it.

**Smallest version that is still safe, as two PRs:**

1. **FE-1 `feat/frontend-demo` (rule-touching: one independent review, live runs per 10.4).**
   - **Features:** gate, upload button, composer with the mode radio, polling at 2 s with `429` handled (H2), plan card (M3's guard, server-relative time from M4, no client expiry), verbatim reply, downloads when `delivered`, error messages verbatim, the page-voice connection notice (M5), and the reset on `404` (H1).
   - **Tests (non-negotiable):** verbatim reply; outcome-is-layout with attributes; no download unless `delivered`; nothing result-shaped before `finished`; the reply slot only ever holds `turn.reply`; the M3 guard; the H1 reset; the UI-copy snapshot.
   - **End-to-end (five):** golden path with an independent file check and the rule-2 hash; reject; pipeline failure reply; approval timeout; lost connection followed by reload.
   - Developed in parallel against the fake engine; merged after backend PR-3.
2. **FE-2 `feat/frontend-deploy`.**
   - Dockerfile, static mount, security headers, deploy notes.
   - Merged with or after backend PR-4.
   - Reviewed for headers and secrets handling; not rule-touching.

**Moved to the backlog, stated as not done:**
- the details panel (M1);
- drag-and-drop;
- the countdown;
- the three-viewport matrix (keep one 360 px no-overflow check);
- axe in CI (one recorded axe run instead);
- the manual screen-reader pass;
- `.xls` in the UI;
- the rate-limit and restart e2e cases;
- `openapi-typescript` (not applicable with htmx).

### 3.7 Contradictions with the backend design

| # | Frontend | Backend | Resolution |
|---|---|---|---|
| C1 | Poll every 1 s (1.6) | 60 requests/min/IP (Decisions item 1) | H2. Must be fixed in both before the chat PR |
| C2 | "Restart repair" handles a mid-turn restart on Hugging Face (11.3) | Repair reads turn records from disk (7.5, run 8) | M7. On an ephemeral disk the result is a `404`, not a failed turn |
| C3 | Plan card shows the steps verbatim; mockup shows "from report.xlsx" for step 2 | Prompt template filled with `input_name` (1.5) | M2. Fix the template for chain steps |
| C4 | Needs `steps` (ask 1) | Keeps step-1-only compatibility fields (1.5) | M3. The UI refuses to approve without `steps`; drop the fields |
| C5 | Plan built "before `Hands` runs" from Write-tier steps | [Certain] the tier is only known after dispatch (`buddy/hands.py:240-252`); `CapabilityRequest` has no tier | Backend PR-3 must resolve tiers through the Dispatcher before publishing the plan, or the plan can leave out or include the wrong steps. Raise in the backend review |
| C6 | Keys turns by `turn_id` (implied) | `turn_id` and delivered-file ids are unique only within a session (3.3, 5.3) | H1 |
| C7 | Countdown from `expires_at` | Only an absolute `expires_at` is given | M4. Add `expires_in_seconds` |
| C8 | Password header (ask 4) | Header auth (Decisions item 1) | Consistent. If the htmx-plus-cookie recommendation is taken, Decisions item 1 changes, and that's the user's decision |

No other conflicts. Same-origin with empty CORS matches Decisions item 1. Types generated at build time don't conflict with `/openapi.json` being disabled in production.

---

## 4. Verdict: **APPROVE WITH CHANGES**

The rule-1 design at the core (Section 4.1) is correct and should be kept as written. Before implementation starts:

1. **H1:** state keyed by `(session_id, id)`, a full reset on a session change, stale cross-session responses dropped, and the tests.
2. **H2:** agree the poll interval, `429` handling on polls and a separate poll budget in **both** design docs.
3. **H3:** add to Section 13's preconditions: backlog 1a closed, or the upload-time multi-sheet refusal (and `.xls` left out of the demo); plus the user's explicit go or no-go on going public with PR-B / M2 open.
4. **M1:** drop the details panel from the demo, or apply the relabelling and outcome restrictions.
5. **M2 and M3:** chain-aware plan text and a plan-level line from the backend; the UI shows Approve only with a non-empty `steps`.
6. **M4:** server-relative time, no client-side expiry, handling of a lost confirmation POST, answered cards never re-shown.
7. **M5:** page-voice copy outside the reply slot, with a test.
8. **M6:** the structural `outcome` guard, the attribute-level comparison and the UI-copy snapshot; the word scan is kept only as a tripwire.
9. **M7:** correct 11.3; the owner's code-visibility decision becomes a deployment precondition.
10. **Stack and split:** the user decides React or htmx before FE-1 (I recommend htmx with a cookie, Section 3.5). The four FE PRs become the two in Section 3.6.

L1 to L4 can be done in FE-1 without another design round.

---

## Deferred

These are also proposed for `docs/backlog.md` by whoever applies this review's changes; this review doesn't edit the backlog.

- The "What happened" details panel (if dropped per M1), redesigned to show only verified data.
- Short-lived download tokens and SSE (frontend ask 5), not needed with the htmx-plus-cookie approach.
- The accessibility matrix: three viewports, axe in CI, and a manual NVDA or VoiceOver pass. The review of FE-1 must say whether any was done.
- `.xls` in the demo, after backend Decisions item 4's test passes and the sheet count can be checked for `.xls`.
- The multi-sheet upload refusal is a stopgap. Backlog 1a (keep every sheet, or refuse with a reply) is the real fix.
- Verifying the Hugging Face Spaces terms (RAM, sleep, disk, private Spaces). This review couldn't reach `huggingface.co`.
