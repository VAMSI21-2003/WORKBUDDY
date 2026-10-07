# Deploying the WorkBuddy demo

How to run the image, every environment variable the server reads, what to check before the link goes public, and what the image measured. **This document doesn't choose a host.** The owner chooses one from the measurements in Section 6 (`docs/design/frontend-design.md`, Section 11.3).

## 1. Build and run

```
docker build -t workbuddy .
docker run --rm -p 8000:8000 --env-file prod.env -v workbuddy-data:/data workbuddy
```

`prod.env` holds the variables of Section 2. The image never contains a password, a key or a user's file.

What the image is (`Dockerfile`, `.dockerignore`):
- `python:3.11-slim`, with the runtime packages of `requirements-runtime.txt` only (`requirements.txt` adds the test and lint tools and isn't used).
- Runs as user `app` (uid 10001), which can write only to `/data`. Mount a volume there; see Section 4 for what that changes.
- One process, one worker: `python -m backend --port "$PORT"` (`PORT` defaults to 8000). Sessions, turns, pending plans and access tokens live in that process's memory, so a second worker or a second instance behind one address would split them. Don't scale it out.
- `WORKBUDDY_ENV=production` is set in the image. Production binds `0.0.0.0` and refuses to start without `WORKBUDDY_DEMO_PASSWORD` and an https `WORKBUDDY_PUBLIC_ORIGIN`; it prints which setting is wrong, never its value, and exits with status 2. Some messages name the setting by its internal field (`max_upload_bytes` for `WORKBUDDY_MAX_UPLOAD_BYTES`, `public_origin` for `WORKBUDDY_PUBLIC_ORIGIN`), others by the variable.
- `HEALTHCHECK` calls `GET /healthz` (liveness only; no credential needed). Hosts with their own health check can use the same path: `200` and `{"ok": true, "llm_configured": …}`.
- Behind a build proxy that inspects TLS, build with `docker build --secret id=pip_ca,src=/path/to/ca-bundle.pem .`. Without the secret nothing changes, and the bundle isn't written into the image.

TLS is the host's job: the container speaks plain HTTP on `PORT`, and the host's proxy terminates TLS in front of it. The cookies are `Secure`, and `WORKBUDDY_PUBLIC_ORIGIN` must be the `https://` address people open.

## 2. Environment variables

Every variable the server reads. "Set" means the host must set it; the rest have the default shown. A value that can't be used stops the server at start with a message that names the setting (by the variable, or by its internal field name: `max_upload_bytes`, `public_origin`, `trusted_proxy_hops`, `turn_deadline_s` and so on, which are the variable's name in lower case without `WORKBUDDY_`) and never its value.

### The host must set

| Variable | What it is |
|---|---|
| `WORKBUDDY_DEMO_PASSWORD` | The shared demo password. Required in production. Keep it in the host's secret store. It's never logged, echoed or returned. |
| `WORKBUDDY_PUBLIC_ORIGIN` | The exact origin people open, `https://demo.example`: lower case, no path, no trailing slash. Required in production, and must be `https://`. The Origin check on every state-changing request compares against it exactly, so a mismatch makes the gate answer `403 forbidden_origin`. |
| `GROQ_API_KEY` | The LLM key (preferred). `ANTHROPIC_API_KEY` is used if there's no Groq key. With neither, the server starts, `/healthz` says `llm_configured: false` and every turn fails with the fallback reply. A secret: host secret store only. |

### Set by the image (change only for a reason)

| Variable | Image value | What it is |
|---|---|---|
| `WORKBUDDY_ENV` | `production` | `production`, `dev` (may run with no password; never public) or empty (binds `127.0.0.1`, needs a password). |
| `WORKBUDDY_DATA_DIR` | `/data` | Where uploads, delivered files and session records go. Default outside the image: `<repo>/workspace`. |
| `PORT` | `8000` | The port the server listens on. Many hosts set it themselves. |

### Proxy and access

