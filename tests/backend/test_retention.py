"""Retention, quotas, the storage cap, binding and CORS, and what the server doesn't serve.

Design "Decisions (2026-09-30)", item 1 (the retention sweep, per-session quotas,
the global cap, binding and CORS, docs off) and "(2026-09-30, part 2)", ask 2
(``retention_hours`` is the sweep's own TTL). Time is injected: nothing sleeps
except the one test that runs the real background thread.
"""

from __future__ import annotations

import inspect
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.__main__ import choose_host, is_loopback
from backend.main import create_app
from backend.retention import (
    MaintenanceLoop,
    RetentionSweeper,
    StorageMeter,
    directory_bytes,
    remove_tree,
)
from backend.sessions import session_key
from backend.settings import Settings, SettingsError
from tests.backend.factories import BlockingFactory, PipelineFactory, QueuedProvider, single
from tests.backend.helpers import (
    RELAXED_LIMITS,
    FakeClock,
    locked_settings,
    new_session,
    start_turn,
    upload,
    wait_finished,
    xlsx_bytes,
)

HOUR = 3600.0


class Rig:
    """An app on a temporary data root, a monotonic clock and a wall clock the test advances."""

    def __init__(self, tmp_path: Path, factory: Any = None, **overrides: Any) -> None:
        self.mono = FakeClock(5_000.0)
        self.wall = FakeClock(1_800_000_000.0)
        values: dict[str, Any] = {
            "data_dir": tmp_path / "data",
            "rate_limits": RELAXED_LIMITS,
            "session_ttl_hours": 2,
        }
        values.update(overrides)
        self.settings = Settings(**values)
        self.factory = factory or (lambda provider: None)
        self.app = create_app(self.settings, self.factory, clock=self.mono, wall_clock=self.wall)
        self.client = TestClient(self.app)

    def sweep(self) -> Any:
        return self.app.state.maintenance._sweeper.sweep()

    def folders(self, session_id: str) -> dict[str, bool]:
        key = session_key(session_id)
        return {
            "uploads": (self.settings.uploads_dir / key).exists(),
            "outputs": (self.settings.outputs_dir / key).exists(),
            "sessions": (self.settings.sessions_dir / key).exists(),
        }

    def restart(self) -> Rig:
        other = Rig.__new__(Rig)
        other.mono, other.wall, other.settings, other.factory = (
            self.mono,
            self.wall,
            self.settings,
            self.factory,
        )
        other.app = create_app(
            other.settings, other.factory, clock=other.mono, wall_clock=other.wall
        )
        other.client = TestClient(other.app)
        return other

    def delivered_session(self) -> str:
        sid = new_session(self.client)
        assert upload(self.client, sid, xlsx_bytes()).status_code == 201
        tid = start_turn(self.client, sid, "remove duplicate rows").json()["turn_id"]
        assert wait_finished(self.client, sid, tid)["outcome"] == "delivered"
        return sid

    def exists(self, session_id: str) -> bool:
        return self.client.get(f"/api/v1/sessions/{session_id}").status_code == 200


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path, PipelineFactory(QueuedProvider(single("remove_duplicate_rows"))))


# -- The sweep -------------------------------------------------------------------------------------


def test_a_session_idle_past_the_ttl_is_gone_with_every_file_and_a_fresh_one_stays(
    rig: Rig,
) -> None:
    old = rig.delivered_session()
    rig.wall.advance(2 * HOUR + 1)
    fresh = new_session(rig.client)
    assert all(rig.folders(old).values())

    result = rig.sweep()

    assert result.removed == (session_key(old),) and result.failed == ()
    assert not any(rig.folders(old).values())  # uploads, outputs and records
    assert not rig.exists(old)
    assert rig.client.get(f"/api/v1/sessions/{old}/files").status_code == 404
    assert rig.exists(fresh)
    assert rig.folders(fresh)["sessions"]


def test_a_session_idle_for_less_than_the_ttl_stays(rig: Rig) -> None:
    sid = rig.delivered_session()
    rig.wall.advance(2 * HOUR - 1)

    assert rig.sweep().removed == ()
    assert rig.exists(sid)


