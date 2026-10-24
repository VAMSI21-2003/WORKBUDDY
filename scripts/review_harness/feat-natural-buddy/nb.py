"""Reviewer's harness for feat/natural-buddy (round 1).

Real ``build_engine`` (real skills, planner, Inspector, Voice) and, on the branch, the real
``NaturalVoice`` + ``build_fact_sheet`` + ``check_reply``. Only the LLM is faked: Brain, Think and
the planner get fixed JSON (``rq.Fake``), and the writer model is a Python callable given the
prompt, so a probe can play a careful model, a careless one, or one that obeys an injection.

    import nb
    nb.setup(tree)                      # a checkout: this one, or a worktree of origin/main
    rec = nb.run(spec, "friend", writer=nb.const({"messages": [...], "facts_used": [...]}))

``speaker``: "template" (no mode; TemplateSpeaker on base, NaturalVoice with no mode on head),
"friend", "focus". On base, friend/focus use PersonalityVoice (what base production runs).
Each record holds the reply, the messages, whether the writer's draft was shown or the template
fallback, the steps, the delivered file's independent facts (openpyxl), and the original's
SHA-256 before and after.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "feat-reply-quality"))
import rq  # noqa: E402  (reviewer library of an earlier branch, imported, not edited)

T: dict = {}
brain, think, plan, DD, ER, AN = rq.brain, rq.think, rq.plan, rq.DD, rq.ER, rq.AN


def setup(tree) -> None:
    rq.setup(tree)
    T.update(rq.T)
    try:
        from buddy import claims, facts, writer

        T.update(writer=writer, facts=facts, claims=claims, head=True)
    except ImportError:
        T["head"] = False


def const(*answers):
    """A writer that gives these answers in order (dict -> JSON), repeating the last."""
    answers = list(answers)

    def w(prompt, attempt, conv):
        a = answers[min(attempt - 1, len(answers) - 1)]
        if callable(a):
            a = a(prompt)
        if isinstance(a, BaseException):
            raise a
        return a if isinstance(a, str) else json.dumps(a)

    return w


def echo(prompt, attempt, conv):
    """A careful writer: the required facts' own sentences, cited (tests/buddy/writer_fakes)."""
    sys.path.insert(0, T["tree"])
    from tests.buddy.writer_fakes import echo_answer

    return echo_answer(prompt)


def fact_ids(prompt):
    import re

    # round 1-2 prompt: "- [F1] ..."; round 3: "- F1 (MUST SHOW, ...):"
    return re.findall(r"^- \[(F\d+)\]", prompt, re.M) or re.findall(r"^- (F\d+) \(", prompt, re.M)


class Router(rq.Fake):
    """rq.Fake, but the writer's prompt goes to ``writer``."""

    def __init__(self, b, t, p, writer):
        super().__init__(b, t, p)
        self.writer = writer
        self.writer_calls = []

    def complete(self, *, system, messages, max_tokens=1024):
        if "HOW TO ANSWER" in system:
            attempt = sum(1 for m in messages if m.role == "assistant") + 1
            self.writer_calls.append(messages[0].content)
            if self.writer is None:
                raise RuntimeError("no writer")
            return self.writer(messages[0].content, attempt, messages)
        return super().complete(system=system, messages=messages, max_tokens=max_tokens)


def run(spec: dict, speaker: str, writer=echo, session="session-1") -> dict:
    pd, build_engine, BuddyState, PV = T["pd"], T["build_engine"], T["BuddyState"], T["PersonalityVoice"]
    d = Path(tempfile.mkdtemp())
    cwd = os.getcwd()
    os.chdir(d)
    try:
        st = BuddyState.new(session_id=session, workspace_id="w")
        up = d / "uploads" / spec.get("name", "report.xlsx")
        if spec.get("file"):
            rq.make_file(spec["file"], up)
            st.context.add_uploaded_file(f"uploads/{up.name}")
            st.context.switch_active_file(f"uploads/{up.name}")
        h0 = rq.sha(up)
        f0 = rq.facts(up) if spec.get("file") else None
        recs, deliveries = [], 0
        for i, turn in enumerate(spec["turns"]):
            if i:
                st.reset_transient()
            st.user_message = turn["msg"]
            st.earlier_deliveries = deliveries
            if "state" in turn:
                pass
            p = Router(turn.get("b", brain("unclear")), turn.get("t", think()), turn.get("p"),
                       turn.get("writer", writer))
            if speaker != "template":
                st.context.set_mode(speaker)
            used = {"fallback": False, "sheet": None}
            if T["head"]:
                W = T["writer"]
                inner = PV()

                def fb(req, inner=inner, used=used):
                    used["fallback"] = True
                    return inner(req)

                sp = W.NaturalVoice(p, fallback=fb)
                orig_build = W.build_fact_sheet

                def spy(req, used=used, orig_build=orig_build):
                    s = orig_build(req)
                    used["sheet"] = s
                    return s

                patch_sheet = _patched(W, "build_fact_sheet", spy)
            else:
                sp = PV(p) if speaker != "template" else None
                patch_sheet = contextlib.nullcontext()
            cm = spec.get("patch") or contextlib.nullcontext
            try:
                with cm(), patch_sheet:
                    build_engine(llm_provider=p, speaker=sp).run(st)
            except Exception as e:  # noqa: BLE001
                recs.append(dict(speaker=speaker, turn=i, msg=turn["msg"], error=repr(e)[:300]))
                continue
            er = st.execution_result
            out = list(er.outputs) if er else []
            delivered = bool(out)
            deliveries += delivered
            sheet = used["sheet"]
            rec = dict(
                speaker=speaker, turn=i, msg=turn["msg"], reply=st.reply,
                messages=list(getattr(st, "reply_messages", ()) or ()),
                writer_calls=len(p.writer_calls), fallback=used["fallback"],
                writer_shown=bool(p.writer_calls) and not used["fallback"],
                sheet_turn=(sheet.turn if sheet else None),
                sheet_facts=[(f.id, f.kind, f.required, f.text, getattr(f, "file_text", getattr(f, "from_file", None)),
                              list(getattr(f, "variants", ()))) for f in sheet.facts] if sheet else None,
                delivered=delivered,
                steps=[(s.capability.split(".")[-1], s.status) for s in (er.steps if er else ())],
                failure=(st.failure.stage if st.failure else None),
                orig_unchanged=(rq.sha(up) == h0) if spec.get("file") else None,
                orig_facts=f0, final_facts=rq.facts(out[0]) if out else None,
                prompt=(p.writer_calls[0] if p.writer_calls else None),
            )
            recs.append(rec)
        return dict(speaker=speaker, turns=recs)
    finally:
        os.chdir(cwd)


@contextlib.contextmanager
def _patched(mod, name, value):
    old = getattr(mod, name)
    setattr(mod, name, value)
    try:
        yield
    finally:
        setattr(mod, name, old)


def show(rec, prompt=False):
    for t in rec["turns"]:
        if "error" in t:
            print(f"  [{rec['speaker']}] t{t['turn']} ERROR {t['error']}")
            continue
        print(f"  [{rec['speaker']}] t{t['turn']} turn={t['sheet_turn']} delivered={t['delivered']} "
              f"steps={t['steps']} writer_calls={t['writer_calls']} shown={t['writer_shown']} "
              f"orig_ok={t['orig_unchanged']} final={t['final_facts']}")
        print(f"     > {t['msg']!r}")
        for m in t["messages"] or [t["reply"]]:
            print(f"     | {m}")
        if prompt and t["prompt"]:
            print("     PROMPT:\n" + "\n".join("       " + x for x in t["prompt"].splitlines()))
