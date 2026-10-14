# Deploying WorkBuddy

How to put WorkBuddy on a server, release a new version safely, take it back, and restore a backup; every environment variable the server reads; what to check before the link goes public; and what the image measured.

**New to servers? Start at Section 7.** It goes from a fresh Ubuntu machine to the page loading over HTTPS, one command at a time. Sections 1 to 6 are the reference.

## Decisions recorded

Owner decisions, 2026-10-07:
- **The code stays private** (checklist item 5, Section 5, is settled). The server gets the code from the private repository with a read-only deploy key (Section 7, step 4); nothing builds from a public repository.
- **The host is a single Ubuntu VM, probably on Azure.** One machine, one app container, Caddy in front. Not scaled out: sessions live in the process's memory (Section 1). The Azure details are in Appendix A; nothing in the deployment depends on Azure.
- **Sign-in with Google replaces the demo password** (`docs/design/google-sign-in.md`; this change). Each person signs in with Google (OpenID Connect, authorization code with PKCE, done on the server) and reaches only their own sessions and files. Anyone with a verified Google email may sign in, unless `WORKBUDDY_ALLOWED_EMAILS` is set. In production the demo password is no longer a credential; scripts and `deploy/smoke.sh` use a separate operator token. How to create the Google client is Section 7, step 6; how to move a server that runs the demo-password version is Section 8, "Move from the demo password to Google sign-in".

## 1. Build and run

```
docker build -t workbuddy .
docker run --rm -p 8000:8000 --env-file prod.env -v workbuddy-data:/data workbuddy
```

`prod.env` holds the variables of Section 2. The image never contains a password, a key or a user's file. This is the bare image, for trying it or for a host that supplies its own proxy; for the Ubuntu VM use `deploy/` (Sections 7 to 9), which adds HTTPS, restarts, limits, backups and releases.

What the image is (`Dockerfile`, `.dockerignore`):
- `python:3.11-slim`, with the packages of `requirements-runtime.lock` only: every package pinned to one exact version with its hashes, installed with `pip install --require-hashes --no-deps`, so a build today and a build next year install the same files, or fail (Section 9, "Dependencies"). The test and lint tools aren't in the image.
- Runs as user `app` (uid 10001), which can write only to `/data`. Mount a volume there; see Section 4 for what that changes.
- One process, one worker: `python -m backend --port "$PORT"` (`PORT` defaults to 8000). Sessions, turns and pending plans live in that process's memory (people's sign-ins are also saved under `/data`, as a hash of the cookie's token), so a second worker or a second instance behind one address would split them. Don't scale it out.
- `WORKBUDDY_ENV=production` is set in the image. Production binds `0.0.0.0` and refuses to start without `WORKBUDDY_GOOGLE_CLIENT_ID`, `WORKBUDDY_GOOGLE_CLIENT_SECRET` and an https `WORKBUDDY_PUBLIC_ORIGIN`; it prints which setting is wrong, never its value, and exits with status 2. Some messages name the setting by its internal field (`max_upload_bytes` for `WORKBUDDY_MAX_UPLOAD_BYTES`, `public_origin` for `WORKBUDDY_PUBLIC_ORIGIN`), others by the variable.
- `HEALTHCHECK` calls `GET /healthz` (liveness only; no credential needed). Hosts with their own health check can use the same path: `200` and `{"ok": true, "llm_configured": …}`.
- Behind a build proxy that inspects TLS, build with `docker build --secret id=pip_ca,src=/path/to/ca-bundle.pem .`. Without the secret nothing changes, and the bundle isn't written into the image.

TLS is the host's job: the container speaks plain HTTP on `PORT`, and the host's proxy terminates TLS in front of it. The cookies are `Secure`, and `WORKBUDDY_PUBLIC_ORIGIN` must be the `https://` address people open.

## 2. Environment variables

Every variable the server reads. "Set" means the host must set it; the rest have the default shown. A value that can't be used stops the server at start with a message that names the setting (by the variable, or by its internal field name: `max_upload_bytes`, `public_origin`, `trusted_proxy_hops`, `turn_deadline_s` and so on, which are the variable's name in lower case without `WORKBUDDY_`) and never its value.

### The host must set

| Variable | What it is |
|---|---|
| `WORKBUDDY_GOOGLE_CLIENT_ID` | The Google sign-in client's id (Section 7, step 6). Required in production, with the secret. |
| `WORKBUDDY_GOOGLE_CLIENT_SECRET` | Its secret. Required in production. Keep it in the host's secret store. It's never logged, echoed or returned. |
| `WORKBUDDY_PUBLIC_ORIGIN` | The exact origin people open, `https://demo.example`: lower case, no path, no trailing slash. Required in production, and must be `https://`. The Origin check on every state-changing request compares against it exactly, so a mismatch makes the gate answer `403 forbidden_origin`. The address Google sends people back to is this plus `/auth/google/callback`, built from this setting alone (never from a request header), and that exact address is the one registered with Google. |
| `GROQ_API_KEY` | The LLM key (preferred). `ANTHROPIC_API_KEY` is used if there's no Groq key. With neither, the server starts, `/healthz` says `llm_configured: false` and every turn fails with the fallback reply. A secret: host secret store only. |

### Sign-in and scripts (optional)