def test_the_advertised_retention_is_the_period_the_sweep_enforces(tmp_path: Path) -> None:
    rig = Rig(tmp_path, session_ttl_hours=3)
    created = rig.client.post("/api/v1/sessions").json()
    sid = created["session_id"]
    state = rig.client.get(f"/api/v1/sessions/{sid}").json()
    assert created["retention_hours"] == state["retention_hours"] == 3

    rig.wall.advance(3 * HOUR - 60)
    assert rig.sweep().removed == ()
    rig.wall.advance(120)
    assert rig.sweep().removed == (session_key(sid),)  # idle longer than retention_hours: gone


def test_a_fractional_ttl_is_advertised_as_it_is_and_a_whole_one_as_an_integer(
    tmp_path: Path,
) -> None:
    assert Settings(data_dir=tmp_path, session_ttl_hours=0.5).retention_hours == 0.5
    whole = Settings(data_dir=tmp_path, session_ttl_hours=24.0).retention_hours
    assert whole == 24 and isinstance(whole, int)


def test_using_a_session_counts_as_activity_even_by_a_poll(rig: Rig) -> None:
    sid = new_session(rig.client)
    for _ in range(3):
        rig.wall.advance(1.5 * HOUR)
        assert rig.exists(sid)  # each read is use
        assert rig.sweep().removed == ()

    rig.wall.advance(2 * HOUR + 1)
    assert rig.sweep().removed == (session_key(sid),)


def test_a_session_with_a_running_turn_is_never_touched(tmp_path: Path) -> None:
    factory = BlockingFactory()
    rig = Rig(tmp_path, factory)
    sid = new_session(rig.client)
    tid = start_turn(rig.client, sid, "hello").json()["turn_id"]
    try:
        assert factory.started.wait(5)
        rig.wall.advance(10 * HOUR)

        assert rig.sweep().removed == ()
        folders = rig.folders(sid)
        assert folders["outputs"] and folders["sessions"]  # (no upload in this session)
    finally:
        factory.release.set()
    wait_finished(rig.client, sid, tid)
    rig.wall.advance(3 * HOUR)
    assert rig.sweep().removed == (session_key(sid),)  # once it's finished and idle, it goes


def test_a_session_whose_plan_waits_for_an_answer_is_never_touched(tmp_path: Path) -> None:
    rig = Rig(tmp_path, PipelineFactory(QueuedProvider(single("remove_duplicate_rows"))))
    sid = new_session(rig.client)
    upload(rig.client, sid, xlsx_bytes())
    tid = start_turn(rig.client, sid, "remove duplicate rows").json()["turn_id"]
    deadline = time.monotonic() + 20
    while (
        rig.client.get(f"/api/v1/sessions/{sid}/turns/{tid}").json()["status"]
        != "awaiting_confirmation"
    ):
        assert time.monotonic() < deadline
        time.sleep(0.02)
    rig.wall.advance(10 * HOUR)

    assert rig.sweep().removed == ()

    wait_finished(rig.client, sid, tid, decision="reject")


def test_a_session_on_disk_only_is_swept_by_the_age_of_its_record(rig: Rig) -> None:
    stale, recent = rig.delivered_session(), new_session(rig.client)
    root = rig.settings.sessions_dir
    now = rig.wall.now
    os.utime(root / session_key(stale) / "session.json", (now - 3 * HOUR, now - 3 * HOUR))
    os.utime(root / session_key(recent) / "session.json", (now - 1 * HOUR, now - 1 * HOUR))
    restarted = rig.restart()  # nothing is in memory now

    result = restarted.sweep()

    assert result.removed == (session_key(stale),)
    assert not any(restarted.folders(stale).values())
    assert restarted.exists(recent)


def test_a_use_is_written_to_disk_so_a_restart_does_not_sweep_a_session_that_was_just_read(
    rig: Rig,
) -> None:
    sid = new_session(rig.client)
    record = root = rig.settings.sessions_dir / session_key(sid) / "session.json"
    os.utime(record, (rig.wall.now - 5 * HOUR, rig.wall.now - 5 * HOUR))  # an old write
    rig.wall.advance(100)
    rig.app.state.registry._sessions.clear()  # forget it, as a restart does
    assert rig.exists(sid)  # read again: restored, and the use is marked

    restarted = rig.restart()

    assert restarted.sweep().removed == ()
    assert root.stat().st_mtime >= rig.wall.now - 1