| Variable | Default | What it is |
|---|---|---|
| `WORKBUDDY_TRUSTED_PROXY_HOPS` | `0` | How many reverse proxies stand in front of the app. `0`: every request's client is the socket's peer, and `X-Forwarded-For` is ignored. `N`: the client is the N-th entry from the right of `X-Forwarded-For`. **Wrong either way hurts:** too low, and every user shares one rate-limit bucket (the proxy's address); too high, and a caller can choose their own bucket. Checklist item 7. |
| `WORKBUDDY_ACCESS_TTL_HOURS` | `12` | How long an access cookie's token lasts, from issue (not renewed by use). |
| `WORKBUDDY_ACCESS_MAX_TOKENS` | `10000` | The most live access tokens held at once. |
| `WORKBUDDY_CORS_ORIGINS` | empty | Exact origins allowed to call the API from another origin. Leave empty: the page is served from the same origin as the API. |

### Sessions, storage and retention

| Variable | Default | What it is |
|---|---|---|
| `WORKBUDDY_SESSION_TTL_HOURS` | `24` | A session idle for longer is removed, with its files. Also the number the page's retention notice shows. |
| `WORKBUDDY_SWEEP_INTERVAL_S` | `300` | Seconds between retention sweeps. |
| `WORKBUDDY_MAX_UPLOAD_BYTES` | `2097152` (2 MiB) | The largest upload. Raise it only with a measurement: a 5 MB file didn't fit the turn deadline (backend Decisions 2026-10-04). |
| `WORKBUDDY_MAX_UPLOADS_PER_SESSION` | `20` | Uploads one session may hold. |
| `WORKBUDDY_MAX_SESSION_UPLOAD_BYTES` | `104857600` (100 MiB) | Bytes of uploads one session may hold. |
| `WORKBUDDY_MAX_TURNS_PER_SESSION` | `50` | Turns one session may run. |
| `WORKBUDDY_MAX_TOTAL_BYTES` | `2147483648` (2 GiB) | A cap on the bytes under the data root. At or over it a new session or upload gets `503 storage_full`. Keep it below the volume's size. |

### Turns

| Variable | Default | What it is |
|---|---|---|
| `WORKBUDDY_MAX_CONCURRENT_TURNS` | `2` | Turns running at once, across all sessions. A third gets `429 server_busy`. **The memory knob:** each running turn needs memory (Section 6), so a small host should set `1`. |
| `WORKBUDDY_TURN_DEADLINE_S` | `180` | Seconds a turn may work before it's finished as failed, not counting the wait for the plan's answer. At most 3600. |
| `WORKBUDDY_CONFIRMATION_TIMEOUT_S` | `120` | Seconds a plan waits for the user's answer; no answer is a rejection. At most 3600. |
| `WORKBUDDY_BUSY_RETRY_AFTER_S` | `10` | The `Retry-After` on `429 server_busy`. |

### Rate limits

One token bucket per client IP and bucket, in memory. Each bucket has two variables, `WORKBUDDY_RATE_<BUCKET>_BURST` (a whole number) and `WORKBUDDY_RATE_<BUCKET>_PER_HOUR` (the refill, tokens per hour). What each bucket is charged for is in `backend/ratelimit.py`.

| Bucket | `…_BURST` | `…_PER_HOUR` |
|---|---|---|
| `GENERAL` | `60` | `3600` |
| `POLL` | `30` | `10800` |
| `TURNS` | `10` | `10` |
| `UPLOADS` | `5` | `20` |
| `ACCESS` | `10` | `20` |
| `HEALTHZ` | `10` | `3600` |
| `PAGE` | `30` | `3600` |

For example `WORKBUDDY_RATE_TURNS_BURST=20`. Loosening `ACCESS` weakens the guard on the shared password.

### Retired

`WORKBUDDY_AUTO_APPROVE` is no longer read; setting it only logs a warning. Every file-changing step asks the user.

## 3. What the host has to provide

- Run the image as it is, with one instance, `PORT` reachable by the host's proxy, and outbound HTTPS to `api.groq.com`.
- TLS in front, at the address in `WORKBUDDY_PUBLIC_ORIGIN`, and HTTP/1.1 keep-alive and plain polling requests passed through (the page polls every 2 to 3 seconds; there are no websockets or streams).
- The three secrets of Section 2 in a secret store.
- Open the page at its own address. Inside another site's frame the browser blocks the access cookie, and the page forbids framing (`frame-ancestors 'none'`).
- Memory as measured in Section 6.

## 4. Restarts and disks

Everything the process holds in memory is lost when it restarts: turn versions, pending plans and access tokens.
- **Everyone sees the gate again** after a restart (the tokens are gone) and enters the password.
- **With a volume on `/data`**, sessions, uploads and delivered files survive. A turn a restart interrupted is recorded as failed with the fallback reply, and a plan that was waiting counts as rejected (backend 7.5, run 8). A page open across the restart shows that account only in a server that has no password (`WORKBUDDY_ENV=dev`), where the server's `HX-Refresh` reloads the page on its next request. **In production, a restart ends every access token, so a page that was open gets `401` on its next request, stops polling and shows the notice "The demo password is missing or wrong" with a link to enter the password again;** the person enters it and sees the turn as the server has it.
- **On an ephemeral disk** (no volume, or a host that wipes it) the sessions are gone too. The page shows the new-session notice, "That session no longer exists on the server, so this is a new one." That's correct, not a fault, and the route and browser tests cover it.
- Idle hosts that stop or sleep a container are the same case.

The `/data` folder must be writable by uid 10001 (`chown 10001:10001` on a bind mount; named volumes take the image's ownership). If `/data` itself refuses writes for that user, the server stops at start with "WorkBuddy can't start: this process can't write to its data folder /data (Permission denied)" and exit status 2. That's the only case with that message: a volume mounted read-only (`:ro`) ends with exit status 1 and a Python traceback (`OSError: Read-only file system`), and if `/data` is writable but its `uploads/`, `outputs/` or `sessions/` folder is owned by someone else (a volume left from a root-run container), the server starts and answers every new session with `500 internal_error`. So: `chown -R 10001:10001` the whole folder, never mount it read-only, and after setting it up open the page once and start a session.

## 5. The checklist for going public (design 13.2)

The link goes public only when **all** of these hold. Status as of this branch; the owner's decisions are the owner's to record.

| # | Item | Status |
|---|---|---|
| 1 | Backend PR-1 to PR-4, FE-1 and FE-2 are merged | PR-1 to PR-4 and FE-1 are merged. FE-2 is this branch, and merges when the owner decides |
| 2 | Multi-sheet `.xlsx` is refused at upload (backlog 1a, or the stopgap with its tests passing) | Met: the upload refuses a multi-sheet workbook with `422 multiple_sheets` (backend Decisions 2026-10-01) |
| 3 | `.xls`: out of `accepted_extensions` until it can be checked, or the owner accepts the risk in writing | Met, settled by the owner (recorded 2026-10-06): `.xlsx` only, an `.xls` upload gets `415` |
| 4 | PR-B (H3b), an explicit owner go or no-go | Met: PR-B is merged |
| 5 | **Code visibility (M7): the owner decides whether the code may be public** | **Open, the owner's decision.** It limits the choice of host (a host that builds from a public repository publishes the `Proprietary` code) |
| 6 | Measured on the host: backend part 2's live run 9 repeated on the chosen host; a two-step chain on a 2 MB file must finish inside `WORKBUDDY_TURN_DEADLINE_S` | **Open until a host is chosen.** Section 6 is the same measurement in this repository's image on a sandbox machine, not on the host |
| 7 | `WORKBUDDY_TRUSTED_PROXY_HOPS` is tested for the chosen host: with it set, two clients behind the proxy get separate buckets, and a spoofed `X-Forwarded-For` doesn't change anyone's | **Open until a host is chosen** |

Also before going public, not in the design's list but needed by this document:
- the three secrets are set in the host's secret store, and `GET /healthz` shows `llm_configured: true` (that only says a key is set, **not that it works**: an invalid key, or a host with no route to `api.groq.com`, also shows `true`, and a turn then ends with a reply that says no change was made and doesn't say the model failed. Send one real message after setting the key, and read its reply);
- `WORKBUDDY_PUBLIC_ORIGIN` is the address in the browser's bar, character for character (the gate says `403 forbidden_origin` otherwise);
- a person completes the flow once on the host: gate, upload, a message, approve, download, and the original file is untouched (`docs/reviews/frontend-deploy-live-runs.md` has the four runs of design 10.4 against the image in this repository).

## 6. Measurements

What the image used on the real pipeline with real Groq, so the owner can choose a host. **These are measurements on one sandbox machine, not on a host**, and checklist item 6 still has to be repeated on the host that's chosen.

**How.** `scripts/measure_container.py` starts the image as a host would (`WORKBUDDY_ENV=production`, a demo password, an https public origin, one worker, a fresh data folder, the design's real limits except that the `turns` and `uploads` buckets are raised, because the matrix makes more than 10 turns an hour), under a memory limit with no swap, and drives the whole flow over HTTP with `scripts/live_api_runs.py`'s own driver: the password exchanged for the access cookie, upload, a message, the plan approved, the delivered file downloaded. For every turn it checks the file against the original with pandas, the download's SHA-256 against the recorded one, and the original's SHA-256 before and after (all matched in every turn that delivered a file, in every run; the turns that failed at the deadline delivered nothing and left the original unchanged). Memory is read from the container's cgroup (`memory.max_usage_in_bytes`, reset before each scenario, which includes page cache; the sampled peak of its anonymous memory, every 0.25 s) and the server process's peak resident set (`VmHWM`, which also counts shared library pages mapped from files). Raw numbers: `docs/reviews/frontend-deploy-measurements.json`. The image's packages: `docs/reviews/frontend-deploy-image-freeze.txt`.

- **Machine:** 4 vCPUs (Intel Xeon @ 2.1 GHz), 16 GB, Docker 29.8.2 with cgroup v1. The container ran on the host's network, because this sandbox reaches the LLM only through an egress proxy; a real host uses ordinary networking.
- **Image:** 1.39 GB; `python:3.11.17`; pandas 2.3.3, ydata-profiling 4.18.4, splink 4.0.17 (resolved at build time; nothing is pinned or locked). Build time with a cold cache, 4 min 15 s.
- **Fixtures:** *small*, 6 rows (5,018 bytes: 2 exact duplicates, 1 empty row); *2 MB*, 24,497 data rows by 14 columns, 1,223 exact duplicate rows and 3 empty rows (2,055,232 bytes, just under the default 2 MiB cap).
- **Requests:** "remove duplicate rows" (*single*), and "remove duplicate rows, then remove empty rows" (*chain*, one plan for both steps). Each ran after the previous ones, in one container, so after the first the server is warm.
- **Wall time** is from sending the message to the finished turn, with the plan approved the moment it appears; it includes the LLM calls (a few seconds in all).
- Each cell is one run, not an average. The four runs below agree with each other to about 1 MB on memory and a few seconds on time, so I believe the differences between limits are noise.

### Memory and time (the run with no limit)

| Scenario | Wall time | Peak, container (cache included) | Peak, container anonymous | Peak, server process |
|---|---|---|---|---|
| Idle, 20 s after start | | 117 MB | 109 MB | 146 MB |
| Small, single | 5.7 s | 240 MB | 227 MB | 341 MB |
| Small, chain | 4.6 s | 240 MB | 228 MB | 342 MB |
| 2 MB, single | 120 s | 396 MB | 374 MB | 488 MB |
| 2 MB, chain | 150 s | 429 MB | 397 MB | 512 MB |
| 2 MB, two chains at once (two sessions) | both stopped at the 180 s deadline, no file | 591 MB | 531 MB | 640 MB |
| Idle again, after all of the above | | 456 MB | 424 MB | 530 MB |

Memory the server has taken for a large file isn't given back: it idled at about 420 to 435 MB (anonymous) after the 2 MB runs, against 109 MB at the start. The server process's number is higher than the container's anonymous number because it includes shared libraries mapped from files.

### Which limits it survives

| Limit | Idle, small, 2 MB single, 2 MB chain | Two 2 MB chains at once |
|---|---|---|
| none | survives | survives (memory peak 591 MB); both turns hit the 180 s deadline |
| 2 GB | survives | survives; both turns hit the deadline |
| 1 GB | survives | survives (573 MB); both turns hit the deadline |
| 512 MB | survives (the chain peaked at 427 MB in the container, 394 MB anonymous, about 85 MB under the limit) | **killed by the kernel (OOM, exit status 137)** at about 509 MB anonymous; the container stopped and the scenario's turns were lost |

- **One turn at a time fits in 512 MB**, with about 85 MB to spare, and in 1 GB with room. I didn't run 512 MB with `WORKBUDDY_MAX_CONCURRENT_TURNS=1`, which would turn the second simultaneous request into `429 server_busy`; it's the setting that should make 512 MB safe, and it is untested.
- **Two big turns at once need more than 512 MB** (they peaked at 518 to 534 MB anonymous, 573 to 591 MB in the container, in the runs that weren't killed). 1 GB held.
- A limit is the container's alone; a host also needs memory for its own processes. Counting those is the host's measurement (checklist item 6).

### Time is the constraint, not memory

- A **single** 2 MB chain took 150 s against the 180 s turn deadline (`WORKBUDDY_TURN_DEADLINE_S`): about 30 s, 17%, of margin on this machine. The check in design 13.2 item 6 (a two-step chain on a 2 MB file must finish inside the deadline) holds here with that margin. A host with a slower CPU, or one that shares its CPU, may not meet it.
- **Two 2 MB chains at once both missed the deadline** in every run where they weren't killed first (181 s). Each finished as `failed`, no file was handed back, the original was unchanged, and the reply said so: "Something went wrong while I was working on this, so I can't confirm that any change was made. I haven't handed back any file from this turn." [Likely, not profiled] The pipeline runs in Python threads in one process, so two turns share about one core's worth of compute. With the default `WORKBUDDY_MAX_CONCURRENT_TURNS=2`, two people sending 2 MB chains at the same moment would both fail.
- The options are the owner's: `WORKBUDDY_MAX_CONCURRENT_TURNS=1` (the second request gets `429 server_busy` and waits), a longer `WORKBUDDY_TURN_DEADLINE_S` (up to 3600; the user waits longer), or a lower `WORKBUDDY_MAX_UPLOAD_BYTES`. None of them was measured here.
- Small files take 5 to 6 s, and almost all of that is the LLM.

### What wasn't measured

Browsers and the page (the browser runs are in `docs/reviews/frontend-deploy-live-runs.md`); memory under many simultaneous users, or over hours; a second run of the same cell; a host's real CPU and network; swap; the `.xls` path (not accepted); inputs wider or longer than the 2 MB fixture; and any limit other than 512 MB, 1 GB, 2 GB and none.
