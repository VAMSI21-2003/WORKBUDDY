# Fixes after review round 1: `feat/backend-retention`

Review: `docs/reviews/backend-api-retention-review.md`, round 1 (commit `4352d1c`), verdict DON'T MERGE.

## M1 (blocking): fixed

`backend/retention.py`, `remove_tree`: a regular file with no owner write bit gets `chmod(S_IWRITE | S_IREAD)` before `os.unlink`, as the reviewer specified. A symlink is never chmodded.

Tests (`tests/backend/test_retention.py`), both fail on the round-1 code and pass now:
- `test_read_only_files_are_deleted_where_a_read_only_file_cannot_be_unlinked` applies Windows' rule to `os.unlink` (a read-only file can't be deleted) and asserts a delivered session is removed on the first sweep, with `failed == ()`.
- `test_a_symlink_is_not_chmodded_when_its_target_is_read_only` asserts the link is removed and its read-only target keeps its mode and content.

Not run on Windows: the rule is simulated on Linux, as in the reviewer's probe.

## L8: fixed

The test count in `backend-api-retention-impl.md` is corrected (base 2258, branch adds 182).

## Not changed

M2 and L1 to L7 are the reviewer's own "Deferred" items, already in `docs/backlog.md` (section "backend-api-retention review (round 1), deferred"). The writer didn't touch them. `CLAUDE.md`'s "Decided behaviour" has nothing to report for the owner from this round; the reviewer notes backlog P2 (ydata-profiling telemetry, "before any shared deployment") is still open.