def test_the_disk_is_touched_at_most_once_a_minute(rig: Rig) -> None:
    sid = new_session(rig.client)
    record = rig.settings.sessions_dir / session_key(sid) / "session.json"
    rig.wall.advance(10)
    rig.client.get(f"/api/v1/sessions/{sid}")
    first = record.stat().st_mtime
    rig.wall.advance(10)
    os.utime(record, (first - 500, first - 500))
    rig.client.get(f"/api/v1/sessions/{sid}")

    assert record.stat().st_mtime == first - 500  # not touched again within the minute


def test_folders_that_are_not_session_keys_are_never_deleted(rig: Rig) -> None:
    sid = new_session(rig.client)
    odd = [
        rig.settings.uploads_dir / "not-a-key",
        rig.settings.outputs_dir / ("0" * 31),  # one digit short
        rig.settings.sessions_dir / ("G" * 32),
        rig.settings.uploads_dir / ("A" * 32),  # upper case isn't a key
    ]
    for folder in odd:
        folder.mkdir()
        (folder / "x.txt").write_text("keep")
        os.utime(folder, (1, 1))
    rig.wall.advance(100 * HOUR)

    assert rig.sweep().removed == (session_key(sid),)
    assert all((folder / "x.txt").exists() for folder in odd)


def test_orphan_folders_of_a_session_with_no_record_go_when_old(rig: Rig) -> None:
    key = "ab" * 16
    (rig.settings.uploads_dir / key / "f_1").mkdir(parents=True)
    (rig.settings.outputs_dir / key / "t0001").mkdir(parents=True)
    for folder in (rig.settings.uploads_dir / key, rig.settings.outputs_dir / key):
        os.utime(folder, (rig.wall.now - 3 * HOUR,) * 2)

    result = rig.sweep()

    assert result.removed == (key,)
    assert not (rig.settings.uploads_dir / key).exists()
    assert not (rig.settings.outputs_dir / key).exists()


def test_a_symlink_is_removed_as_a_link_and_never_followed(rig: Rig, tmp_path: Path) -> None:
    sid = rig.delivered_session()
    key = session_key(sid)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious.txt").write_text("keep me")
    (rig.settings.outputs_dir / key / "t0001" / "link").symlink_to(outside)
    rig.wall.advance(3 * HOUR)

    assert rig.sweep().removed == (key,)

    assert (outside / "precious.txt").read_text() == "keep me"
    assert not (rig.settings.outputs_dir / key).exists()


def test_a_symlinked_session_folder_is_unlinked_not_followed(rig: Rig, tmp_path: Path) -> None:
    key = "cd" * 16
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "precious.txt").write_text("keep me")
    (rig.settings.uploads_dir / key).symlink_to(outside)
    os.utime(rig.settings.uploads_dir / key, (1, 1), follow_symlinks=False)

    result = rig.sweep()

    assert result.removed == (key,)
    assert not (rig.settings.uploads_dir / key).exists()
    assert (outside / "precious.txt").read_text() == "keep me"


def test_read_only_uploads_are_deleted(rig: Rig) -> None:
    sid = rig.delivered_session()
    (upload_dir,) = (rig.settings.uploads_dir / session_key(sid)).iterdir()
    assert upload_dir.stat().st_mode & 0o222 == 0  # stored read-only
    rig.wall.advance(3 * HOUR)

    assert rig.sweep().removed == (session_key(sid),)


