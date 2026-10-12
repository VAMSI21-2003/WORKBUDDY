# ruff: noqa: E501
"""Core follow-up scenarios through the HTTP API, 2 speakers the backend selects (friend, focus) plus template.
    python probe_a.py [--tree DIR] [--out FILE]
"""
from fu import *  # noqa

OUT = outpath("a-head.json")
res = {}


def run(name, speaker, fn, **kw):
    s = Sess(speaker, **kw)
    try:
        fn(s)
    except Exception as e:  # noqa
        s.log.append({"EXC": repr(e)})
    res[f"{name}/{speaker}"] = [short(r) if "EXC" not in r else r for r in s.log]
    s.close()


def s1(s):  # dedupe -> empty rows -> dedupe again (nothing left) -> empties again
    s.turn("remove the duplicate rows", brain("remove_duplicate_rows", (RD, "remove the duplicate rows")))
    s.turn("now remove the empty rows too", brain("remove_empty_rows", (RE, "remove the empty rows")))
    s.turn("remove the duplicate rows again", brain("remove_duplicate_rows", (RD, "remove the duplicate rows")))
    s.turn("and the empty rows again", brain("remove_empty_rows", (RE, "remove the empty rows")))


def s2(s):  # start over
    s.turn("remove the duplicate rows", brain("remove_duplicate_rows", (RD, "remove the duplicate rows")))
    s.turn("start over and remove the empty rows", brain("remove_empty_rows", (RE, "remove the empty rows"), file="original"))
    s.turn("and the duplicates", brain("remove_duplicate_rows", (RD, "remove the duplicates")))


def s3(s):  # "use my original" alone, delivers nothing; then a request
    s.turn("remove the duplicate rows", brain("remove_duplicate_rows", (RD, "remove the duplicate rows")))
    s.turn("use my original file", brain("unclear", file="original"))
    s.turn("remove the empty rows", brain("remove_empty_rows", (RE, "remove the empty rows")))


def s4(s):  # chain then a repeat
    s.turn("remove duplicates and then the empty rows", brain("multi_step_request", (RD, "remove duplicates"), (RE, "the empty rows")),
           plan((DD, "remove duplicates"), (ER, "the empty rows")))
    s.turn("do both again", brain("multi_step_request", (RD, "do both"), (RE, "again")), plan((DD, "do both"), (ER, "again")))
    s.turn("remove duplicates", brain("remove_duplicate_rows", (RD, "remove duplicates")))


def s5(s):  # report on copy / report + nothing left
    s.turn("remove the duplicate rows", brain("remove_duplicate_rows", (RD, "remove the duplicate rows")))
    s.turn("give me a report", brain(RPT, (RPT, "give me a report")))
    s.turn("remove the duplicate rows and give me a report", brain("multi_step_request", (RD, "remove the duplicate rows"), (RPT, "give me a report")),
           plan((DD, "remove the duplicate rows")))
    s.turn("remove the empty rows and give me a report", brain("multi_step_request", (RE, "remove the empty rows"), (RPT, "give me a report")),
           plan((ER, "remove the empty rows")))


def s6(s):  # many follow-ups: names growth
    for i in range(7):
        if i % 2 == 0:
            s.turn(f"dedupe {i}", brain("remove_duplicate_rows", (RD, "dedupe")))
        else:
            s.turn(f"empty {i}", brain("remove_empty_rows", (RE, "empty")))


def s7(s):  # second upload and picking
    s.turn("remove the duplicate rows", brain("remove_duplicate_rows", (RD, "remove the duplicate rows")))
    fid2, _ = s.upload(h.xlsx(), "second.xlsx")
    s.turn("remove the duplicate rows (2nd upload)", brain("remove_duplicate_rows", (RD, "remove the duplicate rows")))
    s.turn("empty rows on second", brain("remove_empty_rows", (RE, "remove the empty rows")))
    s.turn("pick the first upload", brain("remove_empty_rows", (RE, "remove the empty rows")), file_id=s.orig_id)
    s.turn("no pick: back on second?", brain("remove_empty_rows", (RE, "remove the empty rows")))


def s8(s):  # greeting / failing turn between
    s.turn("remove the duplicate rows", brain("remove_duplicate_rows", (RD, "remove the duplicate rows")))
    s.turn("hello", brain("greeting"))
    s.turn("garbage brain", "not json at all")
    s.turn("now the empty rows", brain("remove_empty_rows", (RE, "remove the empty rows")))


for sp in ("template", "friend", "focus"):
    for nm, fn in (("s1", s1), ("s2", s2), ("s3", s3), ("s4", s4), ("s5", s5), ("s6", s6), ("s7", s7), ("s8", s8)):
        run(nm, sp, fn)
dump(OUT, res)
print("wrote", OUT, len(res))
