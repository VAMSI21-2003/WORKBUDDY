"""P1: ordinary scenarios x speakers, head vs base.

    python p1_ordinary.py --tree TREE --writer echo|raise --out out/p1-X.json [scenario-prefix ...]

writer=echo: a careful model (required facts' own sentences). writer=raise: the model can't be
reached, so head must give exactly base's reply (PersonalityVoice/TemplateSpeaker). On base the
writer flag is ignored.
"""
import argparse
import json

import nb

ap = argparse.ArgumentParser()
ap.add_argument("--tree", default="../../..")
ap.add_argument("--writer", default="echo")
ap.add_argument("--out")
ap.add_argument("only", nargs="*")
a = ap.parse_args()
nb.setup(a.tree)
from scenarios import S  # noqa: E402

def _raise(prompt, attempt, conv):
    raise TimeoutError("writer unreachable")

W = {"echo": nb.echo, "raise": _raise}[a.writer]
res = []
for name, spec in S.items():
    if a.only and not any(name.startswith(o) for o in a.only):
        continue
    for sp in ("template", "friend", "focus"):
        r = nb.run(spec, sp, writer=W)
        print(f"\n== {name}")
        nb.show(r)
        for t in r["turns"]:
            t["name"] = name
            t.pop("prompt", None)
            res.append(t)
if a.out:
    json.dump(res, open(a.out, "w"), indent=1, default=str)
bad = [(t["name"], t["speaker"], t["turn"]) for t in res if t.get("orig_unchanged") is False]
print("\nORIGINAL CHANGED:", bad or "none", " turns:", len(res))
