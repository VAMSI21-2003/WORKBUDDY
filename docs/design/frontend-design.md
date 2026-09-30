# Frontend design: the web UI for the public demo

**Status:** design only. Nothing in this document is implemented.

**Depends on:** `docs/design/backend-api-design.md` on the branch `design/backend-api` (PR open, not merged), including its "Decisions (2026-09-30)" section: public demo link, shared demo password, once-per-turn plan confirmation, Python 3.11+, `.xlsx` and `.xls`. Where this document names an API field, it is that document's field; where it needs something the backend design doesn't yet say, that is listed in Section 12 as an ask, not assumed.

**Rule-touching:** yes, rule 1. The backend design (Section 5.1) says the API can't stop a frontend from writing its own "Done!" banner. This design is where that is prevented, and the frontend needs its own independent review and live runs before merging (`CLAUDE.md`). Rule 2 is not touched by the UI (it never sees a path and can't write an upload), but the end-to-end test checks it anyway (Section 10.3).

---

## 0. Decisions in brief

1. **A small React + TypeScript + Vite single-page app, built to static files and served by the FastAPI process itself.** One container, one origin, no CORS, no second host (Sections 3, 11).
2. **The UI never authors a statement about the user's file.** `reply` is rendered as plain text, exactly as returned. `outcome` decides layout only (whether a download list appears). The UI has no "Done", "Success", "Saved" or "Removed" string, no success or failure icons or colors, and a test enforces it (Section 4).
3. **One screen after the password gate.** Files, chat, plan-approval card, details panel and downloads are parts of one page, not routes. There is no client router.
4. **Polling, not streaming.** The backend is asynchronous with polling (backend Section 0, item 2); the UI polls the turn every 1 s.
5. **No state library and no UI kit.** Runtime dependencies: `react` and `react-dom`. Everything else is dev-only.
6. **Tests:** component tests against an injected fake API client, and an end-to-end browser test against the real FastAPI app with the fake LLM (Section 10).
7. **Suggested host: a single Docker container on Hugging Face Spaces** for the free demo; a small always-on VM (Fly.io or similar) as the fallback. Claims about pricing and limits are from memory and unverified (Section 11.3).

---

## 1. Screens and flow

### 1.1 Overview

```
 Gate ──(password accepted, session created)──▶ Workspace
                                                   │
        upload ─▶ send message ─▶ [running] ─▶ [awaiting plan approval] ─▶ [running] ─▶ finished
                                                                                         │
                                                              reply shown verbatim ◀─────┘
                                                              (+ downloads if outcome == "delivered")
                                                              (+ "What happened" panel on demand)
```

Everything after the gate is the same page. State is in one reducer: `{ auth, session, files, turns, activeTurnId, composer, banner }`.

### 1.2 Screen 1: the demo-password gate

- A single password field, a "Continue" button, and one line of static text saying what this is (a public demo; uploaded files are deleted after a retention period, see 1.3). No account, no sign-up, no "forgot password".
- Submitting calls `POST /sessions`, with the password in the auth header the backend settles on (Section 12, ask 4). This doubles as the password check: there is no separate login endpoint, because there is no login state on the server, only a password checked per request.
- `401` shows the API's `error.message` verbatim under the field and keeps the field focused. `429 rate_limited` shows the message and the `Retry-After` wait, and disables the button until then.
- The password is kept in `sessionStorage` (cleared when the tab closes) and in memory. It is never put in a URL, a cookie or `localStorage`, and never logged. `sessionStorage` is readable by any script on the origin; the mitigation is that the app renders API text only as text (Section 4.2) and ships a strict CSP (Section 11.2).
- Any later `401` from any call returns the user to the gate with the API's message, and keeps their draft message in memory.

### 1.3 Screen 2: the workspace

Desktop layout (two columns); on narrow screens see Section 8.

```
┌──────────────────────────────────────────────────────────────────────────┐
│ WorkBuddy demo                         Mode: (•) Friend  ( ) Focus       │
├───────────────────────┬──────────────────────────────────────────────────┤
│ Files                 │  You:    remove duplicate rows, then empty rows  │
│ ┌───────────────────┐ │                                                  │
│ │ Drop a .xlsx/.xls │ │  ┌ Plan for your approval ─────────────────────┐ │
│ │ or [Choose file]  │ │  │ 1. Remove duplicate rows from report.xlsx   │ │
│ └───────────────────┘ │  │ 2. Remove empty rows from report.xlsx       │ │
│ report.xlsx  18 KB    │  │ [Approve plan]   [Reject plan]   2:00 left  │ │
│  (active)             │  └─────────────────────────────────────────────┘ │
│                       │                                                  │
│ Files from Buddy      │  Buddy:  <reply, exactly as returned>            │
│ report_dedup…xlsx     │          [Download report_dedup….xlsx (16 KB)]   │
│   [Download]          │          ▸ What happened                         │
│                       ├──────────────────────────────────────────────────┤
│                       │ [ message box                        ] [Send]    │
└───────────────────────┴──────────────────────────────────────────────────┘
```

