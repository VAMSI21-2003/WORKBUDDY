"""Memory and wall time of the Docker image on the real pipeline, with a real LLM.

For each memory limit it starts the image the way a host would (``WORKBUDDY_ENV=production``, a demo
password, an https public origin, one worker, a fresh data folder), then drives the whole flow over
HTTP with ``scripts/live_api_runs.py``'s own driver: the password exchanged for the access cookie,
upload, a turn, the plan approved, the delivered file downloaded and checked against the original
with pandas, and the original's SHA-256 before and after. For each scenario it records the wall
time and the memory: the container's cgroup peak (page cache included, which is what a limit
counts, though the kernel gives cache back before it kills anything), the peak resident set of the
server process (``VmHWM``) and the peak of the cgroup's anonymous memory (sampled every 0.25 s).

Scenarios, in this order, in one container (so the server is warm after the first):
``small single`` (6 rows, "remove duplicate rows"), ``small chain`` (that, then "remove empty
rows"), ``2 MB single``, ``2 MB chain``, and ``2 MB chain x2``: two sessions at once, which is what
``WORKBUDDY_MAX_CONCURRENT_TURNS=2`` allows. If the kernel kills the container (OOM), that
scenario is recorded as killed, and a fresh container goes on with the next one.

It stops at the first sign of a rate limit from the LLM provider (any HTTP 429 in the container's
log) and reports how many scenarios were done.

Sandbox notes: the container runs with ``--network host`` and the egress proxy's settings,
because this sandbox reaches the LLM only through that proxy (``HTTPS_PROXY`` and its CA bundle
are passed in; the key itself is never read or printed, ``GROQ_API_KEY`` is only passed through).

Usage (``GROQ_API_KEY`` set; ``dockerd`` running)::

    python scripts/measure_container.py --image workbuddy:fe2 --limits none,2g,1g,512m \
        --json scratch/container/results.json [--pause 8] [--port 8780]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import live_api_runs as live  # noqa: E402

SCRATCH = REPO_ROOT / "scratch" / "container"
CGROUP = Path("/sys/fs/cgroup/memory/docker")
MIB = 1024 * 1024
APP_UID = 10001
CA_BUNDLE = Path(os.environ.get("SSL_CERT_FILE") or "/root/.ccr/ca-bundle.crt")
PROXY = os.environ.get("HTTPS_PROXY", "")
SMALL_CHAIN = "remove duplicate rows, then remove empty rows"
SMALL_SINGLE = "remove duplicate rows"


def docker(*args: str, check: bool = True, timeout: int = 120) -> str:
    done = subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout, check=False
    )
    if check and done.returncode != 0:
        raise SystemExit(f"docker {' '.join(args[:2])} failed: {done.stderr.strip()[:300]}")
    return done.stdout.strip()


def limit_bytes(text: str) -> int | None:
    if text == "none":
        return None
    match = re.fullmatch(r"(\d+)([mg])", text)
    if not match:
        raise SystemExit(f"bad limit {text!r}: use none, 512m, 1g, 2g")
    return int(match.group(1)) * MIB * (1024 if match.group(2) == "g" else 1)


class Container:
    """One run of the image, shaped like :class:`live_api_runs.Server` for the driver."""

    def __init__(self, image: str, limit: str, port: int, root: Path) -> None:
        self.image, self.limit, self.port = image, limit, port
        self.name = f"wb-measure-{limit}-{port}"
        self.data_dir = root / "data"
        self.base = f"http://127.0.0.1:{port}"
        self.cid = ""
        self.pid = 0
        self.log_offset = 0

    def start(self) -> None:
        docker("rm", "-f", self.name, check=False)
        shutil.rmtree(self.data_dir, ignore_errors=True)
        self.data_dir.mkdir(parents=True)
        os.chown(self.data_dir, APP_UID, APP_UID)
        args = ["run", "-d", "--name", self.name, "--network", "host"]
        cap = limit_bytes(self.limit)
        if cap is not None:
            args += ["--memory", str(cap), "--memory-swap", str(cap)]  # no swap: a limit is a limit
        env = {
            "PORT": str(self.port),
            "WORKBUDDY_DEMO_PASSWORD": live.LIVE_PASSWORD,
            "WORKBUDDY_PUBLIC_ORIGIN": live.PUBLIC_ORIGIN,
            **live.RELAXED_ENV,
            "HTTPS_PROXY": PROXY,
            "https_proxy": PROXY,
            "NO_PROXY": "127.0.0.1,localhost",
            "SSL_CERT_FILE": "/ca-bundle.pem",
            "REQUESTS_CA_BUNDLE": "/ca-bundle.pem",
        }
        for key, value in env.items():
            args += ["-e", f"{key}={value}"]
        args += ["-e", "GROQ_API_KEY"]  # passed through from this environment, never read here
        args += ["-v", f"{self.data_dir}:/data", "-v", f"{CA_BUNDLE}:/ca-bundle.pem:ro"]
        self.cid = docker(*args, self.image)
        self.pid = int(docker("inspect", "-f", "{{.State.Pid}}", self.name))
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if docker("inspect", "-f", "{{.State.Running}}", self.name) != "true":
                raise SystemExit("the container stopped at start:\n" + self.log_text()[-1500:])
            try:
                health = httpx.get(f"{self.base}/healthz", timeout=2, trust_env=False).json()
                if not health["llm_configured"]:
                    raise SystemExit("the server has no LLM configured")
                return
            except httpx.HTTPError:
                time.sleep(0.5)
        raise SystemExit("the server didn't start in 120 s")

    def state(self) -> dict[str, Any]:
        text = docker("inspect", "-f", "{{json .State}}", self.name, check=False)
        return dict(json.loads(text)) if text else {}

    def log_text(self) -> str:
        done = subprocess.run(
            ["docker", "logs", self.name], capture_output=True, text=True, check=False
        )
        return done.stdout + done.stderr

    def stop(self) -> None:
        docker("rm", "-f", self.name, check=False)

    # -- memory --------------------------------------------------------------------------------

    def cgroup(self, name: str) -> int | None:
        try:
            return int((CGROUP / self.cid / name).read_text().split()[0])
        except (OSError, ValueError, IndexError):
            return None

    def stat(self) -> dict[str, int]:
        try:
            lines = (CGROUP / self.cid / "memory.stat").read_text().splitlines()
        except OSError:
            return {}
        return {k: int(v) for k, v in (line.split() for line in lines)}

    def vm(self, field: str) -> int | None:
        try:
            text = Path(f"/proc/{self.pid}/status").read_text()
        except OSError:
            return None
        match = re.search(rf"{field}:\s+(\d+) kB", text)
        return int(match.group(1)) * 1024 if match else None

    def reset_peaks(self) -> None:
        try:
            (CGROUP / self.cid / "memory.max_usage_in_bytes").write_text("0")
            Path(f"/proc/{self.pid}/clear_refs").write_text("5")  # restart VmHWM from VmRSS
        except OSError:
            pass


class Sampler(threading.Thread):
    """The container's memory every 0.25 s, until stopped."""

    def __init__(self, box: Container) -> None:
        super().__init__(daemon=True)
        self.box = box
        self.stopping = threading.Event()
        self.max_anon = 0
        self.max_rss = 0
        self.samples = 0

    def run(self) -> None:
        while not self.stopping.wait(0.25):
            self.max_anon = max(self.max_anon, self.box.stat().get("rss", 0))
            self.max_rss = max(self.max_rss, self.box.vm("VmRSS") or 0)
            self.samples += 1

    def finish(self) -> None:
        self.stopping.set()
        self.join(2)


