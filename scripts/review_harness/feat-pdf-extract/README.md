# feat/pdf-extract: reviewer's probes (round 1, head `e841088`, base `origin/main` `89625e7`)

Real app (`backend.main.create_app`) + real `build_engine` (real skills, the real sandboxed worker, Hands, Inspector,
Voice), fake LLM only. No key, no network. Run from this folder; add `2>/dev/null` to hide the Splink noise.
`pl.py` is the library (imports `../feat-follow-up-turns/fu.py`, not edited); `pdfs.py` builds the PDFs
(reportlab, LibreOffice `soffice` for realistic exports, hand-drawn canvases). Output goes to `out/` (git-ignored).

    python probe_a_worker.py [case ...]       # the skill's `extract` reading vs the Inspector's `verify` reading on ~25 PDFs:
                                              # do they agree, and does the agreed table equal what was drawn?
    python probe_b_pipeline.py [pNN ...] [--speakers template,friend,focus]
                                              # 20 PDF turns (and follow-ups) through the API in three speakers:
                                              # reply, delivered cells with types, original SHA-256
    python probe_c_sandbox.py                 # what code inside the worker's lockdown can still do (files, io_uring, x32, fork)
    python probe_d_dtype.py [--tree BASE]     # design decision 8 (dtype=object) on mixed-type workbooks, head vs base
    python probe_e_api.py                     # odd PDF uploads, and an upload/turn while every worker place is taken
    python mutations.py $WT [name ...]        # 24 one-place mutations; $WT = `git worktree add --detach $WT HEAD`, never the branch

Shared (not edited): `python ../harness.py --out out/h_head.json` and `--tree <base> --out out/h_base.json`,
then `python ../feat-reply-quality/cmp_harness.py out/h_base.json out/h_head.json`.

The counts and the cases quoted are in `docs/reviews/feat-pdf-extract-review.md`.

## Round 2 (head `37e65eb`)

Same setting. New scripts (the round-1 ones above are reused unchanged):

    python r2_overflow_sweep.py [--text T] [--step 1]   # hand-drawn grid, a sentence run into the empty next cell; the first
                                                        # column's width swept so the rule lands everywhere in it: how many
                                                        # widths would be delivered split (verifier passes)
    python r2_lo_overflow.py                            # the same with 12 LibreOffice Calc exports (note longer than its column,
                                                        # next cell empty)
    python r2_pipeline.py [qNN ...]                     # q01-q06 through the app, three speakers (overflow, partial column rules,
                                                        # extract + report, one row, header only)
    python r2_sandbox.py [--no-landlock]                # the full lockdown + Landlock, as main.py applies it; then: another
                                                        # upload, chmod, the server's environ, signals, pidfd_getfd on the
                                                        # server's sockets, io_uring, clone, unshare, memfd, /tmp
    python r2_mutations.py $WT [name ...]               # 20 one-place mutations of the round-1 fixes

To check a prototype fix, copy the `r2_*.py` files into a throwaway worktree's copy of this folder and run them there
(they import the product code from the tree they sit in).