(The plan card and the reply are never on screen together for one turn; they are shown together here only to label the parts.)

**Session lifecycle.** After the gate, `POST /sessions` returns `session_id`. It is kept in `localStorage` (a per-viewer convenience, wrapped in try/catch because it can throw) so a reload resumes with `GET /sessions/{id}`. A `404 session_not_found` (expired by the retention sweep, or the server restarted) starts a new session and shows a static notice: "I couldn't find your previous session, so this is a new one. Your earlier files aren't here." That sentence is about the session's existence, not about any work, so it makes no claim rule 1 cares about; it is shown only on that `404`, never as a guess.

**Retention text.** The retention promise on the page must be true. The number comes from the server (Section 12, ask 2), never a constant in the UI; if the server doesn't provide it, the page shows no number.

### 1.4 Upload

- A labelled `<input type="file" accept=".xlsx,.xls">` plus an optional drop zone. The drop zone is an enhancement; the button works alone.
- The client does **not** validate beyond an advisory extension check for a friendlier prompt. Type, size and structure are the server's decision (backend Section 6.2); the UI shows the server's `error.message` for `413`, `415`, `422` next to the upload control. If the server publishes its limits (ask 2) the UI may pre-check size to avoid a doomed upload; otherwise it doesn't guess.
- Uploading while a turn is running is disabled in the UI (the server also answers `409 session_busy`).
- On `201`, the file appears in the list with the server's sanitized `name` (not the browser's filename, since replies will use the server's), and becomes the active file. `POST .../turns` sends `file_id` when the user picks a different uploaded file.
- The list shows uploads (not downloadable, per backend Section 1.6) and, separately, files Buddy delivered (downloadable).
- `.xls` is listed as accepted only once the backend's `.xls` end-to-end test passes (backend Decisions, item 4); until then the UI's accepted list comes from the server's `accepted_extensions` (ask 2), so it can't advertise more than the server supports.

### 1.5 Chat with Buddy: friend and focus mode

- A mode toggle, two labelled radio buttons: Friend (default) and Focus. It is a per-session setting sent as `mode` with the next `POST .../turns` (and at session creation). The UI does not describe how the modes differ beyond static one-line hints, because tone is the pipeline's job; a hint like "Friend: chattier. Focus: brief." is UI copy about its own control, not about any result.
- The transcript is the session's `turns`, oldest first: the user's `message`, then Buddy's `reply` once `status == "finished"`. A turn that isn't finished shows a progress row (1.6).
- Composer: a multi-line `<textarea>` with a visible label, a `Send` button, Enter to send and Shift+Enter for a newline (stated next to the field for keyboard users), and a character counter against the 4,000-character limit (from the server if provided, else 4,000 used only as `maxLength`). The composer is disabled while a turn is running or awaiting approval, and after `409 session_busy`.
- On `429 server_busy` / `429 rate_limited` the message stays in the box and the API's message is shown; nothing is added to the transcript, because a refused request creates no turn (backend Section 1.3).

### 1.6 Polling a running turn

State machine per turn, driven only by the `status` field the API returns:

```
POST /turns ─202─▶ running ──poll──▶ running …
                     │                 │
                     │                 └─▶ awaiting_confirmation ─(user answers)─▶ running …
                     └──────────────────────────────────────────────────────────▶ finished
```

- After `202`, poll `GET /sessions/{id}/turns/{turn_id}` every 1 s, backing off to 2 s after 20 s. Stop at `finished`. Polling pauses when the tab is hidden and resumes with an immediate poll when it's visible. Only one poll is in flight at a time; a stale response is ignored (compared by request sequence number).
- While `running`: a progress row that says "Buddy is working" with a spinner. That text is true exactly while the API says `running`, and the row is removed when the API says anything else. The UI never estimates progress and never says which step is running, because `detail` isn't populated until the turn finishes.
- While `awaiting_confirmation`: the plan card (1.7). The other controls stay disabled.
- **Connection loss.** A failed poll is retried with backoff, and the row changes to a static "Lost connection to the server; retrying" while it's retrying. After 5 consecutive failures (about 30 s) the UI stops and shows: "I couldn't reach the server to check this turn, so I don't know how it ended. Reload the page to check again." It says "I don't know", which is the only true statement it has. On reload, `GET /sessions/{id}` restores the turns, and a still-running or awaiting turn resumes polling.
- **Ambiguous POST.** If the network fails on `POST .../turns` itself, the server may or may not have created a turn. The UI doesn't retry the POST (it could double-run a write). It reloads session state; if a new turn exists it is shown, and if none exists the draft is restored to the composer with the message "The server didn't confirm receiving that message." Both are statements about the request, not about work.

### 1.7 The once-per-turn plan approval card

Backend "Decisions" item 2: before the first file-changing step the user sees the whole plan and approves or rejects it once; approval covers every step of that turn; reject means no step runs.

