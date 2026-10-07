# WorkBuddy demo server: the JSON API and the page, one process, one worker.
# Host-neutral: nothing here names a hosting platform. See docs/deploy.md for the environment
# variables it needs and the checklist for going public.
#
#   docker build -t workbuddy .
#   docker run --rm -p 8000:8000 --env-file prod.env -v workbuddy-data:/data workbuddy

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# A user with no login and no write access to the code. /data is the only place it may write
# (uploads, outputs, session records); mount a volume there to keep sessions across restarts.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app \
    && mkdir /data \
    && chown app:app /data

WORKDIR /app

# The runtime lock only (no test or lint tooling), in its own layer so a code change doesn't
# reinstall it. Every package is pinned with its hashes (requirements-runtime.lock; refresh it on
# purpose with scripts/refresh_locks.py), so pip resolves nothing: a build today and a build next
# year install the same files, or fail. --no-deps and --require-hashes make pip refuse anything the
# lock doesn't list. Behind a proxy that inspects TLS, build with
#   docker build --secret id=pip_ca,src=/path/to/ca-bundle.pem .
# and pip trusts that bundle for this one step. Without the secret nothing changes, and the
# bundle is never written into the image.
COPY requirements-runtime.lock .
RUN --mount=type=secret,id=pip_ca,required=false \
    if [ -f /run/secrets/pip_ca ]; then export PIP_CERT=/run/secrets/pip_ca; fi \
    && pip install --require-hashes --no-deps -r requirements-runtime.lock \
    && pip check

# The server and the pipeline. Templates and static files are part of backend/web.
COPY backend/ backend/
COPY buddy/ buddy/
COPY core/ core/
COPY skills/ skills/

USER app

# Production mode binds 0.0.0.0, and refuses to start without WORKBUDDY_DEMO_PASSWORD and an
# https WORKBUDDY_PUBLIC_ORIGIN, which the host supplies (docs/deploy.md). No key or password
# is ever baked into the image.
ENV WORKBUDDY_ENV=production \
    WORKBUDDY_DATA_DIR=/data \
    PORT=8000

EXPOSE 8000

# Liveness only: /healthz answers 200 whenever the process serves requests, and doesn't need a
# credential. (It also says whether an LLM key is configured; that isn't a reason to restart.)
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8000') + '/healthz', timeout=4).read()"]

# One worker, always: sessions, turns and access tokens live in this process's memory.
# ``python -m backend`` is the tested entry point (it picks the bind address from the
# environment and reports a bad setting by name, never by value).
CMD ["sh", "-c", "exec python -m backend --port \"${PORT}\""]