| Variable | Default | What it is |
|---|---|---|
| `WORKBUDDY_ALLOWED_EMAILS` | empty | Comma-separated emails (case doesn't matter). When set, only those Google accounts may sign in; a person removed from the list is out at their next request, even if they were already signed in. Empty: any verified Google email. |
| `WORKBUDDY_OPERATOR_TOKEN` | none | At least 32 plain characters. Sent as `Authorization: Bearer <token>` by scripts and `deploy/smoke.sh`. It is a principal of its own and reaches only the sessions it created, never a person's. `deploy/` requires it (the smoke test needs it). A secret: never logged, echoed or returned. |
| `WORKBUDDY_DEMO_PASSWORD` | none | **Not a credential in production.** A value left from the demo-password version is ignored there (the server logs one warning at start). Outside production (`WORKBUDDY_ENV=dev` or empty) it still works, as the shared password for local trials and scripts. |
| `WORKBUDDY_OIDC_ISSUER` | `https://accounts.google.com` | The identity provider. **Only the tests and the live runs change it** (to a fake provider); never set it on a real server. An `https://` origin in production. |

### Set by the image (change only for a reason)

| Variable | Image value | What it is |
|---|---|---|
| `WORKBUDDY_ENV` | `production` | `production` (people sign in with Google), `dev` (may run with nothing configured, so open; never public; usable without Google) or empty (binds `127.0.0.1`, needs a credential: Google, a password or an operator token). |
| `WORKBUDDY_DATA_DIR` | `/data` | Where uploads, delivered files and session records go. Default outside the image: `<repo>/workspace`. |
| `PORT` | `8000` | The port the server listens on. Many hosts set it themselves. |

### Proxy and access

| Variable | Default | What it is |
|---|---|---|
| `WORKBUDDY_TRUSTED_PROXY_HOPS` | `0` | How many reverse proxies stand in front of the app. `0`: every request's client is the socket's peer, and `X-Forwarded-For` is ignored. `N`: the client is the N-th entry from the right of `X-Forwarded-For`. **Wrong either way hurts:** too low, and every user shares one rate-limit bucket (the proxy's address); too high, and a caller can choose their own bucket. Checklist item 7. |
| `WORKBUDDY_ACCESS_TTL_HOURS` | `12` | How long a sign-in lasts, from the moment Google vouched for the person (not renewed by use). The cookie is a session cookie, so closing the browser also ends it on that computer. |
| `WORKBUDDY_ACCESS_MAX_TOKENS` | `10000` | The most live sign-ins held at once; the one closest to expiring is dropped first. |
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

One token bucket per client IP and bucket, in memory (`USER_TURNS` and `USER_UPLOADS` are per *person*: they are keyed by the signed-in person, not the address). Each bucket has two variables, `WORKBUDDY_RATE_<BUCKET>_BURST` (a whole number) and `WORKBUDDY_RATE_<BUCKET>_PER_HOUR` (the refill, tokens per hour). What each bucket is charged for is in `backend/ratelimit.py`.

| Bucket | `…_BURST` | `…_PER_HOUR` |
|---|---|---|
| `GENERAL` | `60` | `3600` |
| `POLL` | `30` | `10800` |
| `TURNS` | `10` | `10` |
| `UPLOADS` | `5` | `20` |
| `ACCESS` | `10` | `20` |
| `HEALTHZ` | `10` | `3600` |
| `PAGE` | `30` | `3600` |
| `SIGNIN` | `10` | `60` |
| `USER_TURNS` | `10` | `10` |
| `USER_UPLOADS` | `5` | `20` |

For example `WORKBUDDY_RATE_TURNS_BURST=20`. `SIGNIN` limits starting and finishing a Google sign-in from one address (each calls Google); `ACCESS` limits the password route, which in production never signs anyone in. A request is charged to its address's bucket **and**, for turns and uploads, to its person's, in one step, so one account can't use the whole model quota by changing address.

### Retired

`WORKBUDDY_AUTO_APPROVE` is no longer read; setting it only logs a warning. Every file-changing step asks the user.

## 3. What the host has to provide

- Run the image as it is, with one instance, `PORT` reachable by the host's proxy, and outbound HTTPS to `api.groq.com`.
- TLS in front, at the address in `WORKBUDDY_PUBLIC_ORIGIN`, and HTTP/1.1 keep-alive and plain polling requests passed through (the page polls every 2 to 3 seconds; there are no websockets or streams).
- The secrets of Section 2 (the Google client secret, the operator token, the LLM key) in a secret store.
- Open the page at its own address. Inside another site's frame the browser blocks the access cookie, and the page forbids framing (`frame-ancestors 'none'`).
- Memory as measured in Section 6.

## 4. Restarts and disks

Everything the process holds in memory is lost when it restarts: turn versions, pending plans and logins that were started but not finished.
- **Nobody is signed out** by a restart or a release. A sign-in is saved under `/data` (`auth/signins/`) as a hash of the cookie's token, the person's Google id, their email and two times, and is read back at start. It ends at its expiry (`WORKBUDDY_ACCESS_TTL_HOURS`), when the person signs out, or when their email is taken off `WORKBUDDY_ALLOWED_EMAILS`. A person in the middle of choosing an account at Google when the server restarts sees "Sign-in didn't work. Try again."
- **With a volume on `/data`**, sessions, uploads and delivered files survive. A turn a restart interrupted is recorded as failed with the fallback reply, and a plan that was waiting counts as rejected (backend 7.5, run 8). A page open across the restart reloads itself on its next poll (the server's `HX-Refresh`) and shows the turn as the server has it.
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
| 5 | Code visibility (M7): the owner decides whether the code may be public | **Met, settled by the owner (recorded 2026-10-07): the code stays private.** The VM builds from a private checkout (Section 7, step 4) |
| 6 | Measured on the host: backend part 2's live run 9 repeated on the chosen host; a two-step chain on a 2 MB file must finish inside `WORKBUDDY_TURN_DEADLINE_S` | **Open until it's measured on the VM.** The host is chosen (a single Ubuntu VM) but not yet running. Section 6 is the same measurement in this repository's image on a sandbox machine, not on the VM. `deploy/smoke.sh` runs a small-file turn on the VM; the 2 MB two-step chain isn't automated |
| 7 | `WORKBUDDY_TRUSTED_PROXY_HOPS` is tested for the chosen host: with it set, two clients behind the proxy get separate buckets, and a spoofed `X-Forwarded-For` doesn't change anyone's | **Met for the compose layout** (`WORKBUDDY_TRUSTED_PROXY_HOPS=1`, Caddy in front, which replaces `X-Forwarded-For` with the address it saw): `deploy/check-proxy.sh` passes 6 of 6, and fails with the setting at `0` or `2` (`docs/reviews/feat-release-safety-live-runs.md`). It was run on one machine, not yet on the Azure VM: run `./deploy/check-proxy.sh` there once (Section 7, step 9) |

Also before going public, not in the design's list but needed by this document:
- the secrets are set in the host's secret store, and `GET /healthz` shows `llm_configured: true` (that only says a key is set, **not that it works**: an invalid key, or a host with no route to `api.groq.com`, also shows `true`, and a turn then ends with a reply that says no change was made and doesn't say the model failed. Send one real message after setting the key, and read its reply);
- `WORKBUDDY_PUBLIC_ORIGIN` is the address in the browser's bar, character for character (the gate says `403 forbidden_origin` otherwise);
- the Google client is created and published as in Section 7, step 6, and a person has signed in with a real Google account (the owner's checks in Section 7, step 9, which no test can do);
- a person completes the flow once on the host: sign in, upload, a message, approve, download, and the original file is untouched (`docs/reviews/frontend-deploy-live-runs.md` has the four runs of design 10.4 against the image in this repository).

## 6. Measurements

What the image used on the real pipeline with real Groq, so the owner can choose a host. **These are measurements on one sandbox machine, not on a host**, and checklist item 6 still has to be repeated on the host that's chosen.

**How.** `scripts/measure_container.py` starts the image as a host would (`WORKBUDDY_ENV=production`, a demo password, an https public origin, one worker, a fresh data folder, the design's real limits except that the `turns` and `uploads` buckets are raised, because the matrix makes more than 10 turns an hour), under a memory limit with no swap, and drives the whole flow over HTTP with `scripts/live_api_runs.py`'s own driver: the password exchanged for the access cookie, upload, a message, the plan approved, the delivered file downloaded. For every turn it checks the file against the original with pandas, the download's SHA-256 against the recorded one, and the original's SHA-256 before and after (all matched in every turn that delivered a file, in every run; the turns that failed at the deadline delivered nothing and left the original unchanged). Memory is read from the container's cgroup (`memory.max_usage_in_bytes`, reset before each scenario, which includes page cache; the sampled peak of its anonymous memory, every 0.25 s) and the server process's peak resident set (`VmHWM`, which also counts shared library pages mapped from files). Raw numbers: `docs/reviews/frontend-deploy-measurements.json`. The image's packages: `docs/reviews/frontend-deploy-image-freeze.txt`.

- **Machine:** 4 vCPUs (Intel Xeon @ 2.1 GHz), 16 GB, Docker 29.8.2 with cgroup v1. The container ran on the host's network, because this sandbox reaches the LLM only through an egress proxy; a real host uses ordinary networking.
- **Image:** 1.39 GB; `python:3.11.17`; pandas 2.3.3, ydata-profiling 4.18.4, splink 4.0.17 (resolved at build time when this was measured; since then every package is pinned in `requirements-runtime.lock` at exactly the versions of that freeze). Build time with a cold cache, 4 min 15 s.
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

## 7. Step by step: from a fresh Ubuntu VM to the page over HTTPS

For someone who has never used a server. Every line in a grey box is typed exactly as shown, then Enter. `<name>` means something you replace (without the `<` `>`). If a command prints an error, stop and read it; don't carry on to the next step.

**What you need before you start**
- An Ubuntu 24.04 virtual machine you can reach by SSH, with a public IP address. Appendix A creates one on Azure.
- A name for it that people can type, such as `workbuddy-demo.westeurope.cloudapp.azure.com` (Appendix A gives you one for free). Call it `<name>` below.
- **Ports 80 and 443 open to the internet** on the VM's cloud firewall (Appendix A, step 4). Without them HTTPS can't be set up.
- A Groq API key (the LLM key).
- A Google account that can create a Google Cloud project (any ordinary Google account can). It owns the sign-in client of step 6.
- A GitHub account that can read the private repository `VAMSI21-2003/WORKBUDDY` (to add a deploy key, step 4).

**1. Connect to the VM.** On your own computer (macOS and Linux: Terminal; Windows: PowerShell), using the SSH key you downloaded when you created the VM:

```
ssh -i ~/Downloads/<key-file>.pem azureuser@<name>
```

The first time it asks "Are you sure you want to continue connecting?": type `yes`. You're in when the prompt ends with `$` and shows the VM's name. (If it complains the key file is too open, run `chmod 600 ~/Downloads/<key-file>.pem` first.) Everything below is typed on the VM.

**2. Bring the machine up to date.**

```
sudo apt-get update && sudo apt-get upgrade -y
```

If it says "A reboot is required", run `sudo reboot`, wait a minute, and connect again as in step 1.

**3. Make a key that lets the VM read the private repository.**

```
ssh-keygen -t ed25519 -f ~/.ssh/workbuddy_deploy -N "" -C workbuddy-vm
cat ~/.ssh/workbuddy_deploy.pub
```

The second command prints one line starting with `ssh-ed25519`. Copy it. In a browser, open the repository on GitHub, then **Settings → Deploy keys → Add deploy key**; any title; paste the line; leave **Allow write access unchecked**; **Add key**. (A deploy key can only read this one repository, and only the VM holds its private half.)

**4. Download the code.**

```
printf 'Host github.com\n  IdentityFile ~/.ssh/workbuddy_deploy\n  IdentitiesOnly yes\n' >> ~/.ssh/config
chmod 600 ~/.ssh/config
git clone git@github.com:VAMSI21-2003/WORKBUDDY.git
```

The first time, git asks to trust github.com and shows a fingerprint. GitHub's current one for its ED25519 key is `SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU` (check it at <https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints>); if it matches, type `yes`. Then:

```
cd WORKBUDDY
git log --oneline -1
```

You should see one line: a commit id and a message.

**5. Install Docker, a swap file and the firewall.** This is the only step that changes the machine itself; it takes a few minutes.

```
sudo ./deploy/install.sh
```

It ends with "Done. Next:". It can be run again safely. Then **log out and back in** so Docker works without `sudo`:

```
exit
```

Connect again with the `ssh` command of step 1, then `cd WORKBUDDY`, and check:

```
docker ps
```

It should print a header line (`CONTAINER ID IMAGE ...`) and nothing else. "Permission denied" means you didn't log out and in.

**6. Create the Google sign-in client, and fill in the settings.** The settings file holds your keys and secrets; it stays on this VM, is never committed, and no script prints it.

*6a. Create the client in Google's console.* (Do this on your own computer, in a browser, signed in to the Google account that will own it. Google renames its screens now and then; this is the layout of the "Google Auth Platform" pages. If a screen differs, you need three things: a consent screen for an external app that asks for no more than `openid` and `email`, a **Web application** client, and the one redirect address below.)
1. Open `https://console.cloud.google.com/`. At the top, click the project picker → **New project**. Name: `WorkBuddy`. **Create**, then select it in the picker.
2. Open `https://console.cloud.google.com/auth/overview` (menu ☰ → **APIs & Services** → **OAuth consent screen**, or **Google Auth Platform**). If it says "Google Auth Platform not configured yet", click **Get started** and fill in:
   - **App name:** `WorkBuddy demo`. **User support email:** yours. **Next**.
   - **Audience: External.** **Next**.
   - **Contact information:** your email. **Next**. Tick the agreement, **Continue**, **Create**.
3. **Data Access** (or **Scopes**) → **Add or remove scopes**: tick `openid` and `.../auth/userinfo.email` only. WorkBuddy asks for nothing else, and these are not "sensitive" scopes, so Google does not need to review the app. **Update**, **Save**.
4. **Audience** → **Publish app** → **Confirm**, so that the status is **In production**. (In **Testing**, only the test users you list can sign in; everyone else gets an error from Google. With only these two scopes, publishing needs no review.)
5. **Clients** → **Create client**. **Application type: Web application.** **Name:** `WorkBuddy web`. Leave **Authorized JavaScript origins** empty (the sign-in happens on the server). Under **Authorized redirect URIs** click **Add URI** and type exactly:

   ```
   https://<name>/auth/google/callback
   ```

   where `<name>` is the same name as `WORKBUDDY_DOMAIN`: `https://`, the name in lower case, `/auth/google/callback`, **no trailing slash**, nothing else. It must match character for character what the server sends, which it builds from `WORKBUDDY_DOMAIN` alone; a difference makes Google answer "Error 400: redirect_uri_mismatch". Click **Create**.
6. A window shows the **Client ID** (it ends in `.apps.googleusercontent.com`) and the **Client secret** (it starts with `GOCSPX-`). Copy both now, or **Download JSON**: Google may not show the secret again. Keep them private; the secret is a password.

*6b. Fill in `deploy/.env`.*

```
nano deploy/.env
```

Type the values after the `=` signs, on the same line. **No spaces at either end, no quotes, no `$`, no backtick, and no ` #` in a value**: Docker reads the file with its own rules (it would drop the part of a secret after a `$`, for instance), and the scripts refuse such a value, by the setting's name, rather than run with a secret that doesn't match (Google's ids and secrets contain none of those characters):
- `WORKBUDDY_DOMAIN=<name>`: your name ("What you need", above), without `https://` and without a trailing slash.
- `WORKBUDDY_GOOGLE_CLIENT_ID=`: the Client ID from 6a.
- `WORKBUDDY_GOOGLE_CLIENT_SECRET=`: the Client secret from 6a.
- `WORKBUDDY_OPERATOR_TOKEN=`: a long random token for scripts and `smoke.sh`. `openssl rand -hex 24` (run it in a second window) makes a good one.
- `GROQ_API_KEY=`: your key.
- Optional: remove the `#` from `WORKBUDDY_ALLOWED_EMAILS=` and list the Google emails that may sign in, separated by commas. Leave it commented and any verified Google email may sign in.

Save with **Ctrl+O**, **Enter**, and leave with **Ctrl+X**. (Lines starting with `#` are optional settings; leave them.)

**7. Check the name points at this VM.**

```
getent hosts <name>
curl -s https://api.ipify.org; echo
```

The first prints an address; the second prints the VM's public address. **They must be the same.** If the first prints nothing or another address, the name isn't set up yet (Appendix A, step 5); wait a few minutes after changing it, and try again. HTTPS can't be set up until they match.

**8. Put WorkBuddy live.**

```
./deploy/release.sh HEAD
```

(`HEAD` is the commit you just downloaded.) The first time takes about 5 to 10 minutes: it downloads the base images, builds the image, starts it, waits for it to report healthy, and runs a real turn on a sample file through the real HTTPS address. It prints five numbered steps. It ends with one of:
- `RELEASED <commit>`: it works. Go on.
- `!! RELEASE <commit> FAILED at: <step>`: it did not work, and it says why in the lines above. The most common causes are in the table below. Fix the cause and run the same command again.

**9. Open it.** In a browser, open `https://<name>`. You should see the page with a **Continue with Google** button. Click it, choose your Google account, and you land in the workspace with "Signed in as <your email>" at the top. Upload a spreadsheet, and ask for something ("remove duplicate rows"). Approve the plan when it asks; download the result. Then, once:

```
./deploy/check-proxy.sh
```

It must end with `CHECK-PROXY OK`. That is checklist item 7 (Section 5): two visitors get separate rate limits and a faked `X-Forwarded-For` changes nobody's.

**10. Turn on a nightly backup.**

```
crontab -e
```

(If it asks which editor, choose `nano`.) Add this line at the end, replacing `azureuser` if your user has another name, then save and leave as in step 6:

```
17 3 * * * /home/azureuser/WORKBUDDY/deploy/backup.sh >> /home/azureuser/backup.log 2>&1
```

That makes a backup at 03:17 every night and keeps the last 7 in `deploy/backups/` (each release also takes one, so a week with many releases holds fewer nightly ones). **These backups are on the same disk as the data**, so they protect against a bad release or a mistake, not against losing the VM. Copy one to your own computer now and then, from your own computer:

```
scp -i ~/Downloads/<key-file>.pem azureuser@<name>:WORKBUDDY/deploy/backups/<file>.tar.gz .
```

**The checks only you can do, with real Google.** The tests sign people in against a fake Google that signs real tokens, so everything the server does is tested, but nothing here has ever talked to the real Google. Once, on the real server, in this order (a private window per person; a second Google account for "B"):
1. **Continue with Google** opens Google's account chooser. If it shows "Error 400: redirect_uri_mismatch", the address of step 6a.5 isn't the one in `WORKBUDDY_DOMAIN`. If it says the app isn't verified or access is blocked, the consent screen isn't **In production** (step 6a.4).
2. Choose account A: you land in the workspace, **already signed in** ("Signed in as A"), not on the gate again. This is the one thing that depends on the browser: the page moves on by itself, because a redirect from Google would stop the strict cookie being sent. Try Chrome, Firefox and Safari (and a phone).
3. Reload the page: still A, same session. **Sign out**: you are on the gate. Press the browser's Back button: no workspace appears. Close and reopen the browser, or restart the server (`./deploy/compose.sh restart app`) and reload while signed in: still signed in after the restart, signed out after closing the browser.
4. Upload a file as A and run "remove duplicate rows". In another private window sign in as B: B sees an empty workspace ("No files uploaded yet.", a different session) and no trace of A's file. **Sign out as A, in as B, in the same browser**: B doesn't get A's session.
5. With `WORKBUDDY_ALLOWED_EMAILS` set to A's email only (`nano deploy/.env`, then `./deploy/compose.sh up -d app`): B gets "That Google account isn't on the list of people who may use this demo." Unset it again.
6. `./deploy/smoke.sh` ends `SMOKE OK`, and `./deploy/compose.sh logs app | grep -i -E "id_token|GOCSPX|operator"` prints nothing.
7. Wait for the sign-in to expire (`WORKBUDDY_ACCESS_TTL_HOURS`, 12 hours) or shorten it: the next click shows "Sign in again" and the button works.

### When something goes wrong

| What you see | Likely cause | What to do |
|---|---|---|
| Step 7: the two addresses differ, or the first prints nothing | The name isn't set up, or is still spreading | Appendix A, step 5; wait 5 minutes |
| `release.sh`: `deploy/.env has no value for: ...` | A setting is blank | Step 6; the message names the setting, not its value. Nothing was changed: the live version is still running |
| Google says "Error 400: redirect_uri_mismatch" | The redirect address registered with Google isn't `https://<WORKBUDDY_DOMAIN>/auth/google/callback`, character for character | Step 6a.5 |
| Google says the app is blocked or only test users may sign in | The consent screen is in **Testing** | Step 6a.4: **Publish app** |
| Back from Google the page says "Sign-in didn't work. Try again." | The client secret or id in `.env` isn't the client's; the server can't reach Google; the sign-in took more than 10 minutes; or the email isn't verified | `./deploy/compose.sh logs --tail 50 app` has a line "Sign-in refused: <reason>" with a short code (`token_endpoint_status`, `token_audience`, `email_not_verified`, …) and nothing from the token |
| `release.sh` fails at "the smoke test", `tls` or no answer | Ports 80 and 443 aren't open to the internet, or the name points elsewhere, so Caddy can't get its certificate | Appendix A, step 4; step 7; then `./deploy/compose.sh logs caddy` shows what Let's Encrypt said |
| Browser: "can't be reached" or times out | The cloud firewall blocks 443 | Appendix A, step 4 |
| Browser: a certificate warning | Caddy hasn't got its certificate yet, or the name doesn't point at the VM | Wait a minute and reload; step 7 |
| The page says `403 forbidden_origin` | The address in the browser isn't `WORKBUDDY_DOMAIN`, character for character | Open `https://<name>` exactly; fix `WORKBUDDY_DOMAIN` and release again |
| The page says "You aren't signed in, or your sign-in has ended." | The sign-in expired (`WORKBUDDY_ACCESS_TTL_HOURS`), the person signed out in another tab, or their email was taken off the allowed list | The notice has a **Sign in again** link |
| A turn ends with "I can't confirm that any change was made" | The LLM key is missing, wrong, or out of quota | `./deploy/smoke.sh` shows it; check `GROQ_API_KEY`; `./deploy/compose.sh logs --tail 50 app` |
| `docker: permission denied` | You haven't logged out and in since step 5 | `exit`, connect again |

Useful commands, from `~/WORKBUDDY`: `./deploy/compose.sh ps` (what's running), `./deploy/compose.sh logs --tail 100 app` (the app's log; it never holds passwords or file contents), `./deploy/smoke.sh` (does a real turn work?), `./deploy/compose.sh restart app` (restart; nobody is signed out).

## 8. Releasing, rolling back and restoring

All from `~/WORKBUDDY` on the VM.

### Release a new version

```
git pull --ff-only
./deploy/release.sh HEAD
```

`git pull` brings this checkout to the version you want to release (the compose file, the Caddyfile and the scripts come from the checkout; the image comes from the commit), and `release.sh` refuses to run if the `deploy/` folder in the checkout differs from the one in the commit it's asked to release, telling you to check that commit out first. Any commit id you've fetched and checked out works in place of `HEAD`. It does five things in order, and stops at the first that fails:

1. **Builds the image** from exactly that commit, tagged with the commit id. If the build fails, nothing has changed: the live version keeps running, and no backup was taken.
2. **Backs up `/data`**, and **saves the running app's logs** to `deploy/logs/app-<commit>-<time>.log`. If the backup fails, nothing else happens. Docker deletes a container's logs when the container is replaced, so without this a problem seen just before a release could not be looked into afterwards. The logs are passed through the app's own logging filter (`backend/logging_filter.py`, run from the new image) before they are kept, so a key, a token or a session id is masked; if the filter can't run, nothing is kept. The folder is private to you (mode 700, files 600), is git-ignored, and holds the newest 20 files (`WORKBUDDY_LOG_KEEP`). A failure to save is a warning, never a reason to stop the release. If a release fails and rolls back, the failed release's own logs are saved the same way before they are replaced.
3. **Starts it** in place of the old one. The site is unavailable for a few seconds while the app container is replaced (one instance, so there's no overlap).
4. **Waits until it reports healthy.**
5. **Runs `smoke.sh`:** authenticate with the operator token, upload, a real message, approve, download, and a hash check, through the real HTTPS address. (A script can't sign in with Google: the operator token is its credential, and the operator reaches only the sessions it creates.)

If step 3, 4 or 5 fails, **it rolls back by itself**: it puts the previous image back (kept on the VM, so no build or download), waits for it to be healthy, runs `smoke.sh` again, and prints `!! ROLLED BACK to <previous>. Release <new> failed at: <step>. The site is serving the previous release.` The exit status is non-zero, so a script that calls it sees the failure. On the very first release there is no previous image: it stops the app and says so.

**After the first release that reads PDFs** (`docs/design/pdf-extract.md`; CI runs the same command inside the built image on every change, "docker build" job), check that the sandbox every PDF is read in can be built on the server: `./deploy/compose.sh exec app python -m skills.pdf` must print `The PDF sandbox holds.` (exit status 0). The check starts the real worker and tries, from inside it, to open a socket and to start a program; both must be refused. Where the container runtime does not let the worker install its seccomp filter, the server does not read PDFs (an upload of one is refused with "PDFs can't be read on this server right now") rather than read them unprotected. Then upload a PDF that holds one ruled table through the page, ask for the table, approve the plan, and download the spreadsheet. `smoke.sh` does not cover PDFs.

`deploy/releases.log` records every release, rollback and failure with the time. `deploy/logs/` holds the logs of the containers that were replaced (step 2).

**Rolling back doesn't restore data.** It changes the program, not `/data`. A new version that ran for a minute may have saved sessions in the new format. Versions are required to read everything older versions saved (`CLAUDE.md`, "Working method"; `tests/deploy/test_saved_data_survives.py`), but an older version isn't required to read what a newer one saved. If after a rollback sessions are missing or the page says a session no longer exists, restore the backup that the release took first (below).

### Move from the demo password to Google sign-in

For a server that runs the demo-password version and is to run this one. From `~/WORKBUDDY` on the VM. **Do the Google steps first:** `release.sh` refuses to build when a setting is missing, before it changes anything.

1. **Create the Google client** (Section 7, step 6a). The redirect address is `https://<WORKBUDDY_DOMAIN>/auth/google/callback`.
2. **Get the new code:** `git pull --ff-only`.
3. **Add the new settings** to `deploy/.env` (`nano deploy/.env`; step 6b): `WORKBUDDY_GOOGLE_CLIENT_ID`, `WORKBUDDY_GOOGLE_CLIENT_SECRET`, `WORKBUDDY_OPERATOR_TOKEN`, and optionally `WORKBUDDY_ALLOWED_EMAILS`. **Leave the `WORKBUDDY_DEMO_PASSWORD` line where it is for now**: the new server ignores it (one warning in the log), and it is what a rollback to the demo version needs.
4. **Release:** `./deploy/release.sh HEAD`. It backs up `/data`, starts the new version, and runs `smoke.sh` with the operator token. If anything fails it puts the demo version back by itself (and `smoke.sh` signs in to *that* one with the password, as it used to). Expect `RELEASED <commit>`.
5. **What people see.** The page now asks them to **Continue with Google**. They are not "signed in" from before. **Sessions started before the release can't be opened by anybody** (they belong to nobody; a demo-era session id is not accepted as a key), and the ordinary retention sweep deletes them once idle for `WORKBUDDY_SESSION_TTL_HOURS` (24), so at most 24 hours after the release. Nothing else in `/data` changes: their files are not touched. From then on every session belongs to the person who made it.
6. **Do the owner's checks** (Section 7, step 9: "The checks only you can do, with real Google").
7. **When you won't go back to the demo version**, delete the `WORKBUDDY_DEMO_PASSWORD=` line from `deploy/.env`, and the password is gone from the machine.

*Going back to the demo-password version* (needs the line from step 3): `git log --oneline` for the last demo-password commit, `git checkout <that commit>`, `./deploy/release.sh HEAD`, `git checkout main`. The older version opens every session on disk, including the ones made while this version ran (it ignores the owner files, so a session id is again the only key to a session). The people-signed-in files in `/data/auth/` are ignored by it and left alone.

### Roll back by hand

To go back to an older version later (the image of the one before the last is gone; it's rebuilt from the commit):

```
git log --oneline -10
git checkout <older commit id>
./deploy/release.sh HEAD
git checkout main      # afterwards, so the next git pull works
```

### Restore a backup

```
ls -lt deploy/backups
./deploy/restore.sh deploy/backups/<file>.tar.gz
```

It asks you to type `yes`; then it copies the archive aside, backs up what's there now (so a mistaken restore can itself be undone; no older backup is deleted by this), stops the app, unpacks the archive next to the data, and only when that has worked replaces `/data` with it, starts the app and waits for it to be healthy. If the unpacking fails (a damaged archive, a full disk) `/data` is untouched and the app is started again; if anything fails after `/data` was changed it prints the file holding the old data and the exact command that puts it back. Then `./deploy/smoke.sh`. Notes:
- Restoring replaces **everything** in `/data`, including sessions created since the backup. It needs room for a second copy of the archive's contents while it unpacks.
- Restore into the VM's own `/data` volume, which is what `restore.sh` does. The saved session records hold absolute paths under `/data`, so a data folder copied to a different path isn't restorable (found while writing the saved-data test; recorded in `docs/backlog.md`).
- Sessions are removed anyway after `WORKBUDDY_SESSION_TTL_HOURS` (24) without use. A backup older than that holds sessions that the server will sweep soon after a restore.

## 9. What the deployment files do

| File | What it is |
|---|---|
| `deploy/docker-compose.yml` | Two containers. `app`: the image, with no published port (only Caddy can reach it), `restart: unless-stopped`, a 2 GB memory limit (Section 6: one big turn at a time fits in 512 MB, two at once need more than that and fit in 1 GB; the limit was raised from 1 GB for PDFs, because one PDF worker may use up to 1 GiB of address space and a dense PDF measured 706 MiB resident, so the server and one worker must fit; the worker marks itself first for the kernel to stop, `oom_score_adj` 1000), rotated logs (5 files of 10 MB), `/data` on the named volume `workbuddy-data`, `WORKBUDDY_TRUSTED_PROXY_HOPS=1`, and the Google client, the operator token and the allowed emails from `.env`. `caddy`: ports 80 and 443, automatic HTTPS, starts after the app is healthy, its certificate kept in a volume |
| `deploy/Caddyfile` | Passes requests to the app, and sets `X-Forwarded-For` to the one address Caddy saw, discarding whatever the caller sent. That is what makes `WORKBUDDY_TRUSTED_PROXY_HOPS=1` correct: the app trusts the last entry, and Caddy wrote it |
| `deploy/.env.example` | The names of the settings, never values. `install.sh` copies it to `deploy/.env`, which is git-ignored |
| `deploy/install.sh` | First-time setup (Section 7, step 5): Docker from Docker's own repository, a 2 GB swap file, the firewall (22, 80, 443 in; everything out) |
| `deploy/release.sh <commit>` | Section 8 |
| `deploy/smoke.sh` | The health check, then a real turn through the API as the operator, with a hash check. Run it any time |
| `deploy/backup.sh`, `deploy/restore.sh` | Section 8 |
| `deploy/check-proxy.sh` | Checklist item 7: starts three throwaway clients with their own addresses, and checks buckets and spoofing through Caddy. Changes nothing |
| `deploy/compose.sh` | `docker compose` with the settings files added: `./deploy/compose.sh ps` |

Things worth knowing:
- **One instance only.** Sessions, turns and pending plans are in the app's memory (Section 1), and sign-ins are loaded into it at start. Don't run two `app` containers.
- **A restart signs nobody out** (Section 4). Sessions, files and sign-ins survive, because `/data` is a volume.
- **Concurrent big turns.** Section 6 measured that two 2 MB chains at once both miss the 180 s deadline. If that matters, set `WORKBUDDY_MAX_CONCURRENT_TURNS=1` in `deploy/.env` (remove the `#` on that line) and `./deploy/compose.sh up -d app`. It's the owner's call; the default is unchanged.
- **No access log from Caddy, on purpose.** Every request line holds a session's address (`/api/v1/sessions/s_...`), which the app treats as a secret and masks in its own logs. Don't add a `log` block to the Caddyfile without masking those.
- **Docker and the firewall.** Docker publishes ports around `ufw`. That's why the app has no published port at all: only Caddy's 80 and 443 are open, which is what `install.sh` allows anyway.
- **IPv6.** If you give the VM an IPv6 address, Docker may show every IPv6 visitor to Caddy as one address, so they'd share one rate-limit bucket. The deployment assumes IPv4 (Azure's default).

### Dependencies

`requirements-runtime.txt` and `requirements.txt` say what the project needs, in ranges. **Two lock files say exactly what is installed:**
- `requirements-runtime.lock`: what the image holds. Every package at one exact version, with hashes. The `Dockerfile` installs from it and nothing else (`pip install --require-hashes --no-deps`), so a build can't pick up a new version by itself. Its first version pinned exactly what `docs/reviews/frontend-deploy-image-freeze.txt` recorded, and an image built from it has an identical `pip freeze`.
- `requirements-dev.lock`: the same packages, plus the test and lint tools. GitHub's checks and development install from it.

**The one command that changes them, on purpose:**

```
pip install uv
python scripts/refresh_locks.py          # both locks, everything to the newest versions allowed
python scripts/refresh_locks.py dev      # only the dev lock; the image's packages stay as they are
```

Then read the diff, run the full suite, build the image, and commit the locks in a change of their own. `tests/deploy/test_dependency_locks.py` fails if the requirements files and the locks disagree.

### Tests on GitHub

`.github/workflows/tests.yml` runs on every pull request and on every push to `main`: the full suite on Python 3.11 installed from the dev lock (browser tests included), `ruff` against `ruff-baseline.txt` (the 22 known errors, counted per file and rule; a new error fails, a fixed one is noted, and `python scripts/check_ruff_baseline.py --update` records the improvement), and the Docker build, followed by starting the image and asking it `/healthz`.

## Appendix A. Azure

Menu names change from time to time; if one differs, look for the same words. You need an Azure account with a subscription.

**1. Create the VM.** Portal → **Create a resource → Virtual machine**.
- **Image:** Ubuntu Server 24.04 LTS (x64).
- **Size:** at least 2 GB of memory. `Standard_B2s` (2 vCPU, 4 GB) is a sensible start; with `Standard_B1ms` (2 GB) set `WORKBUDDY_MAX_CONCURRENT_TURNS=1` (Section 9). The app's limit is 2 GB (Section 6 measured the spreadsheet turns at under 1 GB; the PDF worker is the reason for 2 GB), and the machine needs memory of its own: **a 2 GB VM cannot give the app a 2 GB limit**, so on `Standard_B1ms` the limit is a ceiling the host runs out of first. A VM of 4 GB or more fits.
- **Authentication:** SSH public key; username `azureuser`; let Azure generate the key pair and **download the `.pem` file when asked** (it can't be downloaded again).
- **Disk:** 30 GB standard SSD is enough (the image is 1.4 GB; the build needs about 3 GB more while it runs).

**2. Ports.** On the **Networking** tab choose **Select inbound ports** and tick **SSH (22)**, **HTTP (80)** and **HTTPS (443)**. (Or do step 4 afterwards.) Then **Review + create → Create**. When it finishes, **Go to resource**.

**3. Keep the address.** On the VM's page click its **Public IP address**. Under **Configuration** set **Assignment** to **Static** (so a stop and start can't change it), **Save**.

**4. Open 80 and 443, if you didn't in step 2.** VM → **Networking → Network settings → Create port rule → Inbound port rule**: Destination port ranges `80`, Protocol **TCP**, Action **Allow**, a free Priority (such as 310), Name `allow-http`; **Add**. Again with `443` and Protocol **Any** (TCP and UDP, for HTTP/3), Priority 320, Name `allow-https`. Port 80 matters even though people use HTTPS: Let's Encrypt proves you own the name through it, and Caddy redirects visitors to HTTPS from it.

**5. A free name, no domain to buy.** On the **Public IP address** page, under **Configuration**, fill in **DNS name label**, such as `workbuddy-demo` (lower case letters, digits and hyphens; must be unique in the region), **Save**. The name is then `<label>.<region>.cloudapp.azure.com`, for example `workbuddy-demo.westeurope.cloudapp.azure.com`; the **Overview** page shows it as **DNS name**. That is the `<name>` of Section 7 and the value of `WORKBUDDY_DOMAIN`. Caddy gets a real Let's Encrypt certificate for it.

**6. Costs.** The VM and its disk cost money while they exist; **Stop (deallocate)** stops the compute charge but not the disk. With a static public IP, the address is kept. A backup that leaves the VM is a snapshot of the disk (VM → Disks → the disk → **Create snapshot**) or the `scp` of Section 7, step 10.

Go on with Section 7, step 1.
