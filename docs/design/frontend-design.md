# Frontend design: the web UI for the public demo

**Status:** design. FE-1 is merged (see `docs/reviews/frontend-demo-impl.md` for how, and where it differs). FE-2 is implemented on `feat/frontend-deploy` (`docs/reviews/frontend-deploy-impl.md`, `docs/deploy.md`); Sections 11.1 to 11.3 and 13.1 say where it differs from the first design. Revised on 2026-10-01 to apply `docs/reviews/frontend-design-review.md` (branch `review/frontend-design`, verdict APPROVE WITH CHANGES) and the owner's decision on the stack.

**Owner's decisions (2026-10-01):**
- The UI is **htmx with Jinja templates, served by the same FastAPI process**, as the review recommends (its Section 3.5). React, TypeScript and Vite are dropped.
- The demo password is **exchanged once for an HttpOnly, Secure, SameSite=Strict cookie**, as the review recommends. For the browser this replaces `Authorization: Bearer`.
- The goal is a working demo soon, so the frontend is **two PRs** (review Section 3.6), not four.

**Depends on:** `docs/design/backend-api-design.md` on `design/backend-api` (not merged), and in particular three of its sections:
- "Decisions (2026-09-30)": public demo, shared password, once-per-turn plan confirmation, Python 3.11+, `.xlsx` and `.xls`.
- "Decisions (2026-09-30, part 2)": the poll-aware rate limit, the answers to this design's asks, the 5 MB upload cap.
- "Decisions (2026-10-01)": the access cookie, CSRF, chain-aware plan text, the dropped compatibility fields, the multi-sheet refusal.

Where this document names an API field or behaviour, it means that document's.

