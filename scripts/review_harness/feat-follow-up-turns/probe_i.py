# ruff: noqa: E501
"""Round 2: the start-over change (latest_copy ignores start_over), restart, hand-edited old records, mid-turn failure.
    python probe_i.py [--tree DIR] [--out FILE]
Prints one block per scenario: each turn's outcome, delivered names, next_request_file, replies, original unchanged.
"""
import dataclasses, glob, json
from fu import *  # noqa
from backend.main import create_app

OUT = outpath("i-head.json")
res = {}
FAIL = {"on": False}


class Flaky:
    def __init__(self, inner):
        self.inner = inner

    def __call__(self, st):
        st = self.inner(st)
        if FAIL["on"]:
            raise RuntimeError("boom secret cell 4111 /srv/x")
        return st


def wrap(cycle):
    return dataclasses.replace(cycle, hands=Flaky(cycle.hands))


def restart(s):
    s.app.client.close()
    s.app.client = pr3.TestClient(create_app(s.app.settings, s.app.factory), raise_server_exceptions=False)


def turns_files(s):
    return sorted(glob.glob(str(s.app.settings.sessions_dir / "*" / "turns" / "*.json")))


def run(name, speaker, fn, **kw):
    s = Sess(speaker, **kw)
    try:
        fn(s)
    except Exception as e:  # noqa
        import traceback; traceback.print_exc()
        s.log.append({"EXC": repr(e)})
    res[f"{name}/{speaker}"] = [short(r) if "EXC" not in r else r for r in s.log]
    s.close()


def first(s):
    s.turn("remove the duplicate rows", brain(RD, (RD, "remove the duplicate rows")) if False else brain("remove_duplicate_rows", (RD, "remove the duplicate rows")))


def i1_start_over_delivers_then_restart(s):
    first(s)
    s.turn("start over, empty rows", brain("remove_empty_rows", (RE, "remove the empty rows"), file="original"))
    restart(s)
    s.turn("then dedupe (after restart; should build on start-over result)", brain("remove_duplicate_rows", (RD, "remove the duplicate rows")))


def i2_start_over_fails_midway(s):
    first(s)
    FAIL["on"] = True
    s.turn("start over, empty rows (hands raises)", brain("remove_empty_rows", (RE, "remove the empty rows"), file="original"))
    FAIL["on"] = False
    s.turn("now the empty rows (should build on the first copy)", brain("remove_empty_rows", (RE, "remove the empty rows")))


def i3_old_records(s):
    # hand-write round-1-format records: file_used null on a not_delivered turn; start_over true on a not_delivered turn
    first(s)
    s.turn("use my original file", brain("unclear", file="original"))
    for f in turns_files(s):
        d = json.load(open(f))
        if d["outcome"] == "not_delivered":
            d["file_used"] = None
        json.dump(d, open(f, "w"))
    restart(s)
    s.turn("empty rows (file_used null on disk)", brain("remove_empty_rows", (RE, "remove the empty rows")))
    for f in turns_files(s):
        d = json.load(open(f))
        if d["outcome"] == "not_delivered":
            d["file_used"] = {"kind": "upload", "file_id": s.orig_id, "start_over": True}
            json.dump(d, open(f, "w"))
    s.turn("use my original again", brain("unclear", file="original"))
    for f in turns_files(s):
        d = json.load(open(f))
        if d["outcome"] == "not_delivered":
            d["file_used"] = {"kind": "upload", "file_id": s.orig_id, "start_over": True}
            json.dump(d, open(f, "w"))
    restart(s)
    st = s.state()
    s.log.append({"label": "state after hand-edited start_over=true on not_delivered", "outcome": "n/a", "reply": f"status_ok={'turns' in st} next={st.get('next_request_file')}", "delivered_names": [], "final_describe": [], "next_request_file": st.get("next_request_file"), "uploads_unchanged": True, "voice_calls": 0})
    s.turn("dedupe (should build on latest copy)", brain("remove_duplicate_rows", (RD, "remove the duplicate rows")))


def i4_start_over_with_report(s):
    first(s)
    s.turn("start over, dedupe and give me a report", brain("multi_step_request", (RD, "dedupe"), (RPT, "give me a report"), file="original"), plan((DD, "dedupe")))
    s.turn("now the empty rows", brain("remove_empty_rows", (RE, "remove the empty rows")))


def i5_start_over_unsupported_chain(s):
    first(s)
    s.turn("start over, dedupe and sort", brain("multi_step_request", (RD, "dedupe"), ("unsupported", "sort by amount"), file="original"), plan((DD, "dedupe")))
    s.turn("now the empty rows", brain("remove_empty_rows", (RE, "remove the empty rows")))


def i6_two_start_overs_and_alone(s):
    first(s)
    s.turn("start over, dedupe", brain("remove_duplicate_rows", (RD, "dedupe"), file="original"))
    s.turn("use my original file", brain("unclear", file="original"))
    s.turn("empty rows (should build on 2nd delivery)", brain("remove_empty_rows", (RE, "remove the empty rows")))
    s.turn("start over, empty rows only", brain("remove_empty_rows", (RE, "remove the empty rows"), file="original"))
    s.turn("dedupe (should build on 4th)", brain("remove_duplicate_rows", (RD, "remove the duplicate rows")))


def i7_second_upload(s):
    first(s)
    fid, _ = s.upload(h.xlsx(h.DUP_ROWS), "second.xlsx")
    s.turn("dedupe second upload (start over flagged)", brain("remove_duplicate_rows", (RD, "dedupe"), file="original"), file_id=fid)
    s.turn("empty rows on active (second)", brain("remove_empty_rows", (RE, "remove the empty rows")))
    s.turn("empty rows on first upload", brain("remove_empty_rows", (RE, "remove the empty rows")), file_id=s.orig_id)


def i8_all_nothing_left_start_over(s):
    first(s)
    s.turn("remove the empty rows", brain("remove_empty_rows", (RE, "remove the empty rows")))
    s.turn("remove the empty rows again (nothing left)", brain("remove_empty_rows", (RE, "remove the empty rows")))
    s.turn("start over, empty rows only", brain("remove_empty_rows", (RE, "remove the empty rows"), file="original"))


for sp in ("template", "friend", "focus"):
    for nm, fn in (("i1", i1_start_over_delivers_then_restart), ("i2", i2_start_over_fails_midway), ("i3", i3_old_records),
                   ("i4", i4_start_over_with_report), ("i5", i5_start_over_unsupported_chain), ("i6", i6_two_start_overs_and_alone),
                   ("i7", i7_second_upload), ("i8", i8_all_nothing_left_start_over)):
        run(nm, sp, fn, **({"wrap": wrap} if nm == "i2" else {}))
dump(OUT, res)
print("wrote", OUT, len(res))