def mb(value: int | None) -> float | None:
    return None if value is None else round(value / MIB, 1)


def idle(box: Container, seconds: float = 6.0) -> dict[str, Any]:
    readings = []
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        stat = box.stat()
        readings.append(
            (box.cgroup("memory.usage_in_bytes") or 0, stat.get("rss", 0), box.vm("VmRSS") or 0)
        )
        time.sleep(0.5)
    return {
        "cgroup_usage_mb": mb(max(r[0] for r in readings)),
        "cgroup_anon_mb": mb(max(r[1] for r in readings)),
        "process_rss_mb": mb(max(r[2] for r in readings)),
    }


# -- Scenarios ---------------------------------------------------------------------------------


class Killed(Exception):
    pass


class PatientDriver(live.Driver):
    """The live driver, which waits out the server's own ``429`` on a poll.

    The driver polls twice a second, as a script would; two of them from one address (the
    two-at-once scenario) are more than the ``poll`` bucket's 3 a second per IP. A page polls
    every 2 to 3 seconds, so real users don't meet this; the measurement shouldn't either.
    """

    def get(self, url: str) -> httpx.Response:
        while True:
            response = super().get(url)
            if response.status_code != 429:
                return response
            time.sleep(float(response.headers.get("Retry-After", "1")))


def run_turns(
    box: Container, runs: list[tuple[str, bytes, str, str | None]]
) -> list[dict[str, Any]]:
    """Each run is (message, fixture bytes, name, expected); all start together, a session each."""
    results: list[dict[str, Any] | BaseException] = [RuntimeError("didn't run")] * len(runs)
    drivers = [PatientDriver(box) for _ in runs]  # type: ignore[arg-type]

    def one(index: int) -> None:
        message, data, name, expected = runs[index]
        driver = drivers[index]
        try:
            session = driver.session("friend")
            upload = driver.upload(session, data, name)
            turn = driver.turn(session, message, "approve", timeout_s=600)
            entry = live.report_turn(driver, "measure", session, upload, data, turn, expected)
            results[index] = entry
        except BaseException as exc:  # noqa: BLE001 - a kill shows up as a transport error
            results[index] = exc

    threads = [threading.Thread(target=one, args=(i,)) for i in range(len(runs))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    out = []
    for item in results:
        if isinstance(item, live.RateLimited):
            raise item
        if isinstance(item, BaseException):
            raise Killed(f"{type(item).__name__}: {str(item)[:200]}")
        out.append(item)
    return out


def scenario(
    box: Container, title: str, runs: list[tuple[str, bytes, str, str | None]]
) -> dict[str, Any]:
    box.reset_peaks()
    sampler = Sampler(box)
    sampler.start()
    started = time.monotonic()
    row: dict[str, Any] = {"scenario": title}
    try:
        entries = run_turns(box, runs)
        row["survived"] = True
        row["turns"] = [
            {
                "outcome": e["outcome"],
                "wall_s": e["wall_s"],
                "delivered": e["delivered"],
                "original_unchanged": e["original_unchanged"],
                "download_matches_record": e.get("download_sha256_matches_record"),
                "file_equals_expected": next(
                    (v for k, v in e.items() if k.startswith("equals_expected_")), None
                ),
                "stopped_at": e["stopped_at"],
                "reply": e["reply"],
            }
            for e in entries
        ]
        row["wall_s_max"] = max(e["wall_s"] for e in entries)
    except Killed as exc:
        row["survived"] = False
        row["error"] = str(exc)
    finally:
        sampler.finish()
    state = box.state()
    row["wall_total_s"] = round(time.monotonic() - started, 1)
    row["peak_cgroup_usage_mb"] = mb(box.cgroup("memory.max_usage_in_bytes"))
    row["peak_cgroup_anon_mb_sampled"] = mb(sampler.max_anon)
    row["peak_process_rss_mb"] = mb(box.vm("VmHWM") or sampler.max_rss)
    row["oom_killed"] = bool(state.get("OOMKilled"))
    row["container_running"] = bool(state.get("Running"))
    row["exit_code"] = state.get("ExitCode")
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="workbuddy:fe2")
    parser.add_argument("--limits", default="none,2g,1g,512m")
    parser.add_argument("--port", type=int, default=8780)
    parser.add_argument("--pause", type=float, default=8.0)
    parser.add_argument("--json", default=str(SCRATCH / "results.json"))
    parser.add_argument("--only", default="", help="comma-separated scenario titles to run")
    args = parser.parse_args()
    live.PAUSE_S = args.pause
    if not os.environ.get("GROQ_API_KEY"):
        raise SystemExit("GROQ_API_KEY isn't set")
    SCRATCH.mkdir(parents=True, exist_ok=True)

    small = live.default_fixture()
    big = live.sized_fixture(live.MAX_UPLOAD_2MB, 20_000)
    print(f"fixtures: small {len(small)} bytes, 2 MB {len(big)} bytes", flush=True)
    plan: list[tuple[str, list[tuple[str, bytes, str, str | None]]]] = [
        ("small single", [(SMALL_SINGLE, small, "small.xlsx", "dedupe")]),
        ("small chain", [(SMALL_CHAIN, small, "small.xlsx", "both")]),
        ("2 MB single", [(SMALL_SINGLE, big, "big.xlsx", "dedupe")]),
        ("2 MB chain", [(SMALL_CHAIN, big, "big.xlsx", "both")]),
        (
            "2 MB chain x2 at once",
            [(SMALL_CHAIN, big, "big_a.xlsx", "both"), (SMALL_CHAIN, big, "big_b.xlsx", "both")],
        ),
    ]
    only = {t.strip() for t in args.only.split(",") if t.strip()}
    results: list[dict[str, Any]] = []
    done = 0
    try:
        for limit in args.limits.split(","):
            root = SCRATCH / limit
            box = Container(args.image, limit, args.port, root)
            box.start()
            entry: dict[str, Any] = {"limit": limit, "scenarios": [], "restarts": 0}
            time.sleep(20)  # let the start-up settle
            entry["idle_at_start"] = idle(box)
            print(f"[{limit}] idle at start: {entry['idle_at_start']}", flush=True)
            for title, runs in plan:
                if only and title not in only:
                    continue
                row = scenario(box, title, runs)
                entry["scenarios"].append(row)
                done += 1
                summary = {k: v for k, v in row.items() if k != "turns"}
                print(f"[{limit}] {title}: {json.dumps(summary)}", flush=True)
                for turn in row.get("turns", []):
                    print(
                        f"      turn: {turn['outcome']} {turn['wall_s']} s "
                        f"delivered={turn['delivered']} "
                        f"equals_expected={turn['file_equals_expected']} "
                        f"original_unchanged={turn['original_unchanged']}",
                        flush=True,
                    )
                if not row["survived"] or not row["container_running"]:
                    entry["restarts"] += 1
                    box.stop()
                    box = Container(args.image, limit, args.port, root)
                    box.start()
                    time.sleep(10)
            time.sleep(30)
            entry["idle_after_runs"] = idle(box)
            print(f"[{limit}] idle after the runs: {entry['idle_after_runs']}", flush=True)
            logs = box.log_text()
            entry["log_mentions_provider_429"] = live.provider_429(logs)
            secrets = [("password", live.LIVE_PASSWORD)]
            secrets += [("access token", token) for token in live.ACCESS_TOKENS]
            entry["log_leaks"] = sorted({name for name, secret in secrets if secret in logs})
            box.stop()
            results.append(entry)
            Path(args.json).write_text(json.dumps(results, indent=2))
    except live.RateLimited:
        print(f"STOPPED: the LLM provider rate-limited the run after {done} scenarios", flush=True)
        results.append({"stopped_by_rate_limit_after_scenarios": done})
    finally:
        Path(args.json).write_text(json.dumps(results, indent=2))
        for limit in args.limits.split(","):
            docker("rm", "-f", f"wb-measure-{limit}-{args.port}", check=False)
    print(f"{done} scenarios done; results in {args.json}")


if __name__ == "__main__":
    main()