**Rule-touching:** yes, rule 1. Backend Section 5.1 says the API can't stop a frontend from writing its own "Done!" banner. This design is where that is prevented. FE-1 needs an independent review and live runs before merging (`CLAUDE.md`). The UI doesn't touch rule 2 (it never sees a path and can't write to an upload), but the end-to-end test checks it anyway (Section 10.3).

**Where each review finding is applied:**

| Finding | Section |
|---|---|
| H1 | 1.3, 6 |
| H2 | 1.6 |
| H3 | 13.2 |
| M1 | 1.9 |
| M2, M3, M4 | 1.7 |
| M5 | 1.6, 1.10, 7 |
| M6 | 4.3 |
| M7 | 11.3, 13.2 |
| L1 | 6 |
| L2 | 1.6 |
| L3 | 1.8 |
| L4 | 1.6 |

Where this design doesn't follow the review, Section 15 says so.

---

## 0. Decisions in brief

1. **Server-rendered HTML with htmx, inside the FastAPI process.** Jinja templates render every screen and fragment from the server's own state. The browser runs htmx (vendored, at a pinned version) and one small `app.js`. There is no Node, no build step and no client-side state store. One container, one origin, no CORS (Sections 3 and 11).
2. **The demo password is exchanged once for an HttpOnly cookie** (`POST /api/v1/access`, backend Decisions 2026-10-01). No script on the page can read the credential, and downloads are plain links.
3. **The UI never writes a statement about the user's file.** `reply` is rendered as escaped text, exactly as returned. `outcome` only decides layout (whether a download list appears), and exactly one function reads it. There is no "Done", "Success", "Saved" or "Removed" string, and no success or failure icons or colours. Tests enforce this structurally (Section 4).
4. **One screen after the password gate.** Files, chat, the plan card and the downloads are parts of one page. The "What happened" details panel isn't in the demo (M1).
5. **Polling, not streaming.** Every 2 s, then every 3 s once the turn is 20 s old by the server's clock. A `429` on a poll waits `Retry-After` and doesn't count as a connection failure (H2).
6. **The tests are pytest.** The HTML-route tests run through `TestClient` in the same suite as the backend's rule-1 tests. Five browser end-to-end tests use `pytest-playwright` against the real app and the fake LLM (Section 10).
7. **No host is chosen until the owner chooses one** (M7), from the measurements in `docs/deploy.md`. The image is host-neutral. Code visibility still matters, because the project is licensed `Proprietary` (Sections 11.3 and 13.2).

---

## 1. Screens and flow

### 1.1 Overview

```
 Gate ──(password → access cookie)──▶ Workspace (new or resumed session)
                                         │
     upload ─▶ send message ─▶ [running] ─▶ [awaiting plan approval] ─▶ [running] ─▶ finished
                                                                                      │
                                                           reply shown verbatim ◀─────┘
                                                           (+ downloads if outcome == "delivered")
```

Everything after the gate is one page, `GET /`. The server renders it from the session's state every time. The browser keeps no copy of that state beyond the DOM (Section 6).

### 1.2 Screen 1: the demo-password gate

- **What it shows.** `GET /` without a valid access cookie renders the gate:
  - one labelled password field and a **Continue** button;
  - the demo notice: this is a public demo, don't upload sensitive data, and the retention text from 1.3.

  There's no account, no sign-up and no "forgot password".
- **Submitting.** The form posts the password to `POST /api/v1/access` (form-encoded, field `password`). A `204` response sets the cookie, and `app.js` reloads `/`, which now renders the workspace. The page never stores the password, puts it in a URL or logs it.
- **Errors:**
  - `401`: the API's `error.message` appears verbatim under the field, the field is cleared, and focus stays in it.
  - `429 rate_limited`: the message appears, and Continue is disabled for `Retry-After` seconds.
  - `403 forbidden_origin`: the message appears. It means the request didn't come from this page's origin (backend Decisions 2026-10-01).
- **A side effect of SameSite=Strict.** [Likely] Browsers don't send a Strict cookie on a navigation that starts on another site. So someone who opens the demo from a link in an email or a chat sees the gate again, even with a valid cookie. Entering the password again works. That's the price of Strict, and the demo accepts it.
- **A later `401` on any request** (the cookie expired, or a server restart lost its tokens) puts the API's message in the page-notice area, with the fixed link "Enter the password again" to `/`. The page doesn't navigate away, so the draft stays on screen.

### 1.3 Screen 2: the workspace

Desktop layout (two columns). On narrow screens, see Section 8.

```
┌──────────────────────────────────────────────────────────────────────────┐
│ WorkBuddy demo                         Mode: (•) Friend  ( ) Focus       │
├───────────────────────┬──────────────────────────────────────────────────┤
│ Files                 │  You:    remove duplicate rows, then empty rows  │
│ [Choose file]         │                                                  │
│ report.xlsx  18 KB    │  ┌ Plan for your approval ─────────────────────┐ │
│  (active)             │  │ 1. Remove duplicate rows from report.xlsx.  │ │
│                       │  │ 2. Then remove empty rows from the result   │ │
│ Files from Buddy      │  │    of step 1.                               │ │
│ report_dedup…xlsx     │  │ The steps run in this order, each on the    │ │
│   [Download]          │  │ previous step's result. Only the final file │ │
│                       │  │ is handed back.                             │ │
│                       │  │ [Approve plan]  [Reject plan]               │ │
│                       │  │ about 110 s left                            │ │
│                       │  └─────────────────────────────────────────────┘ │
│                       │                                                  │
│                       │  Buddy:  <reply, exactly as returned>            │
│                       │          [Download report_dedup….xlsx (16 KB)]   │
│                       │                                                  │
│                       │  ┌ Page notices (page voice, never Buddy's) ───┐ │
│                       │  └─────────────────────────────────────────────┘ │
│                       ├──────────────────────────────────────────────────┤
│                       │ [ message box                        ] [Send]    │
└───────────────────────┴──────────────────────────────────────────────────┘
```

The plan card and the reply are never on screen together for one turn. They appear together here only to label the parts. The plan text is the backend's (backend Decisions 2026-10-01). The UI adds only the heading, the button labels and the time-left text.

**Session lifecycle (H1).**
- **No session yet.** With a valid access cookie and no session pointer (Section 6), `GET /` renders the workspace shell. The shell creates the session with `POST /ui/sessions` on load (htmx `hx-trigger="load"`), so no GET route creates state (backend Decisions 2026-10-01, CSRF). The response sets the session pointer and renders the empty workspace.
- **A session that exists.** With a pointer to it, `GET /` renders that session: files, transcript, any pending plan card, and the poll for an unfinished turn. A reload therefore resumes with no client code.
- **A session the server doesn't have** (removed by the sweep, or lost in a restart, which on an ephemeral-disk host is the normal restart path, M7). `GET /` clears the pointer, renders an empty workspace, and shows the page notice "That session no longer exists on the server, so this is a new one." The server knows exactly when this is the case, so the notice is never a guess.
- **Every fragment URL names its session:** `/ui/s/{session_id}/…`. Take a request made for session A that arrives after the page has moved to session B. It gets `404 session_not_found`, because A is gone, and `app.js` reloads `/`. It can't land on B's `t0001`, because B's turns are only reachable under B's id.
- **The reload is the reset.** On any `404 session_not_found`, the page reloads, so no transcript, download link or file list from A survives (H1).
- **A second guard.** `app.js` also drops any fragment whose `data-session-id` isn't the page's (Section 3.3).

**Retention text.** The page's retention promise must be true. The number is the server's `retention_hours` (backend part 2, ask 2), which stays `null` until the sweep exists (PR-4). While it's `null`, the page shows no number.

### 1.4 Upload

- **The control.** A labelled `<input type="file">` in a form that posts to `POST /ui/s/{sid}/files` (multipart). Its `accept` list is the server's `accepted_extensions`. Drag-and-drop isn't in the demo (Section 16).
- **No client-side validation.** Type, size, sheet count and structure are the server's decision (backend Section 6.2 and Decisions 2026-10-01). For `413`, `415` and `422`, the API's `error.message` appears next to the control, and the file list doesn't change.
- **Multi-sheet workbooks.** A multi-sheet `.xlsx` gets `422 multiple_sheets` at upload (backend Decisions 2026-10-01, H3). Its message is shown like any other upload error.
- **During a turn.** While a turn is running or awaiting approval, the server renders the upload control disabled. It also answers `409 session_busy`.
- **On success.** The response re-renders the file list with the server's sanitized `name` (replies use the server's name, not the browser's), and that file becomes the active one. Uploads are listed but aren't downloadable (backend 1.6). Files Buddy delivered are listed separately and are downloadable.
- **`.xls`.** The control accepts `.xls` only if the server lists it. Whether `.xls` is turned on for the public demo is an open owner decision (Sections 13.2 and 15).

### 1.5 Chat with Buddy: friend and focus mode

- **Mode toggle.** Two labelled radio buttons, Friend (the default) and Focus, inside the composer form. Each `POST /ui/s/{sid}/turns` sends `mode`, which is a per-session setting (backend 1.3). The page says only "Friend: chattier. Focus: brief." about the difference. That line describes the control, not any result.
- **Transcript.** The session's turns, oldest first: the user's `message`, then Buddy's `reply` once `status == "finished"`. An unfinished turn shows the progress row (1.6) or the plan card (1.7).
- **Composer:**
  - a `<textarea>` with a visible label, and `maxlength` set from the server's `max_message_chars`;
  - a Send button;
  - Enter sends and Shift+Enter adds a newline. This is stated next to the field, and takes about 5 lines in `app.js`.

  The server renders the composer disabled while a turn is unfinished.
- **Refused messages.** On `409`, `422` or `429` from `POST …/turns`, htmx doesn't swap (its default for error statuses), so the message stays in the box. The API's message appears under the composer. For a `429`, Send stays disabled for `Retry-After` seconds. Nothing is added to the transcript, because a refused request creates no turn (backend 1.3).

### 1.6 Polling a running turn (H2, M5, L2, L4)

A turn's state is the server's `status`. The page never works it out for itself.

```
POST …/turns ─▶ running ──poll──▶ running …
                  │                 │
                  │                 └─▶ awaiting_confirmation ─(user answers)─▶ running …
                  └──────────────────────────────────────────────────────────▶ finished
```

**How polling works.** An unfinished turn's container holds one poll element:

```html
<div class="poll" hx-get="/ui/s/{sid}/turns/{tid}/poll?v={version}"
     hx-trigger="load delay:{d}s, wb:retry" hx-swap="outerHTML"
     hx-sync="closest .turn:drop"></div>
```

- **The delay `d`** is 2 while the turn is under 20 s old by the **server's** clock (from `started_at`), and 3 after that. The server writes it into each poll element it renders, so the cadence needs no client timer.
- **`version`** is a counter for each turn that the server increments on every status change. It's a new field on the turn record, used only by the HTML layer.
- **When nothing has changed,** the response is a new poll element (re-armed, with the current `d`). While a plan is pending, it also updates the card's time-left text out of band. The card, its buttons and the user's focus aren't touched, so a keyboard user isn't pulled back every 2 s.
- **When the state has changed,** the response replaces the whole turn container, using the `HX-Retarget` and `HX-Reswap` response headers ([Likely] supported by htmx 2; the spike confirms it). A finished turn's container has no poll element, so polling stops.
- **One request per turn at a time.** Each poll element triggers once, so at most one poll per turn is in flight. The worst case is 0.5 polls/s per unfinished turn, well inside the backend's `poll` bucket of 180/min (part 2 item 1).
- **Hidden tabs (L4).** Polling doesn't pause in a hidden tab. Browsers may slow background timers, which only makes polls rarer.
- **Tab title.** While a plan is pending, the fragment carries `<title>Approval needed – WorkBuddy</title>`. [Likely] htmx applies a `<title>` in a response to the document, so the tab shows it. Otherwise the title is "WorkBuddy demo".

**While `running`:** a progress row reads "Buddy is working", with a spinner. That's true exactly while the server says `running`, and the server removes the row when the status changes. The page never estimates progress or names a step.

**Poll failures, handled in `app.js`:**
- **`429` (any code).** Wait `Retry-After` seconds, then fire `wb:retry` on that poll element. This is **not** a connection failure: it doesn't count toward the limit below, and the row's text doesn't change (H2).
- **Network error or `5xx`.** Retry after 2, 4, 8, 8 and 8 s, and reset the count on any `2xx`. After 5 consecutive failures (about 30 s), `app.js` removes the poll element and shows this page notice (M5): "This page lost contact with the server before the turn finished, so its result isn't shown. Reload the page to check." Reloading works, because `GET /` renders the session again from the server's state.
- **`401`.** Remove the poll element, and show the `401` message with the "Enter the password again" link (1.2).
- **`404 session_not_found`.** Reload `/` (1.3).
- **`404 turn_not_found`.** Reload `/`, which renders the session as the server has it.

**An ambiguous turn POST (L2).** A failure of `POST /ui/s/{sid}/turns` that isn't a `4xx` (a network error or a `5xx`) is ambiguous: the server may have created the turn. The page doesn't retry the POST, because that could run a write twice. The composer form carries `last_seq`, the highest turn `seq` on the page. After an ambiguous failure, `app.js` keeps Send disabled, fetches `GET /ui/s/{sid}/transcript`, and then:
- if the transcript has a turn with `seq > last_seq` and the same `message` as the draft, that turn is shown and the draft is cleared;
- otherwise the draft stays, and the page notice reads "The server didn't confirm receiving that message.";
- if that fetch also fails, the page shows the connection notice above, and Send stays disabled until the session has been fetched successfully.

Both notices are about the request, not about any work. A turn with a higher `seq` and a different message (one from another tab) is shown as the session's turn, which it is. It isn't taken to be this message.

### 1.7 The once-per-turn plan approval card (M2, M3, M4)

Backend Decisions item 2: before the first file-changing step, the user sees the whole plan and approves or rejects it once. Approval covers every step, and reject means no step runs.

**What the card shows.** All of its content comes from `pending_confirmation`, rendered and escaped by the server, in this order:
1. The heading "Plan for your approval" (UI copy).
2. `steps`, as an ordered list with one row per step. Each row is the step's `prompt`, verbatim. Step 2 says "the result of step 1", and that number refers to the list's own numbering, so steps are never reordered, merged or renumbered.
3. `plan_note`, verbatim, unless it's `null`. For a chain, the backend sends the fixed line "The steps run in this order, each on the previous step's result. Only the final file is handed back." (backend Decisions 2026-10-01). The UI writes no line about the plan.
4. The buttons and the time left.

**Chain text (M2) is the backend's job.** Its templates say "Then … from the result of step 1" for every step after the first, and no step says "save the result as a new file". The card adds no promise such as "report.xlsx won't change", and doesn't say "nothing runs if you reject", because `spreadsheet.analyze` runs either way (review 3.3).

**Keep the card prominent.** It's the only place where the user can see, before anything runs, that the planned steps aren't what they asked for (backlog N1).

**Approve only with the whole plan (M3).** The server renders **Approve plan** only when `steps` is a non-empty list. Otherwise it renders only **Reject plan**, with the fixed text "The server didn't send the plan, so it can't be approved here." The backend has removed the step-1-only compatibility fields from the public schema (Decisions 2026-10-01), so a fallback has nothing to read. The guard is there for a regression.

**Buttons:**
- **Focus.** Approve and Reject are equally prominent, and neither is focused when the card appears. Focus moves to the card's heading instead (`autofocus` on a heading with `tabindex="-1"`, which htmx [Likely] honours after a swap). Keyboard and screen-reader users land on the plan, and an accidental Enter can't approve it.
- **Clicking** sends `POST /ui/s/{sid}/turns/{tid}/confirmation` with `confirmation_id` and `decision`.
- **While the request is in flight,** both buttons are disabled (`hx-disabled-elt`). The buttons' `hx-sync="closest .turn:replace"` also cancels any poll in flight for that turn, so a poll that was sent before the click can't be applied after it.

**Time left (M4):**
- The card shows "about N s left". The server works out N from `expires_at` and its own clock when it renders the card, and every poll refreshes it.
- There's no client countdown and no client-side expiry. The page never disables the buttons by its own clock, and never says "Approval window closed".
- Only a change of server state removes the card: the first poll after the server's timeout (which the backend treats as a reject), or a `409`. The reply then says what happened, in the pipeline's words.

**After answering:**
- **`200`.** The response is the turn as it now stands (usually `running`), and it replaces the card. The server clears `pending_confirmation` when it records the decision, before responding, so no later response can contain that card. The swap guard in `app.js` also drops any fragment whose turn `version` is lower than the one already on the page. An answered card is never shown again (M4).
- **`409 no_pending_confirmation` or `409 stale_confirmation`.** The plan changed under the user: it timed out, or was answered in another tab. `app.js` fires `wb:retry` to refetch, and the page shows what the server says now. It doesn't assume the decision took effect.
- **Lost POST (a network error or a `5xx`).** Both buttons stay disabled. `app.js` refetches the turn and names the `confirmation_id` it tried to answer (`…/poll?unconfirmed={confirmation_id}`). If that same confirmation is still pending, the server renders the card again, with live buttons and the fixed line "Your answer may not have reached the server." Otherwise it renders the turn as it is now. The page never re-enables the buttons by itself.
- **The page never writes "Plan approved, running" or "Plan rejected, nothing was changed".** After a reject, the pipeline's reply says what happened. While the click is in flight, a button may show the static label "Sending your answer…", which is about the click, not the file.
- **No Write-tier step, no card.** A turn without one never shows a card (backend Decisions item 2, step 5).

### 1.8 Finished turn: the reply and the delivered-file download (L3)

- **The reply** appears in the transcript's reply slot as `turn.reply`, escaped by Jinja, in an element with `white-space: pre-wrap`. Nothing the UI wrote about the work comes before or after it (Section 4). The reply slot holds `turn.reply` or nothing, and page notices never go there (M5).
- **Downloads.** When `should_show_downloads(turn)` is true (Section 4.3), each entry in `delivered_files` gets a link, `<a href="{download_url}" download>`, labelled with the file's `name` and the API's `size`, for example "report_deduplicated_….xlsx (16 KB)". The label is a file name, not a claim. For any other outcome, the download area isn't rendered.
- **How the download works:**
  - A plain same-origin link carries the access cookie, so no blob or object URL is needed, and the browser saves the response directly.
  - L3's client-side hash check needs a blob, so it doesn't apply to plain links, as the review itself notes.
  - What remains is the backend's own check. The download route re-hashes the file and returns `410 file_unavailable` if it no longer matches the hash recorded at delivery (backend 1.7, 5.3). The UI doesn't verify the file itself.
- **Failed downloads.** A `404` or `410` on a plain link goes to the browser's own download handling. [Possible] The browser shows a failed download, or saves the error body. The demo accepts this, because a `410` only happens after the file has gone or changed. Showing the API's message instead is in Section 16.
- **The sidebar.** Delivered files also appear in the sidebar for later turns (`kind: "delivered"`, `downloadable: true`).

### 1.9 The "What happened" details panel: not in the demo (M1)

It's dropped, for two reasons:
- It would show each step's `status` (the skill's own report) and `verified`, under a heading that presents them as what happened, next to a reply that can say nothing could be confirmed (backend 7.3 case 3, `integrity_failure`).
- Nobody needs it to use the demo, and the live-run reports read `detail` from the API directly.

