# ruff: noqa: E501 — reviewer tooling, not product code
"""Round 2: mutations of the round-1 fixes, each in its own ``git archive`` copy of a commit.

    python scripts/review_harness/feat-release-safety/round2/mutations2.py [--rev 7cda772] [--only N01]

Each mutation undoes one fix (one exact text replacement, which must match once), then runs the
tests that are meant to guard it. CAUGHT = a test failed (good); SURVIVED = the guard is vacuous.
The real-Docker tests in tests/deploy/test_release_scripts.py need Docker and python:3.11-slim.
"""

from __future__ import annotations

import argparse
import subprocess
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
REL = "tests/deploy/test_release_scripts.py"
SURV = "tests/deploy/test_saved_data_survives.py"
FILES = "tests/deploy/test_deploy_files.py"


def t(*names: str) -> list[str]:
    return ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", *names]


# (id, what, file, old, new, command)
MUTATIONS = [
    ("N00", "no mutation (the copy itself must pass)", "README.md", "", "", t("tests/deploy")),
    # N01 (mount the caller's archive instead of the private copy) was dropped: inside helper(), $1 is
    # the shell script, so a one-line mutation can't name the archive. The copy is backed up by N02.
    ("N02", "H1: the safety backup prunes again", "deploy/restore.sh",
     'pre="$(WORKBUDDY_BACKUP_PRUNE=0 "$DEPLOY_DIR/backup.sh"', 'pre="$("$DEPLOY_DIR/backup.sh"',
     t(REL + "::test_restoring_the_oldest_backup_works_when_the_folder_is_full")),
    ("N03", "H1: backup.sh ignores WORKBUDDY_BACKUP_PRUNE", "deploy/backup.sh",
     'if [ "${WORKBUDDY_BACKUP_PRUNE:-1}" != "0" ]; then', 'if true; then',
     t(REL + "::test_backups_keep_only_the_newest_few_unless_told_not_to", REL + "::test_restoring_the_oldest_backup_works_when_the_folder_is_full")),
    ("N04", "H1: empty /data before unpacking (the old order)", "deploy/restore.sh",
     "rm -rf /data/.restore-staging && mkdir /data/.restore-staging\n",
     "find /data -mindepth 1 -delete; rm -rf /data/.restore-staging && mkdir /data/.restore-staging\n",
     t(REL + "::test_a_failed_unpack_leaves_data_alone_and_starts_the_app_again")),
    ("N05", "H1: a failed unpack doesn't start the app again", "deploy/restore.sh",
     "  compose up -d >/dev/null 2>&1 || true\n  die \"unpacking", "  die \"unpacking",
     t(REL + "::test_a_failed_unpack_leaves_data_alone_and_starts_the_app_again")),
    ("N06", "H1: staging folder left behind after success", "deploy/restore.sh",
     "cd /data && rmdir /data/.restore-staging'", "cd /data'",
     t(REL + "::test_restoring_the_oldest_backup_works_when_the_folder_is_full")),
    ("N07", "H1: the restore doesn't name the safety backup", "deploy/restore.sh",
     'The data from before is in $pre"', 'Done."',
     t(REL + "::test_the_pre_restore_backup_holds_what_was_there")),
    ("N08", "M1: layout lists folders again", "tests/deploy/data_scenario.py",
     "        if not path.is_file():\n            continue\n", "",
     t(SURV + "::test_the_layout_ignores_empty_folders")),
    ("N09", "M2: failed first release keeps current.env", "deploy/release.sh",
     '    rm -f "$DEPLOY_DIR/current.env"  # nothing is live: a rerun must do the whole release\n', "",
     t(REL + "::test_after_a_failed_first_release_a_rerun_does_the_whole_release")),
    ("N10", "M2: same-commit shortcut without the health check", "deploy/release.sh",
     "  if app_is_healthy; then\n", "  if true; then\n",
     t(REL + "::test_the_same_commit_is_skipped_only_while_it_is_running_and_healthy")),
    ("N11", "M3: '$' allowed", "deploy/lib.sh", "*'$'*|", "",
     t(REL, "-k", "refused_by_name_only")),
    ("N12", "M3: ' #' allowed", "deploy/lib.sh", "*' #'*|", "",
     t(REL, "-k", "refused_by_name_only")),
    ("N13", "M3: compose.sh skips the check", "deploy/compose.sh", "\ncheck_env_syntax\n", "\n",
     t(REL, "-k", "refused_by_name_only")),
    ("N14", "M3: the message prints the value", "deploy/lib.sh",
     'name="${line%%=*}"', 'name="${line}"',
     t(REL, "-k", "refused_by_name_only")),
    ("N15", "M3: \\r allowed", "deploy/lib.sh", "*$'\\r'*|", "",
     t(REL + "::test_a_carriage_return_in_env_is_refused")),
    ("N16", "M4: Caddy access log back", "deploy/Caddyfile",
     "\t\theader_up X-Forwarded-For {remote_host}\n\t}\n", "\t\theader_up X-Forwarded-For {remote_host}\n\t}\n\tlog {\n\t\toutput stdout\n\t}\n",
     t(REL + "::test_caddy_writes_no_access_log")),
    ("N17", "M5: no deploy/ check", "deploy/release.sh",
     'if ! git -C "$REPO_DIR" diff --quiet "$full" -- deploy/; then', 'if false; then',
     t(REL + "::test_a_release_refuses_when_deploy_differs_from_the_commit")),
    ("N18", "L3/L4: backup before the build again", "deploy/release.sh",
     'step "1/5 build the image"\n', 'step "1/5 build the image"\n"$DEPLOY_DIR/backup.sh" || true\n',
     t(REL + "::test_a_failed_build_changes_nothing_and_takes_no_backup")),
    ("N19", "M5 (uncommitted edit): diff only HEAD..commit, not the working tree", "deploy/release.sh",
     'diff --quiet "$full" -- deploy/', 'diff --quiet HEAD "$full" -- deploy/',
     t(REL + "::test_a_release_refuses_when_deploy_differs_from_the_commit")),
]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--rev", default="HEAD")
    p.add_argument("--only")
    a = p.parse_args()
    for mid, what, file, old, new, cmd in MUTATIONS:
        if a.only and mid != a.only:
            continue
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "t"
            tree.mkdir()
            archive = Path(tmp) / "a.tar"
            subprocess.run(["git", "archive", "-o", str(archive), a.rev], cwd=ROOT, check=True)
            with tarfile.open(archive) as tf:
                tf.extractall(tree, filter="data")
            target = tree / file
            text = target.read_text(encoding="utf-8")
            if mid == "N00":
                old, new = text, text
            if text.count(old) != 1:
                print(f"{mid} SKIP  pattern matched {text.count(old)} times  ({what})", flush=True)
                continue
            target.write_text(text.replace(old, new), encoding="utf-8")
            done = subprocess.run(cmd, cwd=tree, capture_output=True, text=True, timeout=900)
            tail = (done.stdout.strip().splitlines() or [""])[-1][:110]
            verdict = "CAUGHT" if done.returncode != 0 else "SURVIVED"
            if mid == "N00":
                verdict = "OK" if done.returncode == 0 else "COPY-FAILS"
            print(f"{mid} {verdict:8} exit={done.returncode}  {what}  | {tail}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
