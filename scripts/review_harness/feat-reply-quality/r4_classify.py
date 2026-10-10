"""Round 4: classify the replies in probe output JSON files by what they say about the part of a request that is not the report.

Classes (per turn):  RO   the REPORT_ONLY sentence ("I did nothing beyond the report ...")
                     CD   a "can't do" sentence
                     DONE the lead word "Done"  /  PART the lead word "Partly done"
                     RPT  "Report on ..." is in the reply
Prints a table per file and speaker, and lists turns where a reply has RPT together with CD, or DONE together with RO/CD, or none of RO/CD/DONE.

    python r4_classify.py out/r4-extras-head.json [more.json ...] [--list CLASS]
"""
import collections
import json
import sys

args = sys.argv[1:]
want = None
if "--list" in args:
    i = args.index("--list")
    want = args[i + 1]
    del args[i : i + 2]


def classes(reply: str) -> tuple[str, ...]:
    c = []
    if "I did nothing beyond the report" in reply:
        c.append("RO")
    if "can't do" in reply:
        c.append("CD")
    if reply.startswith("Done") or " Done — " in reply or "Done —" in reply[:40]:
        c.append("DONE")
    if "Partly done" in reply:
        c.append("PART")
    if "Report on " in reply:
        c.append("RPT")
    return tuple(c)


for path in args:
    res = json.load(open(path))
    print(f"\n### {path}: {len(res)} turns")
    tab = collections.defaultdict(collections.Counter)
    odd = []
    for r in res:
        if "reply" not in r:
            continue
        c = classes(r["reply"])
        key = "+".join(c) or "-"
        tab[r["speaker"]][key] += 1
        if ("RPT" in c and "CD" in c) or ("DONE" in c and ("RO" in c or "CD" in c)):
            odd.append((r["speaker"], r["name"], key, r["reply"]))
        if want and want in key.split("+"):
            print(f"[{r['speaker'][:3]}] {r['name']} :: {key}\n     {r['msg']!r}\n     {r['reply']}")
    for sp, cnt in tab.items():
        print(f"  {sp:9s}", dict(sorted(cnt.items(), key=lambda kv: -kv[1])))
    print(f"  turns with Report+can't-do or Done+(RO|CD): {len(odd)}")
    for sp, name, key, reply in odd[:12]:
        if sp == "template":
            print(f"     [{sp[:3]}] {name} :: {key}\n        {reply[:400]}")
