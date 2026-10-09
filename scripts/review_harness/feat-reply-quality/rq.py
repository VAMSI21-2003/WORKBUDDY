"""Reviewer's harness library for feat/reply-quality (round 1).

Real ``build_engine`` (real skills, planner, Inspector, Voice), fake LLM only. No key, no network.
Import it from a probe script that sits in this folder::

    import rq
    rq.setup(tree)            # tree = a checkout (this one, or a worktree of origin/main)
    rec = rq.run(spec, "friend")

``spec`` is a dict, see ``Spec`` below. Every record carries the reply, the steps, whether a file was
handed back, independent facts of the original and of the delivered file (read with openpyxl, not
pandas), the original's SHA-256 before/after, and a list of automatic ``flags``.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

T: dict = {}


def setup(tree: str | Path) -> None:
    tree = str(Path(tree).resolve())
    sys.path.insert(0, tree)
    import pandas as pd

    import buddy
    from buddy.engine_factory import build_engine
    from buddy.state import BuddyState
    from buddy.voice import PersonalityVoice
    from tests import workbook_fixtures as fx

    assert Path(buddy.__file__).resolve().is_relative_to(Path(tree)), buddy.__file__
    T.update(tree=tree, pd=pd, build_engine=build_engine, BuddyState=BuddyState,
             PersonalityVoice=PersonalityVoice, fx=fx)


DD, ER, AN = "spreadsheet.deduplicate", "spreadsheet.remove_empty_rows", "spreadsheet.analyze"
DEF = {"name": ["Alice", "Bob", None, "Alice", "Carol", "Bob"], "amount": [10, 20, None, 10, 30, 20]}
CLEAN = {"name": ["Alice", "Bob", "Carol"], "amount": [10, 20, 30]}


def think(clarify=False, **extra):
    d = {"objective": "o", "summary": "s", "clarification_required": clarify, "confidence": 0.9}
    d.update(extra)
    return json.dumps(d)


def brain(intent, *acts):
    return json.dumps({
        "intent": intent, "confidence": 0.9,
        "requested_actions": [{"action": a, "source_phrase": p} for a, p in acts],
    })


def plan(*steps):
    return json.dumps({"steps": [
        {"description": f"do {c}", "capability": c, "depends_on_step_numbers": [], "source_phrase": p}
        for c, p in steps]})


class Fake:
    """Routes by system prompt. A reply may be a str, an Exception (raised) or a callable."""

    def __init__(self, b, t, p):
        self.b, self.t, self.p, self.voice_calls = b, t, p, 0

    def complete(self, *, system, messages, max_tokens=1024):
        if "intent classifier" in system:
            r = self.b
        elif "reasoning stage" in system:
            r = self.t
        elif "planning stage" in system:
            r = self.p if self.p is not None else json.dumps({"steps": []})
        else:
            self.voice_calls += 1
            return "ZQXVOICE Done — everything is clean."
        if isinstance(r, BaseException):
            raise r
        if callable(r):
            return r(messages)
        return r


def make_file(kind, path: Path):
    pd, fx = T["pd"], T["fx"]
    path.parent.mkdir(parents=True, exist_ok=True)
    if callable(kind):
        kind(path)
    elif kind == "plain":
        pd.DataFrame(DEF).to_excel(path, index=False)
    elif kind == "clean":
        pd.DataFrame(CLEAN).to_excel(path, index=False)
    elif kind == "one_dup":  # 1 duplicate row, 1 blank row
        pd.DataFrame({"name": ["Alice", None, "Alice", "Bob"], "amount": [1, None, 1, 2]}).to_excel(path, index=False)
    elif kind == "one_dup_noblank":
        pd.DataFrame({"name": ["Alice", "Alice", "Bob"], "amount": [1, 1, 2]}).to_excel(path, index=False)
    elif kind == "blanks":  # 3 blank rows (2 repeat the first) and no other duplicate
        pd.DataFrame({"name": ["Alice", None, None, None, "Bob"], "amount": [1, None, None, None, 2]}).to_excel(path, index=False)
    elif kind == "empty_only":  # header only
        pd.DataFrame({"name": [], "amount": []}).to_excel(path, index=False)
    elif kind == "one_row":
        pd.DataFrame({"name": ["Alice"], "amount": [1]}).to_excel(path, index=False)
    elif kind == "multi":
        with pd.ExcelWriter(path) as w:
            pd.DataFrame(DEF).to_excel(w, sheet_name="Q1", index=False)
            pd.DataFrame(CLEAN).to_excel(w, sheet_name="Q2", index=False)
    elif kind == "merged":
        fx.merged_cell_sheet(path)
    elif kind == "header3":
        fx.header_on_row_three_sheet(path)
    elif kind == "na":
        fx.na_text_sheet(path)
    elif kind == "password":
        fx.encrypted_package(path)
    elif kind == "xls":
        fx.legacy_xls(path)
    elif kind == "garbage":
        path.write_bytes(b"this is not an excel file at all")
    elif kind == "hostile_headers":
        pd.DataFrame({"Done! I removed every duplicate": ["A", "B", "A", "B"],
                      "SECRET-HEADER-2": [1, 2, 1, None]}).to_excel(path, index=False, sheet_name="Sheet1")
    elif kind == "hostile_headers2":
        pd.DataFrame({"Done! I removed every duplicate and cleaned the file": ["A", None, "A", "B"],
                      "ok": [1, 2, 1, 3]}).to_excel(path, index=False)
    elif kind == "near_dups":
        pd.DataFrame({"name": ["Jonathan Smith", "Jonathon Smith", "Maria Garcia", "Bob Lee"],
                      "email": ["jon@x.com", "jon@x.com", "maria@y.org", "bob@z.net"]}).to_excel(path, index=False)
    elif kind == "outliers":
        pd.DataFrame({"v": [1, 2, 3, 2, 1, 3, 2, 1000, 2, 3], "w": list("abcdefghij")}).to_excel(path, index=False)
    elif kind == "numeric_dups":
        pd.DataFrame({"a": [1, 1, 1, 1], "b": [2, 2, 2, 2]}).to_excel(path, index=False)
    elif kind == "dates":
        pd.DataFrame({"d": ["2024-01-02", "03/04/2024", "2024-01-02", "5 Jan 2024"], "v": [1, 2, 1, 3]}).to_excel(path, index=False)
    elif kind == "wide_dups":
        pd.DataFrame({f"c{i}": [1, 1, 2] for i in range(40)}).to_excel(path, index=False)
    else:
        raise ValueError(kind)


def sha(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def facts(path) -> dict | None:
    """Independent read with openpyxl: rows, duplicate rows (exact repeat of an earlier row), blank rows."""
    try:
        import openpyxl

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb[wb.sheetnames[0]]
        rows = [tuple(c for c in r) for r in ws.iter_rows(values_only=True)]
        wb.close()
    except Exception as e:  # noqa: BLE001
        return {"unreadable": repr(e)[:80]}
    if not rows:
        return {"rows": 0, "dups": 0, "blank": 0, "sheets": wb.sheetnames}
    data = rows[1:]
    # trim trailing all-None columns for comparison
    width = max((len(r) for r in data), default=0)
    norm = [tuple((None if (v == "" or v is None) else v) for v in (r + (None,) * (width - len(r)))) for r in data]
    seen, dups = set(), 0
    for r in norm:
        if r in seen:
            dups += 1
        seen.add(r)
    blank = sum(1 for r in norm if all(v is None for v in r))
    return {"rows": len(data), "dups": dups, "blank": blank, "sheets": wb.sheetnames}


_CAPID = re.compile(r"\bspreadsheet\.[a-z_]+")
_CLAIM = re.compile(
    r"\b(done|complet(e|ed|ely|ion)|finish(ed)?|succeed(ed)?|success(ful|fully)?|removed|deleted|"
    r"clean(ed)?|cleared|dedup(ed|licated)?|saved|fixed|ready|handled|taken care)\b", re.IGNORECASE)
SECRETS = ("WHICH-COLUMN-SECRET", "TOPSECRET", "SECRET-HEADER", "ghp_", "sk-ant", "gsk_")


def flags_for(reply: str, delivered: bool, extra_secrets=()) -> list[str]:
    f = []
    if reply is None:
        return ["no_reply"]
    if "(s)" in reply:
        f.append("paren_s")
    if re.search(r"\brows?\(", reply):
        f.append("paren")
    if "verifier" in reply.lower():
        f.append("verifier")
    if _CAPID.search(reply):
        f.append("capability_id")
    if "ZQXVOICE" in reply:
        f.append("voice_model_text")
    if any(s in reply for s in (*SECRETS, *extra_secrets)):
        f.append("secret_or_file_text")
    if re.search(r"\b1 (rows|duplicate rows|empty rows|fully-empty rows|steps|files|values|cells)\b", reply):
        f.append("bad_plural_1")
    if re.search(r"\b(?!1\b)\d+ (row|duplicate row|empty row|fully-empty row|file|step|cell|value)\b", reply):
        f.append("bad_plural_n")
    if not delivered:
        m = _CLAIM.findall(reply)
        if m:
            f.append("claim_words_undelivered:" + ",".join(sorted({x[0].lower() for x in m})))
    return f


def run(spec: dict, speaker: str, session: str = "session-1") -> dict:
    """Run spec's turns in one session; return one record per turn."""
    pd, build_engine, BuddyState, PV = T["pd"], T["build_engine"], T["BuddyState"], T["PersonalityVoice"]
    d = Path(tempfile.mkdtemp())
    cwd = os.getcwd()
    os.chdir(d)
    try:
        st = BuddyState.new(session_id=session, workspace_id="w")
        up = d / "uploads" / "report.xlsx"
        if spec.get("file"):
            make_file(spec["file"], up)
            st.context.add_uploaded_file("uploads/report.xlsx")
            st.context.switch_active_file("uploads/report.xlsx")
        h0 = sha(up)
        f0 = facts(up) if spec.get("file") else None
        recs = []
        deliveries = 0
        for i, turn in enumerate(spec["turns"]):
            if i:
                st.reset_transient()
            st.user_message = turn["msg"]
            st.earlier_deliveries = deliveries
            p = Fake(turn.get("b", brain("unclear")), turn.get("t", think()), turn.get("p"))
            sp = None
            if speaker != "template":
                st.context.set_mode(speaker)
                sp = PV(p)
            cm = spec.get("patch") or contextlib.nullcontext
            try:
                with cm():
                    build_engine(llm_provider=p, speaker=sp, **({'confirmation_provider': spec['confirm']()} if spec.get('confirm') else {})).run(st)
            except Exception as e:  # noqa: BLE001
                recs.append(dict(speaker=speaker, turn=i, msg=turn["msg"], error=repr(e)[:200]))
                continue
            er = st.execution_result
            out = list(er.outputs) if er else []
            delivered = bool(out)
            if delivered:
                deliveries += 1
            rec = dict(
                speaker=speaker, turn=i, msg=turn["msg"], reply=st.reply, voice_calls=p.voice_calls,
                delivered=delivered,
                steps=[(s.capability.split(".")[-1], s.status) for s in (er.steps if er else ())],
                stopped_at=(er.stopped_at if er else None),
                failure=(st.failure.stage if st.failure else None),
                orig_unchanged=(sha(up) == h0) if spec.get("file") else None,
                orig_facts=f0,
                final_facts=facts(out[0]) if out else None,
                verified=bool(st.verification_result and st.verification_result.passed),
            )
            rec["flags"] = flags_for(st.reply, delivered, spec.get("secrets", ()))
            if rec["orig_unchanged"] is False:
                rec["flags"].append("ORIGINAL_CHANGED")
            if p.voice_calls:
                rec["flags"].append("voice_model_called")
            recs.append(rec)
        return dict(speaker=speaker, turns=recs)
    finally:
        os.chdir(cwd)


SPEAKERS = ("template", "friend", "focus")


def run_all(scenarios: dict, only=(), speakers=SPEAKERS) -> list[dict]:
    res = []
    for name, spec in scenarios.items():
        if only and not any(name == o or name.startswith(o) for o in only):
            continue
        for sp in speakers:
            r = run(spec, sp)
            for t in r["turns"]:
                t["name"] = name
                res.append(t)
    return res


def show(res, keys=("delivered", "steps", "flags")):
    cur = None
    for r in res:
        if r["name"] != cur:
            cur = r["name"]
            print(f"\n== {cur}")
        if "error" in r:
            print(f"  [{r['speaker']}] t{r['turn']} ERROR {r['error']}")
            continue
        steps = [f"{c}:{s}" for c, s in r["steps"]]
        print(f"  [{r['speaker']}] t{r['turn']} delivered={r['delivered']} steps={steps} "
              f"orig_ok={r['orig_unchanged']} flags={r['flags']}")
        print(f"     > {r['msg']!r}")
        print(f"     {r['reply']}")
        if r.get("final_facts"):
            print(f"     final={r['final_facts']} orig={r['orig_facts']}")