@pytest.mark.skipif(os.geteuid() != 0, reason="needs root to drop to another user")
def test_a_process_that_is_not_root_can_delete_read_only_uploads() -> None:
    """Root ignores permissions, so this is the real check: delete as an ordinary user."""
    import shutil
    import tempfile

    base = Path(tempfile.mkdtemp(prefix="wb-retention-"))  # (pytest's own folders are root-only)
    try:
        tree = base / "tree"
        (tree / "f_1").mkdir(parents=True)
        (tree / "f_1" / "report.xlsx").write_bytes(b"data")
        os.chmod(tree / "f_1" / "report.xlsx", 0o444)
        os.chmod(tree / "f_1", 0o555)
        os.chmod(tree, 0o555)
        for path in (base, tree, tree / "f_1", tree / "f_1" / "report.xlsx"):
            os.chown(path, 65534, 65534)
        os.chmod(base, 0o755)
        # (the function's own source: this user can't read the packages the app imports)
        code = (
            "import os, stat, sys\nfrom pathlib import Path\n"
            + inspect.getsource(remove_tree)
            + f"\nremove_tree(Path({str(tree)!r}))"
            + f"\nsys.exit(1 if Path({str(tree)!r}).exists() else 0)"
        )

        result = subprocess.run(
            [sys.executable, "-c", code],
            preexec_fn=lambda: (os.setgid(65534), os.setuid(65534)),  # noqa: PLW1509
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stderr
        assert not tree.exists()
    finally:
        shutil.rmtree(base, ignore_errors=True)


def _windows_rule(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Windows' rule for ``os.unlink``: a file with the read-only attribute (``chmod`` 0o444
    sets it) can't be deleted. Linux enforces nothing like it for root, so it is applied here."""
    refused: list[str] = []
    real_unlink = os.unlink

    def unlink(path: Any, *args: Any, **kwargs: Any) -> None:
        if os.path.isfile(path) and not os.path.islink(path) and not os.stat(path).st_mode & 0o200:
            refused.append(str(path))
            raise PermissionError(13, "Access is denied (WinError 5)", str(path))
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", unlink)
    return refused


def test_read_only_files_are_deleted_where_a_read_only_file_cannot_be_unlinked(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review round 1, M1: on Windows every upload and delivered file (``0o444``) is read-only,
    and the sweep must still remove the session it advertises it removes."""
    sid = rig.delivered_session()
    key = session_key(sid)
    refused = _windows_rule(monkeypatch)
    rig.wall.advance(3 * HOUR)

    result = rig.sweep()

    assert result.removed == (key,) and result.failed == (), refused
    assert not any(rig.folders(sid).values())
    assert not rig.exists(sid)


def test_a_symlink_is_not_chmodded_when_its_target_is_read_only(
    rig: Rig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("keep")
    outside.chmod(0o444)
    sid = rig.delivered_session()
    key = session_key(sid)
    (rig.settings.outputs_dir / key / "t0001" / "link").symlink_to(outside)
    _windows_rule(monkeypatch)
    rig.wall.advance(3 * HOUR)

    assert rig.sweep().removed == (key,)

    assert outside.read_text() == "keep"
    assert outside.stat().st_mode & 0o777 == 0o444  # the target's mode is untouched


def test_the_sweep_takes_the_session_out_of_service_before_deleting(rig: Rig) -> None:
    """While its files are being deleted, no request can find the session or restore it."""
    sid = new_session(rig.client)
    key = session_key(sid)
    registry = rig.app.state.registry
    rig.wall.advance(3 * HOUR)
    assert registry.claim_idle(key, rig.wall.now - 2 * HOUR)

    assert registry.get(sid) is None  # not in memory, and not restored from the disk copy
    assert rig.client.get(f"/api/v1/sessions/{sid}").status_code == 404
    assert not registry.claim_unloaded(key)  # nobody else claims it either
    registry.release(key)
    assert registry.get(sid) is not None  # (it was never deleted here)


def test_a_failed_removal_is_reported_leaves_the_record_and_is_retried(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = rig.delivered_session()
    key = session_key(sid)
    rig.wall.advance(3 * HOUR)
    real = remove_tree

    def broken(path: Path) -> None:
        if path.parent == rig.settings.outputs_dir:
            raise PermissionError("nope")
        real(path)

    monkeypatch.setattr("backend.retention.remove_tree", broken)

    result = rig.sweep()

    assert result.failed == (key,) and result.removed == ()
    assert (rig.settings.sessions_dir / key / "session.json").exists()  # the record goes last
    assert not rig.app.state.registry._deleting  # not stuck out of service
    monkeypatch.undo()
    assert rig.sweep().removed == (key,)
    assert not any(rig.folders(sid).values())


def test_every_session_is_swept_in_one_pass_and_the_result_names_them(rig: Rig) -> None:
    ids = [new_session(rig.client) for _ in range(4)]
    rig.wall.advance(3 * HOUR)

    result = rig.sweep()

    assert sorted(result.removed) == sorted(session_key(i) for i in ids)


def test_a_session_deleted_by_the_sweep_does_not_come_back_after_a_restart(rig: Rig) -> None:
    sid = rig.delivered_session()
    rig.wall.advance(3 * HOUR)
    rig.sweep()

    assert not rig.restart().exists(sid)


def test_remove_tree_deletes_a_whole_tree_and_refuses_nothing_it_is_given(tmp_path: Path) -> None:
    tree = tmp_path / "t"
    (tree / "a" / "b").mkdir(parents=True)
    (tree / "a" / "b" / "f").write_text("x")
    (tree / "a" / "link").symlink_to(tmp_path)

    remove_tree(tree)

    assert not tree.exists() and tmp_path.exists()


def test_the_sweep_also_clears_expired_tokens_and_idle_buckets(rig: Rig) -> None:
    access, limiter = rig.app.state.access, rig.app.state.limiter
    access.tokens.issue()
    limiter.charge("1.2.3.4", [("general", 1)])
    rig.mono.advance(13 * HOUR)
    loop: MaintenanceLoop = rig.app.state.maintenance

    loop.run_once()

    assert len(access.tokens) == 0
    assert limiter._buckets == {}


def test_the_background_loop_sweeps_on_its_own_and_survives_a_failed_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path, session_ttl_hours=0.00001, sweep_interval_s=0.05)  # about 36 ms
    rig.wall.advance(0)
    sweeper: RetentionSweeper = rig.app.state.maintenance._sweeper
    real = sweeper.sweep
    calls = {"n": 0}

    def flaky() -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("one bad pass")
        return real()

    monkeypatch.setattr(sweeper, "sweep", flaky)
    with TestClient(rig.app) as client:  # the lifespan starts the loop
        sid = client.post("/api/v1/sessions").json()["session_id"]
        deadline = time.monotonic() + 10
        while rig.folders(sid)["sessions"]:
            rig.wall.advance(1)
            assert time.monotonic() < deadline, "the loop never removed the idle session"
            time.sleep(0.05)

    assert calls["n"] >= 2  # it went on after the pass that raised
    assert rig.app.state.maintenance._thread is None  # stopped at shutdown


# -- Quotas ---------------------------------------------------------------------------------------


def _quota(response: Any, message_part: str) -> None:
    assert response.status_code == 403, response.text
    error = response.json()["error"]
    assert error["code"] == "session_quota_exceeded"
    assert message_part in error["message"]


def test_a_session_may_hold_only_so_many_uploads_and_a_refused_one_stores_nothing(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path, max_uploads_per_session=2)
    sid = new_session(rig.client)
    for _ in range(2):
        assert upload(rig.client, sid, xlsx_bytes()).status_code == 201
    before = directory_bytes(rig.settings.data_dir)

    third = upload(rig.client, sid, xlsx_bytes())

    _quota(third, "2 uploads")
    assert directory_bytes(rig.settings.data_dir) == before
    assert len(list((rig.settings.uploads_dir / session_key(sid)).iterdir())) == 2


def test_the_upload_byte_total_is_enforced_exactly_and_rolls_back(tmp_path: Path) -> None:
    size = len(xlsx_bytes())
    rig = Rig(tmp_path, max_session_upload_bytes=2 * size)
    sid = new_session(rig.client)
    assert upload(rig.client, sid, xlsx_bytes()).status_code == 201
    assert upload(rig.client, sid, xlsx_bytes()).status_code == 201  # exactly the quota
    before = directory_bytes(rig.settings.data_dir)

    over = upload(rig.client, sid, xlsx_bytes())

    _quota(over, "uploads past")
    assert directory_bytes(rig.settings.data_dir) == before
    files = rig.client.get(f"/api/v1/sessions/{sid}/files").json()["files"]
    assert len(files) == 2


def test_a_file_that_would_cross_the_byte_total_is_refused_after_a_smaller_one_fits(
    tmp_path: Path,
) -> None:
    small = xlsx_bytes()
    rig = Rig(tmp_path, max_session_upload_bytes=len(small) + len(small) // 2)
    sid = new_session(rig.client)
    assert upload(rig.client, sid, small).status_code == 201

    _quota(upload(rig.client, sid, small), "uploads past")


def test_another_session_has_its_own_upload_quota(tmp_path: Path) -> None:
    rig = Rig(tmp_path, max_uploads_per_session=1)
    first, second = new_session(rig.client), new_session(rig.client)

    assert upload(rig.client, first, xlsx_bytes()).status_code == 201
    assert upload(rig.client, second, xlsx_bytes()).status_code == 201
    _quota(upload(rig.client, first, xlsx_bytes()), "1 uploads")


def test_a_session_may_run_only_so_many_turns_and_a_refused_one_creates_nothing(
    tmp_path: Path,
) -> None:
    rig = Rig(
        tmp_path, PipelineFactory(QueuedProvider(single("greeting") * 3)), max_turns_per_session=2
    )
    sid = new_session(rig.client)
    for n in range(2):
        tid = start_turn(rig.client, sid, f"hello {n}").json()["turn_id"]
        wait_finished(rig.client, sid, tid)

    third = start_turn(rig.client, sid, "hello again")

    _quota(third, "2 turns")
    state = rig.client.get(f"/api/v1/sessions/{sid}").json()
    assert len(state["turns"]) == 2 and state["busy"] is False
    assert sorted(p.name for p in (rig.settings.outputs_dir / session_key(sid)).iterdir()) == [
        "t0001",
        "t0002",
    ]
    assert rig.app.state.runner.running == 0  # no slot was taken


def test_the_quota_messages_follow_the_settings(tmp_path: Path) -> None:
    rig = Rig(tmp_path, max_uploads_per_session=1, max_session_upload_bytes=3 * 1024 * 1024)
    sid = new_session(rig.client)
    upload(rig.client, sid, xlsx_bytes())

    assert "1 uploads" in upload(rig.client, sid, xlsx_bytes()).json()["error"]["message"]


@pytest.mark.parametrize(
    "name",
    [
        "max_uploads_per_session",
        "max_session_upload_bytes",
        "max_turns_per_session",
        "max_total_bytes",
        "access_max_tokens",
    ],
)
def test_the_quotas_must_be_positive_integers(tmp_path: Path, name: str) -> None:
    for bad in (0, -1, 1.5, True):
        with pytest.raises(SettingsError, match=name):
            Settings(data_dir=tmp_path, **{name: bad})


def test_the_quota_defaults_are_the_designs(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)

    assert settings.max_uploads_per_session == 20
    assert settings.max_session_upload_bytes == 100 * 1024 * 1024
    assert settings.max_turns_per_session == 50
    assert settings.session_ttl_hours == 24


def test_the_quotas_and_ttl_come_from_their_variables(tmp_path: Path) -> None:
    env = {
        "WORKBUDDY_ENV": "dev",
        "WORKBUDDY_DATA_DIR": str(tmp_path),
        "WORKBUDDY_SESSION_TTL_HOURS": "0.5",
        "WORKBUDDY_SWEEP_INTERVAL_S": "7",
        "WORKBUDDY_MAX_UPLOADS_PER_SESSION": "3",
        "WORKBUDDY_MAX_SESSION_UPLOAD_BYTES": "1000",
        "WORKBUDDY_MAX_TURNS_PER_SESSION": "4",
        "WORKBUDDY_MAX_TOTAL_BYTES": "5000",
    }

    settings = Settings.from_env(env)

    assert (settings.session_ttl_hours, settings.sweep_interval_s) == (0.5, 7)
    assert settings.max_uploads_per_session == 3
    assert settings.max_session_upload_bytes == 1000
    assert settings.max_turns_per_session == 4
    assert settings.max_total_bytes == 5000
    with pytest.raises(SettingsError, match="finite"):
        Settings.from_env({**env, "WORKBUDDY_SESSION_TTL_HOURS": "inf"})
    with pytest.raises(SettingsError, match="number"):
        Settings.from_env({**env, "WORKBUDDY_SESSION_TTL_HOURS": "soon"})


# -- The global storage cap -----------------------------------------------------------------------


def test_at_the_cap_a_new_session_and_an_upload_get_503_and_the_sweep_frees_room(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path, max_total_bytes=1)
    first = new_session(rig.client)  # empty root: room (the folders alone hold no bytes)
    assert upload(rig.client, first, xlsx_bytes()).status_code == 201
    rig.mono.advance(10)  # the meter's cache

    session = rig.client.post("/api/v1/sessions")
    refused_upload = upload(rig.client, first, xlsx_bytes())

    for response in (session, refused_upload):
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "storage_full"
    assert rig.exists(first)  # the cap never deletes a live session

    rig.wall.advance(3 * HOUR)
    rig.sweep()  # idle: now it goes, the cache is cleared
    assert rig.client.post("/api/v1/sessions").status_code == 201


def test_the_sweep_never_deletes_a_live_session_to_make_room(tmp_path: Path) -> None:
    rig = Rig(tmp_path, max_total_bytes=1)
    sid = new_session(rig.client)
    upload(rig.client, sid, xlsx_bytes())
    rig.mono.advance(10)

    assert rig.client.post("/api/v1/sessions").status_code == 503
    assert rig.sweep().removed == ()
    assert rig.exists(sid)


def test_the_meter_counts_files_and_not_links_and_caches_for_a_moment(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "a").write_bytes(b"x" * 100)
    (root / "sub").mkdir()
    (root / "sub" / "b").write_bytes(b"y" * 50)
    (root / "link").symlink_to(tmp_path)
    clock = FakeClock()
    meter = StorageMeter(Settings(data_dir=root, max_total_bytes=140), clock, cache_s=2.0)

    assert directory_bytes(root) == 150
    assert meter.used_bytes() == 150 and meter.full()
    (root / "a").unlink()
    assert meter.used_bytes() == 150  # cached
    clock.advance(3)
    assert meter.used_bytes() == 50 and not meter.full()
    (root / "sub" / "c").write_bytes(b"z" * 100)
    meter.invalidate()
    assert meter.full()


def test_the_default_cap_is_in_bytes_and_set_by_its_variable(tmp_path: Path) -> None:
    assert Settings(data_dir=tmp_path).max_total_bytes == 2 * 1024**3


# -- Binding and CORS --------------------------------------------------------------


def test_the_server_binds_every_interface_only_in_production(tmp_path: Path) -> None:
    assert locked_settings(tmp_path).bind_host == "0.0.0.0"
    assert Settings(data_dir=tmp_path, env="dev").bind_host == "127.0.0.1"
    assert Settings(data_dir=tmp_path, demo_password="x").bind_host == "127.0.0.1"


def test_a_public_address_is_refused_outside_production_and_loopback_is_always_fine(
    tmp_path: Path,
) -> None:
    dev = Settings(data_dir=tmp_path, env="dev")
    prod = locked_settings(tmp_path)

    assert choose_host(dev, None) == "127.0.0.1"
    assert choose_host(dev, "localhost") == "localhost"
    assert choose_host(dev, "::1") == "::1"
    assert choose_host(prod, None) == "0.0.0.0"
    assert choose_host(prod, "127.0.0.1") == "127.0.0.1"
    assert choose_host(prod, "0.0.0.0") == "0.0.0.0"
    for host in ("0.0.0.0", "::", "192.168.1.5", "example.com"):
        with pytest.raises(SettingsError, match="non-loopback"):
            choose_host(dev, host)
    assert is_loopback("127.0.0.2") and not is_loopback("10.0.0.1")


def test_with_no_cors_origins_no_cors_header_is_ever_sent(tmp_path: Path) -> None:
    client = TestClient(create_app(locked_settings(tmp_path), lambda p: None))

    simple = client.get("/healthz", headers={"Origin": "https://evil.example"})
    preflight = client.options(
        "/api/v1/sessions",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )

    assert not any(h.lower().startswith("access-control-") for h in simple.headers)
    assert not any(h.lower().startswith("access-control-") for h in preflight.headers)


def test_listed_cors_origins_get_headers_and_nobody_else_does(tmp_path: Path) -> None:
    settings = locked_settings(
        tmp_path, cors_origins=("https://app.example", "https://b.example:8443")
    )
    client = TestClient(create_app(settings, lambda p: None))

    ok = client.options(
        "/api/v1/sessions",
        headers={
            "Origin": "https://app.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    other = client.options(
        "/api/v1/sessions",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    actual = client.get("/healthz", headers={"Origin": "https://app.example"})

    assert ok.status_code == 200
    assert ok.headers["access-control-allow-origin"] == "https://app.example"  # never "*"
    assert "access-control-allow-credentials" not in ok.headers
    assert "access-control-allow-origin" not in other.headers
    assert actual.headers["access-control-expose-headers"] == "Retry-After"


def test_a_wildcard_or_malformed_cors_origin_is_refused(tmp_path: Path) -> None:
    for bad in ("*", "app.example", "https://app.example/path", "https://app.example/"):
        with pytest.raises(SettingsError, match="origin"):
            locked_settings(tmp_path, cors_origins=(bad,))


def test_cors_origins_come_from_a_comma_separated_variable(tmp_path: Path) -> None:
    env = {
        "WORKBUDDY_ENV": "dev",
        "WORKBUDDY_DATA_DIR": str(tmp_path),
        "WORKBUDDY_CORS_ORIGINS": "https://a.example, HTTPS://B.example:443 ,",
    }

    assert Settings.from_env(env).cors_origins == ("https://a.example", "https://b.example")


def test_retry_after_is_readable_cross_origin_on_a_429(tmp_path: Path) -> None:
    from backend.settings import DEFAULT_RATE_LIMITS, BucketSpec

    limits = {**DEFAULT_RATE_LIMITS, "healthz": BucketSpec(1, 3600)}
    settings = locked_settings(tmp_path, cors_origins=("https://app.example",), rate_limits=limits)
    client = TestClient(create_app(settings, lambda p: None, clock=FakeClock()))
    headers = {"Origin": "https://app.example"}
    client.get("/healthz", headers=headers)

    limited = client.get("/healthz", headers=headers)

    assert limited.status_code == 429
    assert limited.headers["access-control-expose-headers"] == "Retry-After"
    assert limited.headers["access-control-allow-origin"] == "https://app.example"


# -- What the server doesn't serve, and the headers it always sends -------------------------------


@pytest.mark.parametrize("env", ["production", "dev", ""])
def test_the_docs_and_the_schema_are_not_served_in_any_environment(
    tmp_path: Path, env: str
) -> None:
    from tests.backend.helpers import BEARER

    settings = Settings(
        data_dir=tmp_path / env,
        env=env,
        demo_password="x" if env != "dev" else None,
        public_origin="https://demo.example" if env == "production" else "",
        rate_limits=RELAXED_LIMITS,
    )
    client = TestClient(create_app(settings, lambda p: None))
    headers = {"Authorization": "Bearer x"} if env != "dev" else BEARER

    for path in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"):
        assert client.get(path, headers=headers).status_code == 404, path


def test_hsts_is_sent_in_production_only_and_clickjacking_protection_always(
    tmp_path: Path,
) -> None:
    prod = TestClient(create_app(locked_settings(tmp_path), lambda p: None))
    dev = TestClient(
        create_app(
            Settings(data_dir=tmp_path / "d", env="dev", rate_limits=RELAXED_LIMITS), lambda p: None
        )
    )

    for client in (prod, dev):
        response = client.get("/healthz")
        assert response.headers["Content-Security-Policy"] == "frame-ancestors 'none'"
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["Cache-Control"] == "no-store"
    assert prod.get("/healthz").headers["Strict-Transport-Security"] == "max-age=31536000"
    assert "Strict-Transport-Security" not in dev.get("/healthz").headers
