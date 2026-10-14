# feat/pdf-extract: fixes for review round 3

**`deploy/` changed in this round** (the app's `mem_limit` goes from 1g to 2g, `deploy/docker-compose.yml`), and so did the `Dockerfile` (pip retries) and the CI workflow. See items 7, 9 and 10.

For "Review round 3 (commit f82951a)" in `docs/reviews/feat-pdf-extract-review.md`: DON'T MERGE, R3-H1, R3-M1 and R3-M2; and for the owner's decisions after the checkpoint at `1575f04`. This is the last round for this branch: if round 4 ends DON'T MERGE, the PR stays a draft and the owner's advisor decides.

Nothing is downgraded or skipped. Where the owner told me to adopt the reviewer's prototype (`scripts/review_harness/feat-pdf-extract/r3_prototype_fix.diff`) I did, without loosening it.

## What the owner decided, and what was done

| # | Decision | Done |
|---|---|---|
| 1 | Adopt the prototype for R3-H1 and R3-M1; its refusal gives the real reason | `skills/pdf/_worker/geometry.py` `text_layout_problem` (pure, with `Glyph`), called from `main.py` `_text_layout_problem`. Thresholds are the prototype's (0.5 em across cells, 1.0 em inside one), measured on advance boxes (`FPDFText_GetLooseCharBox`), baselines and font sizes, on lines rebuilt per table row. The object rule, the crossing rule and the beside-the-table rule are kept. The prototype's `PROTO_*` environment variables are gone. `REFUSALS["text_overflows"]` now reads "some text in the table sits too close to a cell's border, or runs past it, for me to be sure which cell it belongs to". |
| 2 | Keep the Write tier | Unchanged. |
| 3 | Keep refusing PDFs without Landlock | Unchanged; the release check now has a test that requires it (item 6, s16). |
| 4 | "Extract the table and give me a report" on a PDF: keep today's behaviour; put "analyse the extracted sheet" in the backlog | Unchanged; `docs/backlog.md` section 7, "Analyse the extracted sheet". |
| 5 | Keep `dtype=object` in the cleaning skills | Unchanged. |

## R3-H1 and R3-M1 (HIGH, MEDIUM): one mechanism, replaced

R3-H1 (a sentence in several text objects delivered split across two cells) and R3-M1 (`Mon - Fri` refused as "columns not ruled") were the third and fourth findings in `_text_layout_problem` after rounds 1 and 2, which is why the owner chose the prototype: the check now reasons about where the characters sit, not about how the PDF grouped them.

- **How it works.** For each row of the table, every printed character is placed: baseline, advance box, font size, cell. They are put into lines by baseline, sorted left to right, and each pair of neighbours is judged by the gap in ems of the smaller font: different cells and less than 0.5 em is `text_overflows`; the same cell and more than 1.0 em is `columns_not_ruled`.
- **Measured, not assumed.** One space is 0.28 em in Helvetica, also beside a dash or a tilde (16 punctuation cells: the largest in-cell gap in every one is 0.28 em), so a cell is 3.6 times short of being called two columns. The review measured a rule in a word space at 0.20 to 0.28 em, below 0.5 em.
- **Compared with the prototype case by case.** `r3_layout.py` (758 cases: punctuation, words and glyphs per object at 10 and 12 pt, row- and column-major, LibreOffice exports) run on my implementation and on the prototype applied to this head: **0 differences**; no wrong delivery in any group; all 16 punctuation cells and `double_spaces` delivered right; 5 of 6 LibreOffice Calc punctuation cells delivered, `Smith - one` refused; the 3 LibreOffice Writer tables refused (the cost the review named: tight auto-fit columns, 0.30 to 0.35 em from the neighbour).
- **Tests** (all fail on `1575f04`: run there with the new fixture, 14 of the end-to-end tests fail):
  - `tests/skills/pdf/test_text_layout.py`: the pure rule on characters placed by hand (thresholds on both sides, the smaller font size, the drawing order shuffled, two lines); the real worker with the rule swept through a sentence drawn as a note with one bold word (`text_unit="rich"`: three text objects), word by word with and without the trailing space, per word, per glyph, row- and column-major, 10 and 12 pt; **the rule placed exactly in every word space at its start, middle and end** (a 3-point sweep can step over a 2.8-point space, and did: the first version of that test passed on the old code for the bold-word case; the targeted version fails on it for `rich` and `word_space`); ten cells with dashes, tilde, colon, double spaces, delivered whole; the refusal sentence names the real reason and never says "ruled".
  - `tests/buddy/test_pdf_extract_fixes3.py` (three speakers): the dashes table is delivered with the right cells; the bold-word table is refused with the real reason in the reply, no file, nothing from the file in the reply.
  - `tests/pdf_fixtures.py`: `hand_table_pdf` gets `text_unit`, `column_major`, `font_size`, `listener_port`; coordinates that were whole numbers are written as before.

## R3-M2 (MEDIUM, security): the worker could kill or starve the server

- `skills/pdf/_worker/lockdown.py`: the architecture tables are now a dataclass with a third group. **Refused whatever the arguments:** `setpriority`, `ioprio_set` (their first argument is not a pid), with the existing signal and handle calls. **Allowed only on pid 0 or the worker's own pid:** `prlimit64`, `sched_setparam`, `sched_setscheduler`, `sched_setaffinity`, `sched_setattr`, `migrate_pages`, `move_pages` (the reviewer's list). `kill` and `tgkill` as before. Only the low 32 bits of the argument are compared, the way the kernel reads a `pid_t`.
- `selftest` now makes each of the 14 calls on the server from inside the worker, in a form that does nothing if it gets through (signal 0, an invalid `which`, a read-only `prlimit64`, an empty node list), and checks `EPERM`; and checks that the same calls on the worker itself are not refused. Their numbers are written out a second time in the self-test, and a third time in the tests from the kernel's tables, so a wrong number in the filter is caught.
- **Tests that kill round 3's survivors** (my own runs in a throwaway copy, `tkill`, `rt_sigqueueinfo`, `rt_tgsigqueueinfo`, `pidfd_send_signal`, `tgkill` unchecked, plus every new entry; the filter alone, so that Landlock's own refusals can't hide a missing one: `migrate_pages` and `move_pages` are refused by the kernel under Landlock whatever the filter says, and are killed only by the filter-only test):
  - s10 (`tgkill` unchecked), s11 (`tkill`), s12 (`rt_sigqueueinfo`), s13 (`pidfd_send_signal`): killed by `test_the_worker_cannot_open_a_socket_start_a_program_or_a_process` and `test_without_landlock_the_filter_alone_refuses_every_call_on_the_server`. s16: see item 6 below.
  - `test_a_worker_that_tries_to_change_or_kill_the_server_changes_nothing`: the real attempts (a one-second CPU limit, five file descriptors, nice 19, affinity, `SCHED_BATCH`, `tgkill`, `sigqueue`, `kill`), made by a worker whose parent is a throwaway server; every one refused, and the server's limits, nice value, affinity and policy afterwards are what they were.
  - `tests/skills/pdf/test_seccomp_filter.py`: the filter's BPF program run through a small interpreter on both x86-64 and arm64 numbers (arm64 was never exercised before): foreign architecture and x32 kill, signals refused for every pid, `kill`/`tgkill` only on the own pid, the pid-first calls on 0 or the own pid only, the high half of the argument ignored, threads still start, an ordinary call is allowed, and the tables equal the kernel's numbers.

