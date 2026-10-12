# ruff: noqa: E501
"""Adversarial API scenarios for follow-up turns. python probe_b.py [--tree DIR] [--out FILE]"""
import os, shutil
from fu import *  # noqa

OUT = outpath("b-head.json")
res = {}


def run(name, speaker, fn, **kw):
    s = Sess(speaker, **kw)
    try:
        extra = fn(s)
    except Exception as e:  # noqa
        import traceback; traceback.print_exc()
        s.log.append({"EXC": repr(e)})
        extra = None
    res[f"{name}/{speaker}"] = [short(r) if "EXC" not in r else r for r in s.log] + ([{"extra": extra}] if extra else [])
    s.close()


def first(s):
    return s.turn("remove the duplicate rows", brain("remove_duplicate_rows", (RD, "remove the duplicate rows")))


def delivered_path(s, r):
    folder = s.turn_dir(r["turn_id"])
    return next(iter(sorted(folder.glob("*.xlsx"))))


def b1_tamper(s):  # delivered file changed on disk
    r = first(s)
    p = delivered_path(s, r)
    os.chmod(p, 0o644)
    p.write_bytes(p.read_bytes()[:-5] + b"XXXXX")
    s.turn("now the empty rows", brain("remove_empty_rows", (RE, "remove the empty rows")))
    s.turn("again", brain("remove_empty_rows", (RE, "remove the empty rows")))


def b2_deleted(s):
    r = first(s)
    delivered_path(s, r).unlink()
    s.turn("now the empty rows", brain("remove_empty_rows", (RE, "remove the empty rows")))


def b2b_deleted_nothing_left(s):  # copy gone, request would have been nothing left
    r = first(s)
    delivered_path(s, r).unlink()
    s.turn("dedupe", brain("remove_duplicate_rows", (RD, "remove the duplicate rows")))


def b3_hostile_name(s):
    return None


def b4_filevalues(s):
    first(s)
    for v in ("ORIGINAL", " original ", ["original"], {"x": 1}, True, None, "orig", "latest", "start over", 1):
        s.turn(f"file_to_use={v!r}", brain("remove_empty_rows", (RE, "remove the empty rows"), raw={"file_to_use": v}), label=f"file_to_use={v!r}")


def b5_decline_with_original(s):
    first(s)
    # plan shown & declined, Brain read start over
    s.turn("start over and remove duplicates then empty", brain("multi_step_request", (RD, "remove duplicates"), (RE, "then empty"), file="original"),
           plan((DD, "remove duplicates"), (ER, "then empty")), decision="reject", label="declined plan, Brain=original")
    s.turn("now the empty rows", brain("remove_empty_rows", (RE, "remove the empty rows")))


def b6_misread_original(s):
    first(s)
    s.turn("now the empty rows too", brain("remove_empty_rows", (RE, "remove the empty rows"), file="original"), label="model misreads: original on plain follow-up")
    s.turn("and the duplicates again", brain("remove_duplicate_rows", (RD, "remove the duplicate rows")))


def b7_restart(s):
    first(s)
    from backend.main import create_app
    from backend.settings import Settings
    s.app.client.close()
    app2_client = pr3.TestClient(create_app(s.app.settings, s.app.factory), raise_server_exceptions=False)
    s.app.client = app2_client
    s.turn("now the empty rows (after restart)", brain("remove_empty_rows", (RE, "remove the empty rows")))
    s.turn("dedupe (after restart)", brain("remove_duplicate_rows", (RD, "remove the duplicate rows")))


def b8_formulas(s):
    pass


def b9_report_only_chain_on_copy(s):
    first(s)
    s.turn("report then clean", brain("multi_step_request", (RPT, "give me a report"), (RE, "remove the empty rows")), plan((ER, "remove the empty rows")))


def b10_two_sessions(s):
    first(s)
    s2 = Sess(s.speaker)
    r = s2.turn("remove the empty rows", brain("remove_empty_rows", (RE, "remove the empty rows")))
    res[f"b10-other-session/{s.speaker}"] = [short(r)]
    s2.close()


def b11_unclear_original(s):
    first(s)
    s.turn("use my original file", brain("unclear", file="original"), label="use my original file (unclear+original)")
    s.turn("what is in the file", brain("conversation", file="original"), label="conversation+original")


def b12_pinned_always(s):
    first(s)
    for i in range(2):
        s.turn("now the empty rows", brain("remove_empty_rows", (RE, "remove the empty rows")), file_id=s.orig_id, label="pinned file_id each turn")


def b13_nothingleft_other_unsupported(s):
    first(s)
    s.turn("dedupe and sort by amount", brain("remove_duplicate_rows", (RD, "dedupe"), ("unsupported", "sort by amount")), label="nothing left + unsupported")
    s.turn("dedupe and email report", brain("multi_step_request", (RD, "dedupe"), (RPT, "report"), ("unsupported", "email it to me")), plan((DD, "dedupe")), label="nothing left + report + unsupported")


def b14_collision(s):  # display name " (2)" vs work-copy name
    first(s)  # report_deduplicated.xlsx
    s.turn("start over, dedupe again", brain("remove_duplicate_rows", (RD, "dedupe"), file="original"))  # report_deduplicated (2).xlsx
    s.turn("now empty rows", brain("remove_empty_rows", (RE, "remove the empty rows")))


for sp in ("template", "friend", "focus"):
    for nm, fn in (("b1", b1_tamper), ("b2", b2_deleted), ("b2b", b2b_deleted_nothing_left), ("b4", b4_filevalues), ("b5", b5_decline_with_original),
                   ("b6", b6_misread_original), ("b7", b7_restart), ("b9", b9_report_only_chain_on_copy), ("b10", b10_two_sessions),
                   ("b11", b11_unclear_original), ("b12", b12_pinned_always), ("b13", b13_nothingleft_other_unsupported), ("b14", b14_collision)):
        run(nm, sp, fn)
dump(OUT, res)
print("wrote", OUT, len(res))
