"""Rollback claim (design: the key is left out when there is none). Write with head, open with base.
    JMODE=write python probe_j.py                      # head: writes out/j-data-fail (only a failed turn) and out/j-data-unclear (a not_delivered turn)
    JMODE=open  python probe_j.py --tree $BASE_WORKTREE # base: opens each; prints whether the session is readable
"""
import dataclasses, os, shutil
from fu import *  # noqa
from backend.main import create_app

MODE = os.environ.get("JMODE", "write")
D = HERE / "out"


class Boom:
    def __init__(self, inner): self.inner = inner
    def __call__(self, st): self.inner(st); raise RuntimeError("boom")


if MODE == "write":
    for nm, fn in (("fail", lambda s: s.turn("dd", brain("remove_duplicate_rows", (RD, "dd")))),
                   ("unclear", lambda s: s.turn("hello", brain("unclear"))),
                   ("delivered", lambda s: s.turn("dd", brain("remove_duplicate_rows", (RD, "dd"))))):
        s = Sess("focus", wrap=(lambda c: dataclasses.replace(c, hands=Boom(c.hands))) if nm == "fail" else None)
        fn(s)
        s.app.tmp._finalizer.detach()  # keep the temp data dir: paths inside session.json are absolute
        dst = s.app.settings.data_dir
        (D / f"j-path-{nm}.txt").write_text(str(dst))
        print("wrote", dst, "turn files:", sorted(p.name for p in dst.rglob("t0*.json")), "file_used in record:", any('"file_used"' in p.read_text() for p in dst.rglob("t0*.json")))
        s.app.client.close()
else:
    from backend.settings import Settings
    from backend.store import SessionStore
    for nm in ("fail", "unclear", "delivered"):
        work = Path((D / f"j-path-{nm}.txt").read_text())
        store = SessionStore(Settings(data_dir=work))
        key = next(iter((work / "sessions").iterdir())).name
        try:
            rec = store.load_session(key)
            print(nm, "->", "READABLE" if rec is not None else "NOT FOUND (load_session returned None)", "turns:", len(rec.turns) if rec else None)
        except Exception as e:  # noqa
            print(nm, "->", "EXC", repr(e)[:150])