## 6. Mutations s10-s13 and s16 (R3-L2)

s10, s11, s12, s13: above. **s16** (the release check ignores `filesystem_confined`): `tests/skills/pdf/test_release_check.py` runs `skills.pdf.__main__.main` against a worker outcome in which every check is true except the filesystem confinement and requires exit 1 and no "The PDF sandbox holds." line; any single false check, an empty answer, and a worker that did not run all fail it; and the real `python -m skills.pdf` must print the line and exit 0 on the machine running the tests (it fails, loudly and with the output, on a kernel without Landlock or a container that blocks the filter, instead of skipping).

## 7. Memory

`sandbox.MAX_CONCURRENT_WORKERS = 1` (it was 2: two workers could use 2 GiB of address space and a dense PDF measured 706 MiB resident against a 1 GB container); the worker sets its own `oom_score_adj` to 1000 (`lockdown.prefer_to_be_killed_first`, called from `apply` before the filter; it keeps the descriptor open so `selftest` can read back what the kernel holds after Landlock closes `/proc` to it; if the setting is refused the worker parses nothing); `deploy/docker-compose.yml` `mem_limit: 2g`. Tests: `test_the_worker_sets_its_oom_score_adj_to_1000`, the self-test's `oom_score_adj_is_1000` in the real worker, `test_a_refused_oom_score_adj_is_a_failure_to_lock_down_not_a_shrug` and `test_a_worker_that_cannot_lower_its_oom_priority_reads_no_pdf` (fail closed), `test_the_worker_cap_is_one_place`, and `test_the_apps_memory_limit_has_room_for_the_server_and_one_pdf_worker` (the compose limit is at least one worker's address space plus 1 GiB for the server).