- Rendered from `pending_confirmation.steps` (ask 1): an ordered, numbered list, one row per step, each row the API's `prompt` text verbatim (which already names the file). The UI does not reword steps, merge them, or add steps it thinks are implied.
- Two equally prominent buttons, **Approve plan** and **Reject plan**, plus the time left computed from `expires_at`. Neither is pre-focused; focus moves to the card's heading when it appears so screen-reader and keyboard users land on the plan, and Tab reaches the buttons. (Not auto-focusing "Approve" prevents an accidental Enter-key approval.)
- Clicking sends `POST .../confirmation` with the `confirmation_id` and `decision`. Both buttons disable while the request is in flight (double-click safe). `409 no_pending_confirmation` or `409 stale_confirmation` means the plan changed under the user (expired, or answered elsewhere): the UI refetches the turn and shows whatever the API now says. It does not assume the decision took effect.
- After answering, the card is replaced by the progress row, and then by the reply. **The UI never writes "Plan approved, running" or "Plan rejected, nothing was changed".** After a reject the pipeline's reply says what happened; the card simply disappears. (A static label on the button while the click is in flight, such as "Sending your answer…", is about the click, not the file.)
- At `expires_at` the countdown reads "Approval window closed" (a statement about the window). It doesn't say the plan was rejected; the next poll returns the finished turn and its reply, which will say it, in the pipeline's words, if it's true (timeout is treated as reject by the backend).
- If a turn has no Write-tier step, no card appears (backend Decisions item 2, step 5); the UI has no path that shows one anyway.

### 1.8 Finished turn: the reply and the delivered-file download

- The reply is shown in the transcript as `turn.reply` in a text node, `white-space: pre-wrap`, with nothing before or after it that the UI wrote about the work (rules in Section 4).
- **Downloads:** if `outcome == "delivered"`, render one button per entry in `delivered_files`: label is the file's `name` and human size, e.g. "report_deduplicated_….xlsx (16 KB)". The button label is a file name, not a claim. For any other outcome the download area is not rendered at all.
- **How the download works.** Every route needs the demo password header, and a plain `<a href>` can't send headers, so the button does `fetch(download_url, {headers})` → `Blob` → an object URL → a temporary `<a download>` click, then revokes the URL. Memory cost is the file size (bounded by the upload cap, since outputs derive from uploads). `410 file_unavailable` or `404` show the API's `error.message` next to that button. The UI doesn't verify the file itself; if it shows a size it's the API's `size`. (A URL-based download with a short-lived token would avoid the blob copy; deferred, ask 5.)
- Delivered files also appear in the sidebar list for later turns (`GET .../files`, `kind: "delivered"`, `downloadable: true`).

### 1.9 "What happened" details panel

- A disclosure under each finished turn, labelled "What happened", collapsed by default. Its content is fetched from `GET .../turns/{turn_id}` on first open, because the session state omits `detail` (backend Section 1.4).
- It renders `detail` as **data**: `intent`, `clarification_required`, and for each step `capability`, `permission_tier`, `status`, `verified`, `input_name`, `output_names`, plus `stopped_at`. Field names use fixed labels (table headers "Step", "Status", "Verified"); values are the API's, printed verbatim in a monospace style, with no restyling by value. A step with `"status": "success"` displays the API's word; the UI doesn't turn it into a green check.
- A line at the top says it is "raw details from the pipeline, for checking the reply against". That sentence is about the panel.
- The panel shows no skill prose (the API omits it, backend Section 1.3.1), so there's nothing further to sanitize. If `detail` is `null`, the panel shows "No details for this turn."

### 1.10 Error states

Every non-2xx response has the body `{ "error": { "code", "message" } }`; the UI shows `message` verbatim and switches on `code` for behavior only.

| Code / situation | UI behaviour |
|---|---|
| `401 unauthorized` | Back to the gate; draft kept in memory; message shown |
| `404 session_not_found` | New session, with the static notice in 1.3 |
| `404 turn_not_found`, `404 file_not_found` | Refetch session state; show the message next to what was clicked |
| `409 session_busy` | Refetch session state (a turn is running somewhere, maybe another tab) and resume polling if so |
| `413`, `415`, `422 invalid_file` | Message under the upload control; the list is unchanged |
| `422 invalid_request` | Message under the composer; draft kept |
| `429 rate_limited`, `429 server_busy` | Message plus `Retry-After` countdown; draft kept; Send stays disabled until it elapses |
| `410 file_unavailable` | Message beside the download button |
| `503 storage_full` | Message at the top of the page; upload disabled |
| `500 internal_error`, unknown code | The API's message; a Retry button that repeats only idempotent reads |
| Network failure | See 1.6 (poll) and the ambiguous-POST rule; for GETs a static "Can't reach the server" banner |
| Finished turn with `outcome` other than `delivered` | **Not an error state.** The reply is shown as usual, without a download area. The reply carries the explanation |

