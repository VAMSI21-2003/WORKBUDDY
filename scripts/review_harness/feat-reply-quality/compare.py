"""Compare two runs of an existing review harness: which replies changed, and why.

    python scripts/review_harness/feat-reply-quality/compare.py base.json new.json [--show N]

Both files are the JSON a harness in ``scripts/review_harness/`` writes (``--out``): one record per
turn with ``name``, ``speaker``, ``sid`` (or ``fixture``/``request``), ``reply``, ``delivered``,
``orig_unchanged``, ``voice_calls``. Records are matched on every key but the reply. A changed reply
is put in the first class that explains it:

- ``same``: byte for byte;
- ``plural``: equal once "row(s)"-style nouns are counted, "with" is added to "The copy has N rows,
  with …", and the exact-duplicate finding is removed;
- ``plural + exact-duplicate finding / flow``: equal to the base reply once the new exact-duplicate
  finding, the reworded leads ("I also noticed", "Here's what I noticed when I looked it over"), the
  "want me to tackle" question, the all-clear and "before I could change anything" are taken out of both;
- the rest, by the feature that explains them (new reason, report, greeting, question, ...), or
  ``other`` (read these by hand).

It also checks, on the new run: no reply contains "(s)", "verifier" or a raw capability id; every
turn made 0 voice-model calls; the original was unchanged; and whether a file was handed back
agrees with the base run. The last is printed, not asserted: a change there needs an explanation.
"""
import argparse
import json
import re
import sys
from collections import Counter

_ap = argparse.ArgumentParser()
_ap.add_argument("base")
_ap.add_argument("new")
_ap.add_argument("--show", type=int, default=3, help="examples to print per class")
ARGS = _ap.parse_args()


def key(rec):
    return tuple((k, str(v)) for k, v in sorted(rec.items()) if k in (
        "name", "fx", "speaker", "sid", "fixture", "request", "req", "mode"))


def norm_base(text):
    """The base reply, as the new wording would write the same facts."""
    def plural(m):
        n = int(m.group(1))
        return f"{n} {m.group(2)}{'' if n == 1 else 's'}"
    text = re.sub(r"(\d+) ((?:[a-z-]+ )?(?:row|cell|step|file|value|format|thing))\(s\)", plural, text)
    text = re.sub(r"(The copy has \d+ rows?), (\d+ )", r"\1, with \2", text)
    text = text.replace("Before anything else, here's what I noticed:", "Here's what I noticed when I looked it over:")
    return text


_DUP_FINDING = re.compile(r"\d+ rows? repeats? an earlier row exactly;? ?")
_NOISE = [
    re.compile(r"I also noticed:"),
    re.compile(r"Before anything else, here's what I noticed:"),
    re.compile(r"Here's what I noticed when I looked it over:"),
    re.compile(r"Want me to tackle any of these\?"),
    re.compile(r"[Nn]one of the (?:other )?issues I check for were found\.?"),
    re.compile(r"I ran into a problem (?:before I could change anything|partway through)\."),
]


def skeleton(text):
    """The reply without the pieces this change adds or rewords, to see whether anything else moved."""
    text = _DUP_FINDING.sub("", text)
    for pattern in _NOISE:
        text = pattern.sub("", text)
    return re.sub(r"[\s;.]+", " ", text).strip()


FEATURES = [
    ("report", "Report on "),
    ("no-file", "I don't have a file to work on yet"),
    ("refusal", "I can't work on this file"),
    ("not-understood", "I didn't read that as something I can do"),
    ("failed-reading", "I couldn't work out what you asked"),
    ("greeting", "I can remove duplicate rows, remove empty rows, and"),
    ("cant-do-that", "I can't do that yet"),
]


def classify(old, new):
    if old == new:
        return "same"
    if new == norm_base(old):
        return "plural"
    if skeleton(new) == skeleton(norm_base(old)):
        return "plural + exact-duplicate finding / flow"
    hits = [name for name, marker in FEATURES if marker in new and marker not in old]
    return "+".join(hits) if hits else "other"


base = {key(r): r for r in json.load(open(ARGS.base)) if "error" not in r}
new_all = json.load(open(ARGS.new))
new = {key(r): r for r in new_all if "error" not in r}
errors = [r for r in new_all if "error" in r]
classes = Counter()
examples = {}
problems = []
for k, n in new.items():
    b = base.get(k)
    if b is None:
        problems.append(("not in base", dict(k)))
        continue
    c = classify(b.get("reply") or "", n.get("reply") or "")
    classes[c] += 1
    examples.setdefault(c, []).append((k, b, n))
    reply = n.get("reply") or ""
    if "(s)" in reply or "verifier" in reply.lower() or re.search(r"\bspreadsheet\.[a-z_]+\b", reply):
        problems.append(("forbidden text", dict(k), reply[:160]))
    if (n.get("voice_calls") or 0) != 0:
        problems.append(("voice-model call", dict(k)))
    if n.get("orig_unchanged") is False:
        problems.append(("ORIGINAL CHANGED", dict(k)))
    if b.get("delivered") != n.get("delivered"):
        problems.append(("delivery differs from base", dict(k), b.get("delivered"), n.get("delivered")))

print(f"{ARGS.new}: {len(new)} turns, {len(errors)} errors in new run, base had {len(base)} turns")
for c, count in classes.most_common():
    print(f"  {count:5d}  {c}")
print(f"problems: {len(problems)}")
for p in problems[:20]:
    print("   ", p)
if ARGS.show:
    for c, items in examples.items():
        if c in ("same", "plural"):
            continue
        print(f"\n=== {c} ({len(items)})")
        for k, b, n in items[: ARGS.show]:
            print("  key:", dict(k))
            print("  base:", (b.get("reply") or "")[:400])
            print("  new: ", (n.get("reply") or "")[:400])
sys.exit(0)