**For the owner:** with one worker, a second person's PDF waits up to 15 s for the first (a worker may run for 20 s) and is then answered "PDF reading is busy right now". `docs/deploy.md` now says a 2 GB VM cannot give the app a 2 GB limit; I don't know the size of your VM.

## 8. The GitHub "tests" job

Cause: `test_active_content_in_a_pdf_is_never_followed` bound port 9 on localhost, which only root may do. Here every test runs as root, so it never failed; GitHub's runner is not root (`PermissionError: [Errno 13]`). I reproduced it (`setpriv` as uid 65534: `port 9 as non-root: Permission denied`). The test took the error for a reason to skip but caught it in the wrong place, so it errored instead of skipping, which is better than a silent skip. It now binds port 0, and the PDF names that port (`hand_table_pdf(..., listener_port=port)`); it passes as uid 65534. Nothing in the sandbox tests is skipped for a missing kernel feature: they assert, so a runner without Landlock fails and says which check, and the release check refuses (item 6). The earlier note that this was "a possible timing flake" (R2-L3) was wrong.

## 9. CI: the sandbox inside the image

`.github/workflows/tests.yml`, "docker build" job: after the build, `docker run --rm workbuddy:ci python -m skills.pdf | tee pdf-sandbox.txt` (with `set -eo pipefail`), then `grep --fixed-strings --line-regexp "The PDF sandbox holds." pdf-sandbox.txt`. A test (`test_the_image_job_requires_the_pdf_sandbox_to_hold_inside_the_image`) fails if either line goes. Not run here: no container runtime in this environment, so whether GitHub's Docker lets the sandbox build is for the job to say.

## 10. Dockerfile

`pip install --retries 10 --timeout 60 --require-hashes --no-deps -r requirements-runtime.lock`; the comment says why; `test_the_dockerfiles_pip_install_retries_a_failed_download`. Not run here: the build.

## Mutations

Run in a throwaway copy of this head (`scratch`, not committed). Killed: the 8 sandbox-filter mutations the review listed or implied (s09 any pid, s10-s14, s25 pid 0 refused, s26 own pid refused), every table entry of the 7 pid-first calls and of `setpriority` and `ioprio_set`, a wrong arm64 number, no `prefer_to_be_killed_first` in `apply`, a score of 500, a refused setting ignored, the release check ignoring Landlock or accepting any true, two workers, a 1g limit, the Dockerfile without retries, the CI step without the sandbox run or without the grep; in the layout check, a run threshold of 0.25 em, a column threshold of 1.6 em, no sort by x, no grouping by baseline, the cross-cell check off. **Survives: l05, measuring tight glyph boxes instead of advance boxes** (`FPDFText_GetCharBox`): the 0.5 em and 1.0 em thresholds are far enough from the sidebearings (about 0.05 to 0.1 em) that no test puts a gap between them; I kept the advance box because that is what the thresholds were measured on, and say here that no test pins it. (l07 and l08, the wrong reason code and the sentence saying "ruled", see the table at the end.)

## Checks

(Filled in once the suite has finished.)