A turn with `reply_source == "pipeline_fallback"` is displayed like any other reply. The UI may add a small neutral "automatic message" label to it, a true statement about the source; it is optional and never worded as an apology or a status.

---

## 2. What the UI needs from the API (contract summary)

Used as-is from the backend design: `POST /sessions`, `POST/GET .../files`, `POST .../turns`, `GET .../turns/{id}`, `POST .../turns/{id}/confirmation`, `GET /sessions/{id}`, `GET .../files/{id}/download`, `GET /healthz`. The client is typed from the API's OpenAPI schema (`openapi-typescript`, generated at build time from a committed `openapi.json` that a backend test keeps in sync, ask 3), so a contract change breaks the frontend build instead of the user.

An `api/client.ts` module is the only place that calls `fetch`. It takes the password and base URL, returns typed results or a typed `ApiError { status, code, message, retryAfter? }`, and is injected into the app, which is what makes the component tests possible without a network.

---

## 3. Tech choice, and why

**Choice:** React + TypeScript + Vite, built to static assets, served by FastAPI's `StaticFiles` from the same process. No router, no state library (one `useReducer`), no component library, plain CSS with custom properties.

**Why this, from the requirements:**

| Requirement | Consequence |
|---|---|
| Cheap to host with the backend | Static output served by the existing FastAPI process: no Node in production, no second service, no CORS |
| The UI has real interactive state (polling, an approval card, a disclosure per turn, disabled states) | A component model with tests is worth having; vanilla JS would recreate one badly |
| Rule 1 needs mechanical enforcement | TypeScript plus a typed client make "which fields do we render" checkable; a test can inspect the component tree |
| Small bundle for a public link | Two runtime dependencies; expected under about 100 KB gzipped (an estimate, not a measurement) |
| Tooling | React + Vitest + Testing Library + Playwright is the most conventional stack; Playwright and Chromium are already available in the cloud sessions |

**The strongest alternative: server-rendered pages with htmx (Jinja templates in FastAPI).** It has no Node toolchain and less code, and polling and a confirm card are within its reach. I'm not choosing it because the app's correctness depends on client-side state that htmx handles awkwardly: drafts preserved across a `401`, a single in-flight poll with sequence numbers, per-turn lazy-loaded details, a blob download that carries a header, and countdown timers; and component tests can't be written against server-rendered fragments as directly. The price of my choice is a build step and a `package.json`. If the UI turned out to be three simple views, htmx would win; I expect it to grow (multi-file turns and more file types are deferred in the backend design), which is a guess, not evidence.

**Other options, briefly:** Next.js and other SSR frameworks need a Node server in production (a second process to host) for no benefit here; Svelte or Vue would work equally well, and React is chosen for tooling familiarity, not a technical edge.

Build: Node 20+ is needed at **build time only**, in a Docker build stage (Section 11.1).

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

### 4.2 Text-only rendering

