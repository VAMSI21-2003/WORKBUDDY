"""Independent comparison of two runs of a scripts/review_harness harness (base vs head).

    python cmp_harness.py BASE.json HEAD.json [--show N] [--only-new-claims]

For each turn (name, speaker, sid): normalises plurals "(s)" on the base reply, and reports
 - turns whose handed-back status differs,
 - originals changed, voice calls, forbidden text (new reply), words of completion claim in an undelivered reply,
 - for turns where delivered differs or reply changed beyond plural/flow: a short diff list.
"""
import json, re, sys, collections
args = [a for a in sys.argv[1:] if not a.startswith("--")]
show = 6
if "--show" in sys.argv:
    show = int(sys.argv[sys.argv.index("--show") + 1]); args = [a for a in args if a != str(show)]
def key(r):
    return (r.get("name") or f"{r.get('fx')}|{r.get('req')}", r["speaker"], r.get("sid"))
base = {key(r): r for r in json.load(open(args[0]))}
head = {key(r): r for r in json.load(open(args[1]))}
CLAIM = re.compile(r"\b(done|complet(e|ed|ely|ion)|finish(ed)?|succeed(ed)?|success(ful|fully)?|removed|deleted|clean(ed)?|cleared|dedup(ed|licated)?|saved|fixed|ready|handled|taken care)\b", re.I)
def norm(s):
    s = s or ""
    s = re.sub(r"(\d+) ([\w\- ]+?)\(s\)", lambda m: m.group(0), s)
    s = s.replace("(s)", "")
    s = re.sub(r"\b(row|rows|value|values|format|formats|file|files|step|steps|cell|cells)\b", "X", s)
    return s
cnt = collections.Counter(); ex = collections.defaultdict(list)
for k, h in head.items():
    b = base.get(k)
    if b is None:
        cnt["no-base"] += 1; continue
    if "error" in h or "error" in b:
        cnt["error"] += 1; ex["error"].append((k, h.get("error"), b.get("error"))); continue
    if h["delivered"] != b["delivered"]:
        cnt["delivered-differs"] += 1; ex["delivered-differs"].append((k, b["delivered"], h["delivered"], h["reply"][:400]))
    if h.get("orig_unchanged") is False: cnt["ORIG-CHANGED"] += 1; ex["ORIG-CHANGED"].append(k)
    if h.get("voice_calls"): cnt["voice-calls"] += 1
    r = h["reply"] or ""
    for pat, tag in (("(s)", "paren-s"), ("verifier", "verifier"), ("spreadsheet.", "capid"), ("ZQX", "voice-text")):
        if pat in r: cnt[tag] += 1; ex[tag].append((k, r[:200]))
    if not h["delivered"] and CLAIM.search(r):
        cnt["claim-words-undelivered"] += 1; ex["claim-words-undelivered"].append((k, CLAIM.search(r).group(0), r[:300]))
    if h["reply"] == b["reply"]: cnt["identical"] += 1
    elif norm(h["reply"]) == norm(b["reply"]): cnt["plural-only"] += 1
    else:
        cnt["other-change"] += 1; ex["other-change"].append((k, b["reply"], h["reply"]))
print(dict(cnt))
for tag in ("delivered-differs", "ORIG-CHANGED", "error", "paren-s", "verifier", "capid", "voice-text", "claim-words-undelivered"):
    for e in ex[tag][:show]: print("  ", tag, e)
# summarise other-change by (first differing sentence) category
cat = collections.Counter()
for k, b, h in ex["other-change"]:
    bs = set(re.split(r"(?<=[.?!]) ", norm(b))); hs = set(re.split(r"(?<=[.?!]) ", norm(h)))
    added = [s for s in re.split(r"(?<=[.?!]) ", h) if norm(s) not in bs]
    removed = [s for s in re.split(r"(?<=[.?!]) ", b) if norm(s) not in hs]
    cat[(tuple(sorted(norm(s)[:60] for s in removed)), tuple(sorted(norm(s)[:60] for s in added)))] += 1
print("distinct other-change (removed -> added) groups:", len(cat))
for (rm, ad), n in cat.most_common(40):
    print(f"  {n:4d}  - {list(rm)}\n        + {list(ad)}")