It's listed in Section 16. If it comes back, it shows only verified data, is labelled as raw pipeline data, and isn't rendered for `failed` or `integrity_failure` turns.

### 1.10 Error states

Every non-2xx response, from the JSON API and from the HTML routes alike, has the body `{ "error": { "code", "message" } }`, because the HTML routes use the backend's exception handlers. htmx doesn't swap error responses, so `app.js` puts `message` as **text** (`textContent`) into the error slot of the form or element that made the request. It uses `code` and the status only to choose behaviour.

| Code / situation | UI behaviour |
|---|---|
| `401 unauthorized` | The message and the "Enter the password again" link in the page-notice area. The draft stays on screen, and polling stops (1.2) |
| `403 forbidden_origin` | The message beside the control that made the request |
| `404 session_not_found` | Reload `/`. The server shows the new-session notice (1.3) |
| `404 turn_not_found`, `404 file_not_found` | Reload `/` |
| `409 session_busy` | The message under the control. Refetch the transcript (another tab may be running a turn), which renders that turn's poll |
| `409` on a confirmation | Refetch the turn (1.7) |
| `413`, `415`, `422 invalid_file`, `422 multiple_sheets` | The message under the upload control. The list doesn't change |
| `422 invalid_request` | The message under the composer. The draft is kept |
| `429 rate_limited`, `429 server_busy` | The message. The control stays disabled for `Retry-After` seconds, and the draft is kept. On a poll, Section 1.6 applies instead: no message, and not a failure |
| `410 file_unavailable`, `404` on a download | The browser's own download handling (1.8) |
| `503 storage_full` | The message in the page-notice area. Upload is disabled |
| `500 internal_error`, unknown code | The API's message under the control. On `POST …/turns`, the ambiguous-POST rule applies (1.6) |
| Network failure | Polls: 1.6. Turn POST: the ambiguous-POST rule. Confirmation POST: 1.7. Anything else: the page notice "This page can't reach the server." |
| Finished turn with an `outcome` other than `delivered` | **Not an error state.** The reply is shown as usual, without a download area. The reply carries the explanation |

A turn with `reply_source == "pipeline_fallback"` is displayed like any other reply. The optional "automatic message" label isn't in the demo.

---

## 2. What the UI uses from the backend

- **The same process and the same service functions.** The HTML routes (`backend/web/`) call the functions the JSON routes call to:
  - load a session;
  - build a turn object;
  - start a turn;
  - record a confirmation;
  - list files.

  They never derive `outcome`, `delivered_files` or `reply` again. They read them from the same turn object the JSON API returns. A test checks that the reply text in the HTML equals the JSON `reply` of the same turn, for every finished turn in the tests (Section 4.3).
- **JSON routes the browser calls directly:** `POST /api/v1/access` (the gate) and `GET /api/v1/sessions/{sid}/files/{fid}/download` (plain links). Everything else goes through the HTML routes.
- **HTML routes.** None of them is in `openapi.json`. All except the gate are behind the access cookie, the CSRF check and the rate limiter (backend Decisions 2026-10-01).

| Route | Renders | JSON equivalent |
|---|---|---|
| `GET /` | The gate (without a valid credential), or the workspace for the session pointer | `GET /sessions/{id}` |
| `POST /ui/sessions` | An empty workspace; sets the session pointer | `POST /sessions` |
| `POST /ui/s/{sid}/files` | The file list | `POST …/files` |
| `POST /ui/s/{sid}/turns` | A new turn container (running) | `POST …/turns` |
| `GET /ui/s/{sid}/turns/{tid}/poll` | A poll element, or a new turn container | `GET …/turns/{tid}` |
| `POST /ui/s/{sid}/turns/{tid}/confirmation` | The turn container | `POST …/confirmation` |
| `GET /ui/s/{sid}/transcript` | The transcript | `GET /sessions/{id}` |

Section 12 lists what this design needs from the backend, and the status of each item.

---

