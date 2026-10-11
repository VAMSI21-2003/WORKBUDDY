# ruff: noqa: E501
"""Mixed chains on a copy: one step has nothing left, the other does; report variants. python probe_e.py"""
from fu import *  # noqa
OUT = outpath("e-head.json")
res = {}


def run(name, speaker, fn):
    s = Sess(speaker)
    try:
        fn(s)
    except Exception as e:  # noqa
        import traceback; traceback.print_exc(); s.log.append({"EXC": repr(e)})
    res[f"{name}/{speaker}"] = [short(r) if "EXC" not in r else r for r in s.log]
    s.close()


def m1(s):  # empty rows first (dups remain), then chain dedupe+empty: dedupe removes 2, empty removes 0
    s.turn("remove the empty rows", brain("remove_empty_rows", (RE, "remove the empty rows")))
    s.turn("dedupe and empty rows", brain("multi_step_request", (RD, "dedupe"), (RE, "empty rows")), plan((DD, "dedupe"), (ER, "empty rows")))


def m2(s):  # dedupe first (empty row remains), then chain dedupe+empty: dedupe 0, empty 1
    s.turn("remove the duplicate rows", brain("remove_duplicate_rows", (RD, "remove the duplicate rows")))
    s.turn("dedupe and empty rows", brain("multi_step_request", (RD, "dedupe"), (RE, "empty rows")), plan((DD, "dedupe"), (ER, "empty rows")))


def m3(s):  # reverse order chain on copy
    s.turn("remove the duplicate rows", brain("remove_duplicate_rows", (RD, "remove the duplicate rows")))
    s.turn("empty rows then dedupe", brain("multi_step_request", (RE, "empty rows"), (RD, "dedupe")), plan((ER, "empty rows"), (DD, "dedupe")))


def m4(s):  # unsupported step in a chain on a copy: must not start
    s.turn("remove the duplicate rows", brain("remove_duplicate_rows", (RD, "remove the duplicate rows")))
    s.turn("empty rows then sort", brain("multi_step_request", (RE, "empty rows"), ("unsupported", "sort by amount")), plan((ER, "empty rows"), ("spreadsheet.sort", "sort by amount")))
    s.turn("next", brain("remove_empty_rows", (RE, "empty rows")))


def m5(s):  # report only after start over
    s.turn("remove the duplicate rows", brain("remove_duplicate_rows", (RD, "remove the duplicate rows")))
    s.turn("give me a report on my original", brain(RPT, (RPT, "give me a report"), file="original"))
    s.turn("next: empty rows", brain("remove_empty_rows", (RE, "empty rows")))


for sp in ("template", "friend", "focus"):
    for nm, fn in (("m1", m1), ("m2", m2), ("m3", m3), ("m4", m4), ("m5", m5)):
        run(nm, sp, fn)
dump(OUT, res)
