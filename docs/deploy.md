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
- `WORKBUDDY_ENV=production` is set in the image. Production binds `0.0.0.0` and refuses to start without `WORKBUDDY_DEMO_PASSWORD` and an https `WORKBUDDY_PUBLIC_ORIGIN`; it prints the name of the setting that's wrong, never its value, and exits with status 2.
- `HEALTHCHECK` calls `GET /healthz` (liveness only; no credential needed). Hosts with their own health check can use the same path: `200` and `{"ok": true, "llm_configured": …}`.
- Behind a build proxy that inspects TLS, build with `docker build --secret id=pip_ca,src=/path/to/ca-bundle.pem .`. Without the secret nothing changes, and the bundle isn't written into the image.

TLS is the host's job: the container speaks plain HTTP on `PORT`, and the host's proxy terminates TLS in front of it. The cookies are `Secure`, and `WORKBUDDY_PUBLIC_ORIGIN` must be the `https://` address people open.

## 2. Environment variables

Every variable the server reads. "Set" means the host must set it; the rest have the default shown. A value that can't be used stops the server at start with the variable's name in the message.

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
- **With a volume on `/data`**, sessions, uploads and delivered files survive. A turn a restart interrupted is recorded as failed with the fallback reply, and a plan that was waiting counts as rejected (backend 7.5, run 8). A page open across the restart is reloaded by the server's `HX-Refresh` on its next poll (review M1 of the FE-1 page), and shows that account.
- **On an ephemeral disk** (no volume, or a host that wipes it) the sessions are gone too. The page shows the new-session notice, "That session no longer exists on the server, so this is a new one." That's correct, not a fault, and the route and browser tests cover it.
- Idle hosts that stop or sleep a container are the same case.

The `/data` folder must be writable by uid 10001 (`chown 10001:10001` on a bind mount; named volumes take the image's ownership).

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
- the three secrets are set in the host's secret store, and `GET /healthz` shows `llm_configured: true`;
- `WORKBUDDY_PUBLIC_ORIGIN` is the address in the browser's bar, character for character (the gate says `403 forbidden_origin` otherwise);
- a person completes the flow once on the host: gate, upload, a message, approve, download, and the original file is untouched (`docs/reviews/frontend-deploy-live-runs.md` has the four runs of design 10.4 against the image in this repository).

## 6. Measurements

SECTION 6 IS FILLED IN AFTER THE MEASUREMENT RUNS.