All API strings (`reply`, `error.message`, `name`, `prompt`, `detail` values) are inserted as React text nodes. `dangerouslySetInnerHTML` is banned by ESLint (`react/no-danger`, as an error). This is both the rule-1 guarantee (rendering can't alter the text) and the XSS defence for the password in `sessionStorage`.

### 4.3 Enforcement (each is a test or a CI check)

- **Banned-strings check:** a test scans `src/**/*.{ts,tsx}` string literals and JSX text for the list in 4.1 rule 2 (case-insensitive, word-bounded) and fails on a match, with an allowlist file for reviewed exceptions (each needs a comment). Cheap and blunt on purpose. It won't catch a cleverly reworded claim, which is why review is still required.
- **Verbatim test:** render a finished turn whose `reply` is an arbitrary sentinel containing markup-like text (`<b>x</b> **y** http://a.b`), newlines and leading/trailing spaces, with each of the four `outcome` values; assert the DOM text of the reply node equals the sentinel exactly and that no elements were created from it.
- **Outcome-is-layout test:** the four outcomes with the same reply differ in the DOM only by the presence of the download area (compare the trees with that subtree removed).
- **No-download-unless-delivered test:** `outcome: "not_delivered"` with a non-empty `delivered_files` renders no download button.
- **Pre-finish test:** `running` and `awaiting_confirmation` render no reply node, no banned-list text, and no file names other than those in the plan.
- **Fallback test:** `reply_source: "pipeline_fallback"` shows `reply` unchanged.
- **Review checklist item:** the independent reviewer greps every string literal the UI adds and checks each against the rules in 4.1.

---

## 5. Accessibility basics

Target: WCAG 2.2 AA for the flows above.

- **Structure:** one `<main>`, a `<header>`, labelled regions (`Files`, `Conversation`, `Message`), one `<h1>`, and headings that don't skip levels. The transcript is a plain list; `role="log"` is avoided because its live behaviour is hard to control (see next point).
- **Live announcements:** one visually hidden `aria-live="polite"` region. It announces (a) "Buddy is working" when a turn is `running`, (b) the plan card's appearance ("A plan needs your approval"), and (c) the reply text itself when a turn finishes, exactly as `reply` (so what a screen reader speaks is what the eyes read, with no extra "success" phrasing). Poll updates never announce anything.
- **Focus management:** on plan-card appearance, focus moves to its heading. After the user answers, focus moves to the composer once the reply exists. On a gate error, focus returns to the password field. No focus traps; the approval card is inline, not a modal (a modal would stop the user from reading the transcript that led to the plan).
- **Keyboard:** everything works by keyboard; a visible focus ring on every control (≥3:1 contrast, never removed); the details panel is a real disclosure button with `aria-expanded`; file upload is a real `<input type="file">`; the drop zone is optional.
- **Labels:** every input has a `<label>`; icon-only buttons have accessible names; the download button names the file; errors are tied to their field with `aria-describedby` and `role="alert"` (used for request errors only).
- **Color and contrast:** text ≥4.5:1, UI components ≥3:1, light and dark via `prefers-color-scheme`. Nothing conveys meaning by color alone (and outcomes have no color meaning by rule 4.1.3).
- **Motion:** the spinner and any transitions respect `prefers-reduced-motion`.
- **Zoom and text size:** usable at 200% zoom and 320 px width without horizontal scrolling; font sizes in `rem`.
- **Touch targets:** ≥44×44 CSS px for buttons, radios and the file button.
- **Language:** `<html lang="en">`. Replies are whatever language the pipeline produced; the UI doesn't set `lang` on them.
- **Verification:** `@axe-core/playwright` in the end-to-end tests on the gate, workspace, plan card and finished-turn states. Automated checks catch only a fraction of issues, so a manual keyboard-only and screen-reader pass (NVDA or VoiceOver) is part of the review, and the review doc must say whether it was done.

---

## 6. State and data handling

- **In memory:** the reducer state (session, files, turns, active turn, banner, draft). The password lives here and in `sessionStorage`.
- **`localStorage`:** only `session_id` and the mode preference, both optional conveniences, every access in try/catch, and the app renders correctly without them.
- **Truth is the server's.** After any ambiguous event (reload, `409`, visibility change after a long hide, connection recovery), the UI refetches `GET /sessions/{id}` rather than trusting its local view.
- **No caching of API responses** beyond the reducer; the API sets `Cache-Control: no-store`.
- **Multiple tabs:** two tabs on one session are possible. The second tab gets `409 session_busy` while a turn runs in the first, refetches, and resumes polling that turn. Concurrent approval from both tabs gets `409 stale_confirmation` in one. The UI handles both by refetching; nothing else is done for cross-tab sync.

---

## 7. Copy and content

All UI-authored text lives in one file, `src/copy.ts`, so the banned-strings check (4.3) and the reviewer have a single place to read. Static text is limited to what Section 4.1 allows. The gate and footer carry the demo notice: this is a demo, don't upload sensitive data, and uploaded files are deleted after N hours (N from the server, ask 2). The page never says Buddy "will" do something to a file; it only shows what the API returned.

---

## 8. Mobile layout

- **Mobile-first CSS**, single column below 720 px: header (title, mode toggle), conversation, then a composer pinned to the bottom (`position: sticky`, `padding-bottom: env(safe-area-inset-bottom)`, `100dvh` layout so the on-screen keyboard doesn't hide it).
- The Files panel becomes a collapsible section above the conversation (collapsed once a file exists, with the active file's name in the summary), and the upload button is a large, full-width control.
- The plan card is full-width with stacked buttons, Approve and Reject the same size; the reply is full width with no fixed heights. The details panel and download buttons are full-width controls.
- `<meta name="viewport" content="width=device-width, initial-scale=1">`; inputs use `font-size ≥ 16px` so iOS doesn't zoom on focus.
- Test viewports: 360×640, 390×844 and a 768 tablet, in the end-to-end suite (Section 10.3), asserting no horizontal overflow (`scrollWidth <= clientWidth`) and that the composer and the plan card's buttons are reachable in the viewport.
- Picking a spreadsheet on a phone usually goes through the OS "Files" picker, which the standard input supports; camera-roll and cloud-provider specifics are out of scope.

---

## 9. Files and rough size

Estimates, not measurements; nothing is prototyped.

| Path (under `frontend/`) | Purpose | Est. lines |
|---|---|---|
| `package.json`, `tsconfig.json`, `vite.config.ts`, `eslint.config.js` | Tooling and scripts | 100 |
| `index.html` | Shell, viewport | 20 |
| `src/main.tsx`, `src/App.tsx` | Mount, gate vs. workspace | 90 |
| `src/api/client.ts`, `types.ts` (generated + hand types), `errors.ts` | The only `fetch` caller, typed errors, `Retry-After` | 220 |
| `src/state/reducer.ts`, `usePolling.ts`, `useSession.ts` | State machine, polling with sequence numbers and backoff, session resume | 330 |
| `src/components/Gate.tsx` | Password gate | 80 |
| `FilesPanel.tsx`, `UploadControl.tsx` | Upload, list, delivered list | 200 |
| `Transcript.tsx`, `Message.tsx`, `Composer.tsx`, `ModeToggle.tsx` | Chat | 260 |
| `PlanCard.tsx` | Once-per-turn approval | 130 |
| `Downloads.tsx` | Blob download with header | 90 |
| `DetailsPanel.tsx` | "What happened" | 120 |
| `Banner.tsx`, `Progress.tsx`, `LiveRegion.tsx` | Errors, working row, announcer | 110 |
| `src/copy.ts` | All UI-authored strings | 60 |
| `src/styles.css` | Tokens, layout, mobile, dark mode, focus | 380 |
| **App total** | | **~2,200** |
| `src/**/*.test.tsx`, `src/test/*` | Component tests, banned-strings check, fake client | 1,300 |
| `e2e/*.spec.ts`, the launcher, `playwright.config.ts` | Browser tests | 500 |
| `Dockerfile` (repo root), `.dockerignore` | Two-stage build | 40 |

Backend additions this needs that aren't in the backend design's file list: a `StaticFiles` mount, a `WORKBUDDY_STATIC_DIR` setting, and the CSP/HSTS headers, about 40 lines in `backend/main.py` and `settings.py`. The built `dist/` is a build artifact and is git-ignored.

Runtime dependencies: `react`, `react-dom`. Dev: `vite`, `typescript`, `@vitejs/plugin-react`, `vitest`, `@testing-library/react`, `@testing-library/user-event`, `@testing-library/jest-dom`, `jest-axe`, `jsdom`, `eslint` (with `react`, `react-hooks`, `jsx-a11y` plugins), `openapi-typescript`, `@playwright/test`, `@axe-core/playwright`. Lockfile committed.

---

## 10. Testing

### 10.1 Component tests (Vitest + Testing Library, fake API client)

The app is constructed with an injected `ApiClient`, so tests script whole conversations without a network. Cases:

- **Gate:** wrong password shows the API message and keeps focus; `429` disables until `Retry-After`; success stores nothing in `localStorage`, and does store in `sessionStorage`.
- **Upload:** success adds the server's sanitized name; `413/415/422` show the message and leave the list unchanged; disabled during a turn.
- **Turn lifecycle:** `202` → running row → poll to finished; polling backs off; one request in flight; a stale response is dropped; a hidden tab pauses.
- **Plan card:** renders `steps` in order verbatim; Approve and Reject each send the right body once (double-click sends one); `409` refetches; expiry shows the window text, not a decision; a turn with no pending confirmation renders no card.
- **Downloads:** button only when `outcome == "delivered"`; sends the password header; `410` shows the message; the object URL is revoked.
- **Details panel:** lazy fetch on first open, verbatim values, the `null` detail message.
- **Errors:** every row of the 1.10 table.
- **Connection loss:** five failed polls end at the "I don't know how it ended" message, and never say the turn failed.
- **Resume:** reload with a `running` and with an `awaiting_confirmation` turn restores the right UI.
- **Rule 1:** all the tests in Section 4.3.
- **Accessibility:** `jest-axe` on each component state, plus assertions on focus movement and the live region's text.

### 10.2 What component tests can't show

They fake the API, so they prove nothing about the real contract, header handling, real multipart upload, real download bytes or the real reply. That's the end-to-end suite's job.

### 10.3 End-to-end tests (Playwright, real FastAPI, fake LLM)

A launcher starts the real app with `create_app(settings, engine_factory)` from the backend design (its Section 7.3: real pipeline, queued fake LLM replies), a temporary data root and a known demo password, and prints the port and data root. It lives with the tests, not in the shipped app, so production has no test-only endpoints. The browser drives the built frontend served by that app, the same way it's served in production. Scenarios:

1. **Gate:** wrong password rejected; right password reaches the workspace.
2. **Golden path:** upload the standard fixture (6 rows, 2 exact duplicates, 1 empty row, as in `scripts/run_dedupe_manually.py`), send "remove duplicate rows, then remove empty rows", approve the plan (both steps listed, in order), wait for the reply, download the file, then **independently check** it: a valid `.xlsx`, the expected row count, and a sha256 equal to the `sha256` in the API's `delivered_files`.
3. **Reply is verbatim:** the text in the browser equals the `reply` from a direct `GET` of the same turn, byte for byte.
4. **Rule 2:** the upload's sha256 before and after is equal, read from the data root (the launcher exposes the path); the same check the live-run reports do.
5. **Reject:** reject the plan; no download area; the reply is shown as returned; no file in the delivered list; the original is unchanged.
6. **Approval timeout:** with a short confirmation timeout configured in the launcher, doing nothing leads to a finished turn with no downloads.
7. **Failure path:** a fake LLM that raises produces the pipeline's failure reply (backend R6), and the UI shows it verbatim with no download area.
8. **Clarification / no matching skill:** `not_delivered` renders as an ordinary reply.
9. **`.xls` upload**, once the backend's `.xls` test passes; until then skipped with a stated reason.
10. **Reload mid-turn** resumes polling; a **server restart** yields the `404` → new-session notice.
11. **Rate limit:** the launcher's low limit shows the message and the wait.
12. **Mobile:** scenarios 2 and 5 at 360×640 and 390×844, with no horizontal overflow.
13. **axe** on the gate, workspace, plan card and finished states.

The queued fake replies are deterministic, so these tests are stable; they check the plumbing and the rule-1 boundary, not model quality.

### 10.4 Live runs (required by `CLAUDE.md` before merging)

The frontend touches rule 1, so before merge: a small set of real-Groq runs through the browser against the built app (a plain turn, a two-step chain with approval, a rejection, a request the pipeline can't do), recorded in `docs/reviews/<branch>-live-runs.md` in the format `CLAUDE.md` requires (intent, `clarification_required`, steps, whether a file was delivered, independent check of the file, the reply, whether it makes a false claim, the original's hash before and after), plus DOM text or a screenshot showing the reply on screen equals the API's. Stop and report at the first Groq `429`.

### 10.5 Not tested, said plainly

Visual design quality, real assistive-technology behaviour beyond one manual pass, browsers other than Chromium in CI (Firefox and WebKit are cheap Playwright projects to add later), and performance under many concurrent users (the backend's one worker and two-turn cap are the bottleneck, not the UI).

---

## 11. Deployment with the backend for the public demo

### 11.1 One container, one process

Multi-stage `Dockerfile` at the repo root:

1. `node:20` stage: `npm ci` and `npm run build` in `frontend/`, producing `frontend/dist`.
2. `python:3.11-slim` stage (the floor version, per backend Decisions item 3): `pip install -r requirements.txt` (including `xlrd`), copy `backend/ buddy/ core/ skills/`, copy `dist/` to `/app/static`, run `uvicorn backend.main:app --host 0.0.0.0 --port $PORT --workers 1`.

FastAPI serves `/api/v1/*` and `/healthz` as designed and mounts the static directory at `/`, declared **after** the API routes so they win. The app has no client routes, so `index.html` at `/` is all it needs.

Configuration is by environment, all from the backend design: `WORKBUDDY_ENV=production`, `WORKBUDDY_DEMO_PASSWORD`, `GROQ_API_KEY`, `WORKBUDDY_SESSION_TTL_HOURS`, `WORKBUDDY_TRUSTED_PROXY_HOPS`, the limits, and `WORKBUDDY_STATIC_DIR`. Secrets are set in the host's secret store, never baked into the image or committed.

Same origin means **no CORS configuration at all** (`WORKBUDDY_CORS_ORIGINS` stays empty), a smaller attack surface than a split deployment, and the browser sends the password header only to the host that served the page.

### 11.2 Headers for a public page

Set by the backend on static responses (PR-4 scope, since PR-4 owns hosted-demo hardening): `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'` (Vite's production build needs no inline scripts), `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, HSTS behind TLS, immutable caching for hashed assets and `no-store` for `index.html`. The strict CSP is the main mitigation for the password sitting in `sessionStorage`.

### 11.3 Where to host it

What the app needs from a host: run a Docker container (or a Python process plus static files); one long-lived process with threads, not serverless (turns run for tens of seconds in worker threads, confirmation waits hold a thread for up to 120 s, and state is in memory); enough RAM for pandas plus `ydata-profiling` and `splink` (heavy imports; **[Likely] 512 MB is too small; I haven't measured the process**); outbound HTTPS to Groq; TLS; secrets.

**Recommendation: a Hugging Face Space using the Docker SDK, on the free CPU tier, for the first demo.**
- Why: it runs a container, gives HTTPS at a `*.hf.space` URL, has secrets, and (as I recall) offers roughly 16 GB RAM on the free CPU tier, which removes the memory unknown. It costs nothing.
- What it costs, **[Possible] (from memory; verify before relying on it):** the free tier sleeps after a period of inactivity, so the first visitor waits for a cold start; the disk is ephemeral, so a restart deletes uploads and sessions; the container must listen on the configured port (7860 by default). Ephemeral disk is compatible with the demo's retention promise, and the `404` → new-session path (1.3) already handles a wiped session. A restart mid-turn is handled by the backend's restart repair (its Section 7.5, run 8). Use the direct `*.hf.space` URL, not the embedded Space page: iframe storage restrictions can break `sessionStorage` and `localStorage`.
- Caveat: a public Space may expose the repository's contents (not secrets), depending on its visibility. Nothing in this repo is a secret under `CLAUDE.md`'s rules, but the owner should confirm that is acceptable for a project licensed `Proprietary` (`pyproject.toml`). A private Space may need a paid plan; I haven't checked the terms.

**Fallback: a small always-on container or VM with 1 to 2 GB RAM (for example Fly.io, Railway or a $5 to $10 VPS), with a persistent volume if the demo should survive restarts.** A few dollars a month, no cold start, predictable memory; it needs a card and a bit more ops.

**Not recommended for this app:** serverless and scale-to-zero platforms (Vercel, Netlify, Cloud Run scale-to-zero), because of in-memory state, one worker and threads that outlive a request; and Render's free web service, **[Likely]** too small in RAM and it spins down when idle. All pricing and limit statements here are from memory and unverified; check current terms before committing.

Evidence that would change this recommendation: a measured RSS for one turn on the fixture (if it fits in 512 MB, more free hosts qualify), and Groq's rate limit under the per-IP cap (if the free key is exhausted quickly, cost control, not hosting, is the constraint).

### 11.4 Local development

`npm run dev` runs Vite with a proxy for `/api` to `127.0.0.1:8000`, so developers use the real backend without CORS. `WORKBUDDY_ENV=dev` allows running without a demo password (backend Decisions item 1).

---

## 12. Asks of the backend design (to fold into its PR-2, PR-3 and PR-4)

What the frontend needs that the backend design doesn't currently say. None is assumed to exist.

1. **`pending_confirmation.steps`**: an ordered list of `{capability, prompt}` for the whole plan (already in the backend "Decisions", item 2; listed here because the plan card can't be built without it).
2. **Limits and retention in the session response** (`POST /sessions` and `GET /sessions/{id}`): `retention_hours`, `max_upload_bytes`, `accepted_extensions`, `max_message_chars`. The UI's retention text and input limits then can't drift from the server's settings.
3. **A committed `openapi.json`** kept in sync by a backend test, so the frontend's generated types are checked in CI.
4. **The exact auth header** for the demo password (`Authorization: Bearer …` recommended; the backend design left it open), and its `401` error code and body.
5. **Deferred, not needed for v1:** short-lived download tokens so downloads can use a plain link, and Server-Sent Events in place of polling.
6. **`Retry-After` on `429`** for both `rate_limited` and `server_busy`; the UI's countdown needs it.
7. **`reply_source` and `outcome` always present on finished turns**, as the backend design already states. The UI treats a finished turn missing either as an API error and shows a fixed "unexpected response from the server" message, never a guess.

---

## 13. PR split

Each on its own branch from `main`, merged only by the user. Each needs its backend counterpart to exist first, or runs against the fake client until then.

1. **FE-1 `feat/frontend-shell`**: tooling, the typed client, the gate, session resume, upload and the file list, the banned-strings check, component tests. No turns yet, so no live run; review focuses on the client and the gate.
2. **FE-2 `feat/frontend-chat`** (rule-touching): composer, transcript, polling, verbatim reply, outcome-as-layout, downloads, details panel, error states. Independent review; the Section 4.3 tests must exist before review.
3. **FE-3 `feat/frontend-plan-approval`** (rule-touching): the plan card and its tests. Needs backend PR-3.
4. **FE-4 `feat/frontend-e2e-and-deploy`**: the Playwright suite, axe checks, Dockerfile, static mount and headers (with backend PR-4), the deploy guide, and live runs per 10.4. Independent review; live-runs results file.

Deployment happens only after backend PR-1 through PR-4 and FE-1 through FE-4 have merged: the demo password, rate limit and retention are preconditions for a public link, not extras.

---

## 14. Risks, open questions, evidence that would change this

- **[Certain] The largest rule-1 risk is a future contributor adding a "helpful" banner.** The banned-strings check and the review checklist reduce this but can't stop a reworded claim. It is where human review has to keep working.
- **[Likely] A conflict between polling and the per-IP rate limit.** The backend Decisions suggest 60 requests/min/IP overall. One tab polling every second uses that whole budget by itself, so a single active user would be throttled once they do anything else. The backend limits need setting with polling in mind (for example, count mutating requests strictly and give polls of an existing turn a separate, higher allowance), or SSE moves from deferred to required. This is a real conflict between the two designs, not resolved here; raise it in the backend review.
- **[Possible] The shared password ends up public.** Then the rate limit and quotas are the only controls (backend Decisions, "honest limits").
- **[Possible] Blob downloads for large files** use browser memory. The upload cap bounds it (25 MB default), but it is unmeasured on low-end phones.
- **[Guessing] Line counts and bundle size** are from experience, not measurement.
- **Open:** whether the UI should show `reply_source`; whether the mode toggle is a session setting in the header (assumed) or a per-message control; whether the retention notice needs legal review for a public demo that accepts user files.
- **Evidence that would change this design:** if the independent review finds the banned-strings check gives false confidence, replace it with a snapshot of all UI-authored copy that a reviewer signs off change by change; if blob downloads fail on mobile browsers, switch to token-based links (ask 5); if the htmx alternative's missing pieces turn out not to matter in FE-1 and FE-2, the Node toolchain isn't worth its cost.