## 3. Tech choice, and why

### 3.1 The choice

**htmx 2 with Jinja templates, served by FastAPI** (the owner's decision, 2026-10-01, following review Section 3.5):
- one vendored `htmx.min.js` at a pinned version, not from a CDN;
- one `app.js` of about 150 lines;
- one stylesheet.

Jinja2 is already installed in this environment (3.1.6, as a transitive dependency), and FE-1 adds it to `requirements.txt` explicitly. There's no Node, npm, TypeScript or bundler.

### 3.2 Why, from the requirements

| Requirement | Consequence |
|---|---|
| A working demo soon, built by one developer in a Python-only repo tested with pytest | No second ecosystem: no Node toolchain, lockfile, TypeScript, Vitest, jsdom or two-stage Docker build |
| Rule 1 must be enforced mechanically | All UI text is in templates, rendered by Python with autoescape. The rule-1 tests are ordinary pytest tests through `TestClient`, in the same suite and CI as the backend's |
| Session state across reloads, sessions and tabs (H1) | The server renders from its own state every time, so there's no client cache to go stale |
| Handling a secret on a public page | The access cookie is HttpOnly, so no script on the page can read it. Any script on the origin could read `sessionStorage` |
| Downloads | A plain link carries the cookie, so there's no copy of the file in browser memory |

### 3.3 What `app.js` does, and nothing else

`app.js` contains no user-visible text. It reads every string it shows from `<template data-copy="…">` elements that the server renders, so all copy lives in templates (Section 7). It inserts text only with `textContent`. Its jobs:
1. **Error slots.** On `htmx:responseError`, put the JSON `error.message` into the requesting element's error slot, and apply the behaviour for that code from Section 1.10.
2. **Poll retries.** Wait `Retry-After` on a `429`. Apply the backoff and the five-failure limit on network errors and `5xx` (1.6).
3. **Swap guard.** On `htmx:beforeSwap`, cancel the swap if the fragment's `data-session-id` isn't the page's, or if its `data-version` is lower than the version already on the page for that turn.
4. **Lost confirmation POST.** Keep the buttons disabled, and refetch with `unconfirmed=` (1.7).
5. **Ambiguous turn POST.** The check in 1.6.
6. **Gate success.** Reload `/` after the `204`.
7. **Composer keys.** Enter sends, and Shift+Enter adds a newline.

### 3.4 Costs, stated plainly

- **A second route layer.** The HTML routes repeat the JSON routes' wiring, though not their logic. The mitigations are that they call the same service functions, and the test that the HTML reply equals the JSON reply (Section 4.3).
- **JavaScript behaviour can only be tested in a browser.** `TestClient` doesn't run `app.js`. The five e2e tests (10.3) cover the paths that matter for rule 1. The rest is left to review.
- **This design relies on some htmx behaviour that I haven't verified here [Likely]:**
  - the `HX-Retarget` and `HX-Reswap` response headers;
  - `<title>` handling in fragments;
  - `autofocus` after a swap;
  - `hx-disabled-elt`;
  - `hx-sync` with `replace` and `drop`;
  - no swap on a `4xx` or `5xx`;
  - the config switches in 11.2.

  FE-1 therefore starts with the review's half-day spike: the turn fragment alone (poll, plan card, reply, download) against a stub service, with its three rule-1 pytest tests. It confirms these before the rest is built.
- **New dev-only dependencies.** The e2e tests need `pytest-playwright` and the `playwright` Python package, which aren't installed. Chromium is.
- **The wrong tool for rich interaction.** If the UI later needs rich client-side interaction (sheet previews in the browser, editing, multi-file drag-and-drop), htmx stops being the right choice. The review's evidence for changing course still applies (Section 14).

### 3.5 What was dropped

From the previous revision:
- React, TypeScript and Vite;
- type generation with `openapi-typescript`;
- Vitest, Testing Library, `jest-axe` and ESLint;
- the Node build stage;
- the password in `sessionStorage`;
- downloads through a fetch and a blob.

The previous revision chose React because of client state that htmx handles awkwardly. Review Section 3.5 answers that point by point.

---

## 4. Rule 1 in the UI

The backend states the obligation (its Section 5.1): show `reply` as given, and treat `outcome` as a layout hint only. This section makes that mechanical.

### 4.1 The rules

1. **`reply` is rendered verbatim.** No trimming beyond CSS whitespace handling, no markdown rendering, no linkification, no truncation, no "show more" that hides the outcome sentence, no reformatting numbers. `white-space: pre-wrap` shows what was sent, newlines included.
2. **The UI authors no sentence about work on the user's file.** Its own strings are limited to: control labels, empty states, progress-while-`running`, connection and request problems (about the request, never the work), and the API's own `error.message`. Not allowed anywhere in `src/`: "Done", "Success", "Complete", "Saved", "Removed", "Cleaned", "Finished successfully", "Failed", "All set", check-mark or cross icons, and success or failure colors on replies.
3. **`outcome` only decides layout**: `delivered` renders the download area from `delivered_files`; every other value renders none. It never picks wording, an icon, a color or an emoji. All four outcomes look the same apart from that. In particular there is no red "failed" or green "delivered" treatment: the reply says which it was.
4. **The download area is derived from `delivered_files` only**, never from parsing `reply`, and never rendered when `outcome != "delivered"`, even if `delivered_files` is somehow non-empty (defence against a backend bug: the button is a claim that a file exists).
5. **The details panel shows data with the API's own values, verbatim** (1.9), and adds no interpretation.
6. **Not-yet-finished turns show no result-shaped text.** While `running` or `awaiting_confirmation`, there is no reply area, no counts, no file names of expected output. The plan card shows the API's `prompt` strings, which the backend design says describe a proposed action and claim no outcome (its Section 5.1, item 5).
7. **Errors are about requests, not work** (backend Section 5.1, item 6). A network failure during a turn says the UI doesn't know how it ended; it never says the turn failed or didn't run.
8. **Optimistic UI is limited to the user's own message.** The transcript may show the user's just-typed message immediately, marked as sending; nothing on Buddy's side is optimistic (no pre-created "Buddy replied" row).

**How these rules apply to this stack.** These are notes, not changes. The rules above are word for word from the previous revision, so some of them name React-era things:
- In rule 2, "anywhere in `src/`" means every UI source: everything in `backend/web/` (the Python files, `templates/`, `static/app.js` and `static/styles.css`).
- Rule 5 has nothing to apply to in the demo, because the details panel is dropped (1.9). It applies again if the panel returns.
- The optimistic user message that rule 8 allows isn't implemented in the demo. The user's message appears when the server renders the new turn.

### 4.2 Text-only rendering

- **Templates.** Every template is a `.html` file rendered with Jinja autoescape on. API strings (`reply`, `error.message`, `name`, `prompt`, `plan_note`) are inserted only as `{{ value }}`.
- **Banned in `backend/web/`.** A test scans the templates and Python files for the `|safe` filter, `Markup(`, `{% autoescape false %}` and `autoescape=False`.
- **Banned in `app.js`.** A test scans for `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, `eval` and `new Function`. `app.js` writes text only with `textContent`.
- **Swapped HTML is already escaped.** htmx swaps in HTML that the server rendered with autoescape, so API text arrives escaped. `allowEval: false` stops htmx itself from running code in attributes (Section 11.2).

### 4.3 Enforcement (M6): structure first, words as a tripwire

The guarantee is structural: it comes from what the templates *can* read, and from a snapshot of everything they *can* say. The word scan stays, but only as a cheap tripwire.

1. **Only one function reads `outcome`.** `backend/web/views.py` has `should_show_downloads(turn) -> bool`. It's true only when `turn.outcome == "delivered"`, and the downloads then come only from `delivered_files`. The view model passed to templates (`TurnView`) has `reply`, `downloads: list | None` and the other display fields, but **no `outcome` field**, so a template can't branch on it. A test fails if the string `outcome` appears anywhere under `backend/web/` (templates, static files or Python), except in `should_show_downloads` and its test.
2. **`outcome` only changes layout, attributes included.** Render the same reply under each of the four outcomes, and remove the download area's subtree. The four HTML trees must then be identical: tags, text **and attributes** (classes, `data-*`, `aria-*`, `style`). A small helper built on the standard library's `html.parser` compares them.
3. **The reply slot holds only `turn.reply` (M5).** For every rendered turn, the text of the reply slot (`[data-slot="reply"]`) equals `turn.reply` exactly. When the turn isn't finished, the slot is absent. No page notice or connection message can render inside it.
4. **Verbatim.** Take a finished turn whose `reply` is a sentinel containing markup-like text (`<b>x</b> **y** http://a.b {{ 1 }}`), newlines, and leading and trailing spaces. Under each outcome, the slot's text equals the sentinel, and no element was created from it.
5. **No download unless delivered.** `outcome: "not_delivered"` with a non-empty `delivered_files` renders no link.
6. **Nothing that looks like a result before `finished`.** `running` and `awaiting_confirmation` render no reply slot, and no file names other than those in the plan.
7. **The M3 guard.** When `steps` is missing, `null` or empty, the pending confirmation renders no Approve button. It renders Reject and the fixed text.
8. **Session scoping (H1).** After the pointer moves to session B, a fragment request for session A gets A's `404`, never B's data. B's `t0001` never renders A's turn. The swap guard drops a fragment with another `data-session-id` (checked in e2e).
9. **Fallback.** `reply_source: "pipeline_fallback"` shows `reply` unchanged.
10. **HTML equals JSON.** For every finished turn rendered in the tests, the reply slot's text equals the JSON API's `reply` for the same turn.
11. **A snapshot of the UI copy.** A test extracts every string the UI writes:
    - template text nodes;
    - the attributes `title`, `aria-label`, `placeholder` and `alt`, `value` on buttons, and `content` on `<meta>`;
    - `<title>`;
    - `content:` values in `styles.css`;
    - quoted string literals in `app.js`.

    It compares the sorted list with the committed `tests/web/ui_copy_snapshot.txt`. Any new or changed sentence shows up as a diff, which the independent reviewer reads and signs off in the review file. The snapshot is regenerated with a pytest option (`--update-ui-copy`) and committed along with the change.
12. **A word tripwire.** The banned list from 4.1 rule 2 (case-insensitive, whole words), scanned over the templates, `styles.css` and `app.js`. A reviewed allowlist covers request copy such as "Upload failed" if it's ever needed. It catches the obvious cases, but it isn't the guarantee.
13. **A review checklist item.** The independent reviewer reads the snapshot diff, and checks every template change against 4.1.

### 4.4 Also not allowed (outside 4.1)

- **No toasts** or other pop-up messages that disappear on their own (review 3.1). Every UI message has a fixed place: an error slot next to its control, or the page-notice area.

---

## 5. Accessibility basics

Target: WCAG 2.2 AA for the flows above.

- **Structure.** One `<main>`, a `<header>`, labelled regions (Files, Conversation, Message), one `<h1>`, and headings that don't skip levels. The transcript is a plain list. `role="log"` isn't used.
- **Live announcements.** One visually hidden `aria-live="polite"` region, whose contents the fragments update out of band (`hx-swap-oob="innerHTML:#live"`). The region element itself is never replaced, so screen readers keep watching it. It announces:
  - "Buddy is working", when a turn starts;
  - "A plan needs your approval", when the card appears;
  - the reply text itself, exactly as `reply`, when a turn finishes.

  Polls that find no change don't touch it.
- **Focus.**
  - When the plan card appears, focus moves to its heading (1.7). Polls that find no change don't render the card again, so they don't take focus.
  - When a turn finishes, focus goes to the composer.
  - On a gate error, focus returns to the password field.

  There are no focus traps. The card is inline, not a modal.
- **Keyboard.** Everything works by keyboard. Every control has a visible focus ring (at least 3:1 contrast) that is never removed. Upload is a real `<input type="file">`.
- **Labels.** Every input has a `<label>`. The download link names the file. Error slots are tied to their control with `aria-describedby`, and request errors use `role="alert"`.
- **Colour and contrast.** Text at least 4.5:1, UI components at least 3:1, light and dark themes through `prefers-color-scheme`. Nothing conveys meaning by colour alone, and outcomes have no colour meaning (4.1 rule 3).
- **Page notices** look different from replies (a bordered notice, not a chat bubble) and sit in their own region, so nobody reads them as Buddy speaking (M5).
- **Motion.** The spinner respects `prefers-reduced-motion`.
- **Zoom.** Usable at 200% zoom and 320 px width. Font sizes are in `rem`.
- **Touch targets** are at least 44×44 CSS px.
- **Language.** `<html lang="en">`. Replies get no `lang`.
- **What is checked in the demo:** one axe run over the gate, the workspace, the plan card and a finished turn, with the result recorded in the FE-1 review. The run can use axe-core injected by a Playwright script, or the browser extension. These are deferred (Section 16), and the FE-1 review must say which of them were done:
  - axe in CI;
  - the three-viewport matrix;
  - the manual NVDA or VoiceOver pass.

---

## 6. State and data handling (H1, L1)

- **The server holds the state.** The browser holds the DOM the server rendered, and two cookies. It has no turn cache, no details cache, no `localStorage` and no `sessionStorage`.
- **The access cookie, `__Host-wb_access`.** Set by `POST /api/v1/access`, with the attributes and lifetime from the backend design (Decisions 2026-10-01). It proves the password was given. It doesn't name a session.
- **The session pointer, `__Host-wb_session`:**
  - set by `POST /ui/sessions`, and holds the WorkBuddy `session_id`;
  - attributes `HttpOnly; Secure; SameSite=Strict; Path=/`;
  - no `Max-Age` or `Expires`, so it lasts until the browser closes.

  Only `GET /` reads it, to decide which session to render. Every fragment URL carries the session id in its path, so the pointer never decides which session a fragment request acts on (1.3).
- **L1, shared machines.** The review's concern was a `session_id` in `localStorage` that survives closing the browser on a lab or library computer. The next person types the same public password and resumes that session. A cookie with no expiry is cleared when the browser closes, as `sessionStorage` is, and unlike `sessionStorage`, no script can read it. There are two differences:
  - all tabs of that browser share it, so there's one session per browser, not per tab;
  - [Likely] browsers that restore the previous session on start ("continue where you left off") restore these cookies, just as they restore `sessionStorage`.

  Section 15 lists this as a change of mechanism from the review.
- **The gate test** checks that nothing is written to `localStorage` or `sessionStorage`, and that the password appears in no cookie, URL or page.
- **No caching of responses.** Every HTML and API response has `Cache-Control: no-store`. Static assets are cached by a versioned file name (11.2).
- **Multiple tabs** share one session, because there's one pointer per browser:
  - While one tab runs a turn, the other gets `409 session_busy`, refetches the transcript and polls that turn.
  - If both tabs answer the same plan, one of them gets `409 stale_confirmation`.
  - If one tab moves to a new session, the other's next fragment request gets `404 session_not_found`, and it reloads into the new session.

---

## 7. Copy and content

- **All UI text lives in templates:** one file of copy macros and `<template data-copy>` elements (`backend/web/templates/_copy.html`), plus the templates for each screen that use them. `app.js` has no text of its own. The snapshot test (4.3, item 11) covers all of it.
- **Page voice, never Buddy's (M5).** UI copy is impersonal ("This page…", "The server…", "That session…") and never says "I". It appears in the page-notice area or in an error slot next to its control, styled as a notice, and never in the reply slot.
- **The fixed sentences in this design:**
  - "That session no longer exists on the server, so this is a new one."
  - "This page lost contact with the server before the turn finished, so its result isn't shown. Reload the page to check."
  - "This page can't reach the server."
  - "The server didn't confirm receiving that message."
  - "Your answer may not have reached the server."
  - "The server didn't send the plan, so it can't be approved here."
- **Labels and short strings:**
  - "Buddy is working", "Sending your answer…", "A plan needs your approval";
  - "Plan for your approval", "Approve plan", "Reject plan", "about N s left";
  - "Enter the password again", "Approval needed – WorkBuddy", "WorkBuddy demo";
  - "Friend: chattier. Focus: brief."
- **The demo notice:** this is a demo, don't upload sensitive data, and uploaded files are deleted after N hours. The last part appears only when the server provides N (1.3).
- **No future promises about work.** The page never says Buddy "will" do something to a file. The only forward-looking text about work is the backend's plan text, which describes a proposal.

---

## 8. Mobile layout

- **Single column below 720 px,** with mobile-first CSS. From top to bottom: the header (title and mode toggle), the conversation, and the composer. The composer is pinned to the bottom (`position: sticky`, `padding-bottom: env(safe-area-inset-bottom)`), and the page uses a `100dvh` layout so the on-screen keyboard doesn't hide it.
- **The Files panel** becomes a `<details>` section above the conversation. It's closed once a file exists, with the active file's name in its summary. The upload button is a large, full-width control.
- **The plan card** is full width, with Approve and Reject stacked and the same size. The reply is full width with no fixed height. Download links are full-width controls.
- **Viewport.** `<meta name="viewport" content="width=device-width, initial-scale=1">`. Inputs use `font-size ≥ 16px`, so iOS doesn't zoom on focus.
- **Tested in the demo:** one viewport, 360×640, in the golden-path e2e test (10.3). It checks that nothing overflows horizontally (`scrollWidth <= clientWidth`), and that the composer and the plan card's buttons can be reached. 390×844 and 768 px are deferred (Section 16).
- **Picking a file.** On a phone, choosing a spreadsheet goes through the OS file picker, which the standard input supports.

---

## 9. Files and rough size

These are estimates, not measurements, since nothing is prototyped. For comparison, the previous React revision estimated about 2,200 app lines and 1,800 test lines.

| Path | Purpose | Est. lines |
|---|---|---|
| `backend/web/__init__.py`, `routes.py` | The HTML routes in Section 2, and the session pointer cookie | 220 |
| `backend/web/views.py` | `TurnView` and the other view models, `should_show_downloads`, the time-left text, the poll cadence | 140 |
| `backend/web/templates/` (`base.html`, `gate.html`, `workspace.html`, `files.html`, `turn.html`, `plan_card.html`, `poll.html`, `_copy.html`) | All markup and copy | 350 |
| `backend/web/static/app.js` | Section 3.3 | 150 |
| `backend/web/static/styles.css` | Tokens, layout, mobile, dark mode, focus, notices | 300 |
| `backend/web/static/htmx.min.js` | Vendored, pinned, unmodified | (vendored) |
| **App total** | | **~1,160** |
| `tests/web/` (`test_rule1.py`, `test_routes.py`, `test_plan_card.py`, `test_session_scope.py`, `test_copy_snapshot.py`, `ui_copy_snapshot.txt`, a tree-comparison helper) | HTML-route tests (10.1) | 800 |
| `tests/e2e/` (a `conftest.py` launcher and five tests) | Browser tests (10.3) | 350 |
| `Dockerfile`, `.dockerignore` | Single-stage image (FE-2) | 25 |

Dependencies and wiring:
- `requirements.txt` gains `jinja2` explicitly (it's already installed).
- Dev-only: `pytest-playwright` and the `playwright` Python package.
- The backend's `main.py` mounts `/static` and includes the web router. That's about 20 lines, in FE-1.

---

## 10. Testing

### 10.1 HTML-route tests (pytest, `TestClient`, fake engine)

These use the backend's `create_app(settings, engine_factory)` with the queued fake LLM (backend 7.1) and a temporary data root. They set `base_url="https://testserver"`, because the cookies are `Secure` and httpx sends them only over HTTPS. The cases:
- **Rule 1:** all of Section 4.3, items 1 to 11. Item 12 is a tripwire test.
- **Gate:**
  - a wrong password shows the API's message;
  - a `204` sets the access cookie and nothing else;
  - the gate renders without a cookie, and the workspace with one;
  - no web storage is used (checked in e2e).
- **Session lifecycle:**
  - `GET /` creates nothing;
  - `POST /ui/sessions` sets the pointer;
  - a pointer to a missing session renders the new-session notice exactly once and clears the pointer;
  - fragment routes for another session return `404`.
- **Upload:**
  - success re-renders the list with the server's name;
  - `413`, `415`, `422 invalid_file` and `422 multiple_sheets` leave the list unchanged;
  - the control is disabled while a turn is unfinished.
- **Turn and poll** (with an injected clock):
  - `POST …/turns` renders a running turn with a poll element;
  - a poll returns a re-armed element with `delay:2s` while the turn is under 20 s old, and `delay:3s` after;
  - a status change returns the whole container, with a higher `data-version`;
  - a finished turn has no poll element.
- **Plan card:**
  - the steps appear in order, verbatim, in an `<ol>`;
  - `plan_note` appears verbatim when present, and not at all when `null`;
  - Approve appears only with a non-empty `steps` (4.3 item 7);
  - "about N s left" comes from the server clock (injected), and no client expiry text appears anywhere;
  - a poll that finds no change updates only the time-left text, out of band;
  - the confirmation response never contains the answered card;
  - `unconfirmed=` adds the fixed line only when the id it names is the one still pending.
- **Ambiguous POST.** The transcript route returns the turns after `last_seq`. The check itself is covered in e2e.
- **Errors.** Each row of the 1.10 table that the server decides (status, code and body), on the HTML routes.

### 10.2 What these tests can't show

`TestClient` doesn't run `app.js` or a browser, so these tests can't show:
- the swap guard;
- the `Retry-After` waits, the backoff and the five-failure limit;
- the lost-confirmation path;
- how cookies behave in a real browser;
- focus movement and layout.

The e2e tests cover the ones that matter for rule 1. The rest is left to review and the recorded axe run.

### 10.3 End-to-end tests (`pytest-playwright`, real FastAPI, fake LLM)

A launcher fixture starts the real app with `create_app(settings, engine_factory)`: the real pipeline, queued fake replies, a temporary data root, a known demo password, and **the production rate limits**, not relaxed ones. It lives in `tests/e2e/conftest.py`, so production has no test-only routes. Chromium is already installed in cloud sessions (`/opt/pw-browsers`). The five scenarios, following review Section 3.6:

1. **The golden path, at 360×640:**
   - pass the gate, and upload the standard fixture (6 rows, 2 exact duplicates, 1 empty row, as in `scripts/run_dedupe_manually.py`);
   - send "remove duplicate rows, then remove empty rows";
   - check that the plan card lists both steps in order, with the chain wording and `plan_note`;
   - approve, wait for the reply, and download through the link;
   - **independently check** the file: a valid `.xlsx`, the expected row count, and a sha256 equal to the API's `delivered_files` entry.

   The test also checks that:
   - the reply text on the page equals the JSON `reply` of the same turn;
   - the original upload's sha256 on disk is unchanged (rule 2);
   - the unknown-outcome notice never appeared, at production limits (H2);
   - nothing overflows horizontally;
   - nothing is in `localStorage` or `sessionStorage`.
2. **Reject:**
   - no download area appears;
   - the reply shown is the JSON reply;
   - the list has no delivered file;
   - the original is unchanged.
3. **Pipeline failure.** A fake LLM that raises produces the pipeline's failure reply (backend R6), shown verbatim with no download area.
4. **Approval timeout.** The launcher sets a short confirmation timeout. If the user does nothing, the turn finishes with no downloads, and the card never says the window closed before the server did.
5. **Lost connection, then reload:**
   - the test blocks the poll route in the browser (Playwright routing) for long enough to reach five failures;
   - the page shows the page-voice notice, outside the reply slot;
   - after unblocking and reloading, the page shows the finished turn as the server has it.

Deferred (Section 16): the scenarios for clarification, `.xls`, restarts, rate-limit messages and extra viewports, and axe in CI.

### 10.4 Live runs (required by `CLAUDE.md` before FE-1 merges)

Before FE-1 merges, run real-Groq turns through the browser against the app:
- a plain turn;
- a two-step chain, approved;
- a rejection;
- a request the pipeline can't do.

Record them in `docs/reviews/frontend-demo-live-runs.md`, in the format `CLAUDE.md` requires: the intent, `clarification_required`, the steps, whether a file was delivered, an independent check of the file, the reply, whether it makes a false claim, and the original's hash before and after. Also compare the page's reply text with the API's `reply`. Stop at the first Groq `429`, and report how many runs were completed.

### 10.5 Not tested, said plainly

- visual design;
- real assistive technology (there's no manual screen-reader pass in the demo);
- browsers other than Chromium;
- more than one viewport;
- performance under many users. The bottleneck there is the backend's one worker and two-turn cap.

---

## 11. Deployment with the backend for the public demo

### 11.1 One container, one process

FE-2 adds a single-stage `Dockerfile` at the repo root and a `.dockerignore` that sends only `backend/`, `buddy/`, `core/`, `skills/` and `requirements-runtime.txt` to the build. The image:
- starts from `python:3.11-slim` (the minimum version, backend Decisions item 3);
- installs `requirements-runtime.txt` (the runtime packages only; `requirements.txt` includes it and adds the test and lint tooling, which the image doesn't carry);
- copies `backend/`, `buddy/`, `core/` and `skills/`;
- runs as a non-root user (uid 10001) that can write only to `/data`, where the data root is (`WORKBUDDY_DATA_DIR=/data`; mount a volume there to keep sessions across restarts);
- runs `python -m backend --port "$PORT"` with one worker, in production mode (`WORKBUDDY_ENV=production`, bound to `0.0.0.0`, `PORT` defaulting to 8000).

**Where this differs from the first design.** The first design ran `uvicorn backend.main:app --host 0.0.0.0 --port $PORT --workers 1`. `backend.main` has no module-level `app`: `create_app()` reads the settings when it is called, and a bad setting has to stop the server with a message that names it. `python -m backend` is the entry point that does that, and the one the live runs use, so the image uses it.

There's no Node stage, because the templates and static files are part of `backend/web/`.

Routes:
- `/api/v1/*`: JSON;
- `GET /` and `/ui/*`: HTML;
- `/static/*`: `app.js`, `htmx.min.js`, `styles.css`;
- `/healthz`: the image's `HEALTHCHECK` calls it (liveness only; whether an LLM key is set isn't a reason to restart).

Configuration is by environment variable, all from the backend design. `docs/deploy.md` lists every one, with its default and what happens if it's wrong. The ones a host must set are `WORKBUDDY_DEMO_PASSWORD`, `WORKBUDDY_PUBLIC_ORIGIN` (the exact `https://…` origin, which the CSRF check uses, backend Decisions 2026-10-01) and `GROQ_API_KEY`. In production the server won't start without the first two.

Secrets go in the host's secret store, never in the image or the repo.

Because everything is served from one origin, there's no CORS configuration (`WORKBUDDY_CORS_ORIGINS` stays empty), and the cookies go only to the host that served the page.

### 11.2 Headers and htmx configuration for a public page

- **CSP:** `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'`. `frame-ancestors 'none'` also stops the Approve button being clickjacked from inside a frame.
- **htmx configuration,** set with a `<meta name="htmx-config">` tag:
  - `allowEval: false`. That rules out `hx-on`, trigger filters and `js:` values, which would need `unsafe-eval` under this CSP anyway.
  - `includeIndicatorStyles: false`. [Likely] Otherwise htmx injects an inline `<style>`, which the CSP blocks.
  - `selfRequestsOnly: true`.
  - History caching off. The page never pushes URLs, and [Likely] htmx's history cache would store page HTML, replies included, in `localStorage`.
- **Other headers:**
  - `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`, on every response (FE-2 added the second; FE-1 sent only the first);
  - HSTS in production;
  - `Cache-Control: no-store` on every HTML and API response, and on an error from `/static/*`;
  - long, immutable caching for `/static/*` (`public, max-age=31536000, immutable`) **when the URL carries the file's current version**.
- **Who sets them.** The backend sets these headers, because PR-4 owns hosted-demo hardening (`SecurityHeadersMiddleware`). FE-2 adds the parts that are specific to static files.
- **Static files and their version (a change from the first design, which put the version in the file name).** The pages link `/static/app.js?v=<first 12 hex digits of the file's SHA-256>` (`static_url` in `backend/web/templating.py`). A URL whose `v` is the file's current one is cached for a year, because it can never mean anything else. Any other URL for the file (no `v`, an old one) gets `Cache-Control: no-cache`, so a browser revalidates with the ETag and gets a `304`. A deploy that changes a file changes its URL in the pages, and nothing stale can be served from the cache. File names stay as they are, so there's no build step and no renaming.

### 11.3 Where to host it (M7)

**What the app needs:**
- a Docker container (the image of 11.1, which names no host);
- one long-lived process with threads, not serverless: turns run for tens of seconds, a confirmation wait holds a thread for up to 120 s, and all state is in memory;
- enough RAM for pandas, `ydata-profiling` and `splink`. `docs/deploy.md` has the measured idle and peak memory of the image for the small fixture and for a 2 MB file (single step and two-step chain), and which of 512 MB, 1 GB and 2 GB it survives. They replace the estimates the first design quoted from backend part 2 (about 300 MB at import, 0.9–1.0 GB for two concurrent 5 MB turns, before the 2 MB cap);
- outbound HTTPS to Groq, TLS, and secrets.

**The first design's Option A, a free Hugging Face Space, is gone.** It assumed the free CPU tier. Hugging Face's documentation (`huggingface.co/docs/hub/en/spaces-overview`) now says Gradio and Docker Spaces require a paid plan, as the owner reported on 2026-10-06. [Certain that the owner said so; not checked by the author of this section, because `huggingface.co` is blocked by this session's network policy, as it was for the review.] So the free-tier case for a public demo no longer holds, and what was argued for and against that option no longer decides anything. Two of its points apply to any host, and are kept in `docs/deploy.md`: an ephemeral disk makes `404 session_not_found` and the new-session notice (1.3) the normal restart path, and the page must be opened at its own address, not inside someone else's frame.

**The owner will choose the host from the measurements.** What the choice has to settle:
- **A paid container host with 1 to 2 GB of RAM** (a small VPS, or a platform that runs a Docker image all the time): the code stays private, there's no cold start, and with a persistent volume the restart repair applies (backend 7.5, run 8). It costs a few dollars a month and needs a card. Prices aren't checked here.
- **Which memory size.** The measurements say what the image needs at 2 MB; the backend's `WORKBUDDY_MAX_CONCURRENT_TURNS` (default 2) is the knob if the host is small.
- **Code visibility.** A host that builds from a public repository publishes a `Proprietary` codebase. A private repository or a pushed image avoids that. The owner's decision, precondition 5 in 13.2.
- **Not suitable:** serverless and scale-to-zero platforms, because of the in-memory state, the single worker and threads that outlive a request; any free web tier that spins down when idle or has too little RAM [Likely, not checked].

**Evidence that would change this:** Groq's limits under real traffic, and a measurement on the chosen host itself (13.2 item 6).

### 11.4 Local development

`uvicorn backend.main:app --reload` serves the API and the UI together. There's no separate frontend dev server. `WORKBUDDY_ENV=dev` runs without a demo password (backend Decisions item 1).

[Likely] Chromium and Firefox accept `Secure` and `__Host-` cookies on `http://127.0.0.1` and `http://localhost`, because they treat those as secure contexts. If a browser doesn't, the session pointer won't stick in dev. The fix is a dev-only cookie name without the prefix, not weaker attributes in production.

---

## 12. What the frontend needs from the backend, and its status

| # | Need | Status |
|---|---|---|
| 1 | `pending_confirmation.steps`, in execution order | Accepted (backend part 1 item 2, part 2 ask 1) |
| 2 | `limits` and `retention_hours` (`null` until the sweep exists) in the session response | Accepted (part 2 ask 2). The HTML layer reads them from the same settings |
| 3 | A committed `openapi.json` | Accepted (part 2 ask 3), but **the frontend no longer uses it**, because it has no generated types. The backend keeps it |
| 4 | Authentication for the browser | **Changed:** the access cookie, through `POST /api/v1/access` (Decisions 2026-10-01). Bearer stays for scripts and tests. The `401` body is unchanged |
| 5 | Download tokens and SSE | Not needed. Plain links carry the cookie, and polling fits the `poll` bucket |
| 6 | `Retry-After` on every `429` | Accepted (part 2 ask 6) |
| 7 | `reply_source` and `outcome` always set on finished turns | Accepted (part 2 ask 7). If a finished turn is missing either, the HTML layer treats it as an internal error and returns the backend's `500` with its fixed message, never a guess |
| 8 | CSRF protection for cookie auth | Added (Decisions 2026-10-01): SameSite=Strict, plus an exact `Origin` check on unsafe methods |
| 9 | Chain-aware step text and a fixed plan-level line (`plan_note`) | Added (Decisions 2026-10-01), M2 |
| 10 | No step-1-only compatibility fields in `pending_confirmation` | Added (Decisions 2026-10-01), M3 |
| 11 | Refuse a multi-sheet `.xlsx` at upload (`422 multiple_sheets`) | Added (Decisions 2026-10-01), as the H3 stopgap |
| 12 | The same authentication, CSRF check, rate-limit buckets and error handlers on the HTML routes | Added (Decisions 2026-10-01) |
| 13 | `expires_in_seconds` (review C7) | Not needed. The server renders the time left from its own clock (1.7) |

---

## 13. PR split and deployment preconditions

### 13.1 Two PRs (review Section 3.6)

Each PR goes on its own branch from `main`, and only the user merges it.

**FE-1 `feat/frontend-demo`** (rule-touching: one independent review, and live runs per 10.4).
- **It starts with** the half-day spike (3.4) against a stub turn service. The rest is built on backend PR-2's service functions.
- **Features:**
  - the gate, with the access cookie;
  - upload, and the composer with the mode radios;
  - polling at 2 s and then 3 s, with `429` handling (H2);
  - the plan card: the backend's chain text (M2), the M3 guard, and M4's server-relative time and lost-POST handling;
  - the verbatim reply, and download links only when delivered;
  - error messages verbatim;
  - page-voice notices outside the reply slot (M5);
  - session-scoped URLs and the reset on `404` (H1).
- **Tests (non-negotiable before review):** Section 4.3 items 1 to 11, the cases in 10.1, and the five e2e tests in 10.3.
- **It merges after** backend PR-3 (plan confirmation, `plan_note`) **and PR-4a** (password, access cookie, CSRF check, rate limiter). The gate needs the access route, and the H2 behaviour only means something against the real buckets. The review said "after PR-3", and Section 15 explains the change.

**FE-2 `feat/frontend-deploy`.**
- Contents: the Dockerfile and `.dockerignore`, the static caching, `Referrer-Policy`, the health check, `docs/deploy.md` (every environment variable, the 13.2 checklist, the measurements) and this section's host-neutral 11.3. No host is chosen.
- It also carries the fixes from the FE-1 review (`docs/reviews/frontend-demo-review.md`) and a fix to the workbook test fixtures, which `docs/reviews/frontend-deploy-impl.md` explains.
- It merges after backend PR-4, which has merged.
- Its review covers the headers, the static caching and secrets handling, and, because it carries the FE-1 fixes to the page's script and routes, it went through the review loop.

### 13.2 Preconditions for the public link (H3, M7)

The demo link goes public only when **all** of these hold:
1. **Merged:** backend PR-1 to PR-4, FE-1 and FE-2.
2. **Multi-sheet `.xlsx` (H3a).** Backlog 1a is closed, or the upload-time multi-sheet refusal (backend Decisions 2026-10-01) is merged with its tests passing.
3. **`.xls` (H3a), an owner decision.** The stopgap can't count the sheets in an `.xls`. One of these has to be true:
   - `.xls` stays out of `accepted_extensions` on the public link until it can be checked; or
   - the owner accepts, in writing in the backend Decisions, that a multi-sheet `.xls` can still get a "cleaned copy" reply for a file that has lost sheets.

   **Settled (owner, recorded 2026-10-06 with FE-1):** `.xls` is out of scope. This is backend "Decisions (2026-10-04)", item 1: `accepted_extensions` is `[".xlsx"]` and an `.xls` upload is refused with `415`. The first option holds, and this item is met. Turning `.xls` on later needs a new owner decision.
4. **PR-B (H3b), an owner decision.** The owner gives an explicit, written go or no-go on going public while PR-B / review M2 is open ("Done —" on a misclassified request, `buddy/outcome.py` `lead_word`). It's recorded in the backend Decisions section.

   **Settled (owner, recorded 2026-10-06 with FE-1):** PR-B is merged, so the question of going public while it is open no longer arises, and this item is met.
5. **Code visibility (M7), an owner decision, not yet made.** The owner decides whether the code may be public, which limits the choice of host (11.3).
6. **Measured on the host.** Backend part 2's live run 9 is repeated on the chosen host. If a two-step chain on 5 MB takes more than 120 s, its 2 MB fallback is applied.
7. **The proxy setting is tested.** The rate limiter's `WORKBUDDY_TRUSTED_PROXY_HOPS` is tested for the chosen host (review H2).

---

## 14. Risks, open questions, evidence that would change this

- **[Certain] The biggest rule-1 risk is still a future contributor adding a "helpful" sentence or style.** The structural guard, the attribute comparison and the copy snapshot make it visible in a diff, but a person still has to read it.
- **[Certain] The frontend can't fix rule-1 breaches that are in the reply itself** (multi-sheet, PR-B). That's why they're preconditions in 13.2.
- **[Likely] The htmx details in 3.4 are unverified until the spike.** It may show that polls that find no change can't avoid rendering the card again, or that the `HX-Retarget` pattern is fragile. The fallback is then to swap the whole turn container on every poll, with `app.js` restoring the card's focus, which is worse for keyboard users.
- **[Possible] The shared password becomes public.** Then the rate limits and quotas are the only controls (backend Decisions, "honest limits").
- **[Possible] Users who arrive from links are annoyed** at having to pass the gate again because of SameSite=Strict (1.2).
- **[Possible] A failed plain-link download** shows the browser's own failure UI instead of the API's message (1.8).
- **[Guessing] The line counts** are estimates.
- **Open questions:**
  - whether the mode toggle should be set per message rather than per session (this design assumes per session);
  - whether the retention notice needs legal review, for a public demo that accepts users' files.
- **Evidence that would change this design** (from review 3.5):
  - the developer is fluent in React and not in htmx and Jinja;
  - within about three months, the UI has to grow into something highly interactive (sheet previews, editing, multi-file drag-and-drop);
  - the spike shows the polling fragment can't stay within the rate budget.

---

## 15. Where this differs from the review

**The owner's decision, kept over the review's advice:**
1. **`.xls` in the demo.** The review (H3a) says to leave `.xls` out of the demo until its sheets can be counted. The owner decided on `.xlsx` and `.xls` (backend Decisions item 4). This design doesn't remove `.xls`. It stays a supported type, still waiting on its fixture test (backend part 1 item 4, part 2 ask 2). Whether it's turned on for the public link before a sheet check exists is left to the owner, as precondition 3 in 13.2.

**Mechanism changes that follow from the owner's htmx-and-cookie decision:**

2. **The session pointer is a cookie that lasts until the browser closes, HttpOnly, rather than `sessionStorage` (L1).** With server rendering, `GET /` has to know the session without running a script. This keeps the property L1 asks for (the session id doesn't survive closing the browser), and makes the id unreadable to scripts. Section 6 lists the differences: one session per browser rather than per tab, and [Likely] it's restored by browser session restore, just as `sessionStorage` is.
3. **No `expires_in_seconds` in the JSON API (M4, C7).** The server renders "about N s left" from its own clock, which is what M4 asks for, without a new field.
4. **No client-side hash check on downloads (L3).** The review itself says that with cookie auth, plain links drop the blob and this check with it. The backend's `410` hash check remains.
5. **FE-1 merges after backend PR-4a, not just after PR-3.** The gate needs the access route, and H2's `429` handling only means something against the real buckets.

**Smaller choices:**

6. **Hidden tabs keep polling every 2 s and then 3 s (L4),** instead of the review's 10 s. That's within the `poll` bucket and needs no visibility code. The tab-title change is applied as the review suggests.
7. **The e2e case for H2 ("one full turn at production limits") is part of the golden path** (10.3 scenario 1), not a separate test.
8. **"No toasts" is in Section 4.4, not 4.1,** because the owner asked for Section 4.1's rules to stay unchanged.

---

## 16. Deferred

These aren't in the demo, and are stated here as not done. They're proposed for `docs/backlog.md`, which this change doesn't edit.
- **The "What happened" panel (M1),** redesigned to show only verified data.
- **Drag-and-drop upload.**
- **A live countdown on the plan card.** The demo shows the server's "about N s left", refreshed by each poll.
- **Showing the API's message for a failed download.** This needs a download done with `fetch`.
- **Accessibility checks:** viewports 390×844 and 768 px, axe in CI, and a manual NVDA or VoiceOver pass. The FE-1 review must say which were done.
- **`.xls` in the UI tests,** once backend Decisions item 4's test passes and the owner has decided 13.2 precondition 3.
- **More e2e cases:** clarification, rate-limit messages and server restart.
- **Backlog 1a.** The multi-sheet refusal is only a stopgap. Backlog 1a (keep every sheet, or refuse with a reply) is the real fix.
- **The Hugging Face terms** (11.3): the owner's report that Docker Spaces need a paid plan hasn't been checked against the page from a session that can reach it.
- **Logging out,** to end the access cookie before it expires.
