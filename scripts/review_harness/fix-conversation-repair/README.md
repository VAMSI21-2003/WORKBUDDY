# fix/conversation-repair: reviewer's probes (round 1, head `556d30d`; round 2, head `15f5c92`; base `a4c959b`)

Real app (`backend.main.create_app`) + real `build_engine` (real skills, Hands, Inspector, Voice), fake LLM only. No key, no network.
`cr.py` is the library (imports `../feat-follow-up-turns/fu.py`, which imports `pr3.py` / `harness.py` from other branches' folders; none edited).
Run from this folder. `--tree DIR` (a `git worktree` of `origin/main`) runs a probe on the base; `--out FILE` moves the output (default `out/`, git-ignored).
Add `2>/dev/null` to hide the Splink training noise. Every probe prints the reply and records the original upload's SHA-256 check (`uploads_unchanged`).

    python probe_a_transcript.py     # the owner's 8-turn transcript x template/friend/focus (head vs --tree base)
    python probe_b_pending.py        # the waiting request: resumed, Brain says no, Brain misreads (b3), no file named, string "true", chain, restart, ...  (--all for all speakers)
    python probe_c_names.py          # buddy.source.match_file_name on ~60 wordings + timing (no app needed; --tree works)
    python probe_d_names_api.py      # file naming through the API: output names ("save as cleaned.xlsx"), delivered names, pinned, same name twice, hostile upload names
    python probe_e_retry_logs.py     # Think/Brain fail once/twice/raise with a key + message text in the exception; asserts nothing of either reaches the logs
    python probe_f_wrong_twice.py    # F1: an unknown file named twice, then the right one (Brain simulated realistically)
    python probe_g_report_loose.py   # the trim/case finding through report, chain and follow-up paths; counts re-derived independently with pandas
    python probe_h_unclear_paths.py  # an unknown spreadsheet name in a greeting / unsupported request / thanks
    python probe_i_stage_fail.py     # F2: a stage raises on the answering turn: is the waiting request kept?
    python mutations.py $WT          # 32 one-place mutations; $WT = throwaway `git worktree add --detach $WT HEAD`, never the branch

Also run (shared, not edited): `scripts/review_harness/harness.py --out X.json` on head and base, then `feat-reply-quality/cmp_harness.py base.json head.json`
(532 turns: delivered files identical on all, originals unchanged on all; the only differences are "exact duplicate row(s)" and the file name in the "I worked on" sentence).

## Round 2 (head `15f5c92`)

Rerun unchanged on the new head: `probe_a`, `probe_b` (`--all`), `probe_c`, `probe_d`, `probe_f`, `probe_h`, `probe_i` (F1 to F4 are fixed). New:

    python probe_j_resumed_replies.py    # where the fixed "I took your last message as the answer ... went on with your earlier request." sentence appears, and is it true there (Think fails twice, Brain says answers_pending but unclear, plan declined, Think asks another question, follow-up already satisfied)
    python probe_k_name_keywords.py      # a correct resume whose message is a file name containing an action phrase ("duplicate rows jan.xlsx"): the F3 cross-check now reads the message too
    python probe_l_more.py               # resumed report; resumed unsupported; one upload + "save it as cleaned.xlsx" / an unknown name
    python probe_m_brain_fails_twice.py  # Brain returns non-JSON twice on the answering turn: pending kept? (realistic Brain on the retry; mutation n04 survives the suite)
    python mutations2.py $WT             # 20 one-place mutations of the round-2 code; $WT = throwaway `git worktree add --detach $WT HEAD`; output out/mutations2.txt

A prototype fix for the sentence (scratch worktree, not in the branch): in `TemplateSpeaker._no_change_reply` replace `if request.resumed and not asked:` by `if request.resumed and outcome.report_asked and outcome.report_given:`; `probe_j` then shows no sentence for the unread/unrun cases and `tests/backend/test_conversation_repair.py`, `test_voice.py`, `test_reply_quality.py` still pass (415).
