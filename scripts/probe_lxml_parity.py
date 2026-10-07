"""Does the product behave the same whether or not lxml is installed? (values-only probes)

``scripts/review_harness/values_only_probe.py`` and ``round2_probe.py`` build their odd one-sheet
workbooks by matching openpyxl's XML text, which differs when lxml is installed, so they stop on
an assertion there (the same cause as the test fixtures' failures). They are a reviewer's scripts
and aren't edited. This wrapper runs their own fixtures and their own loop unchanged, with one
difference: each fixture is built once, in a subprocess where openpyxl writes as it does without
lxml (``OPENPYXL_LXML=False``), and every run gets a copy of those bytes. The product (real
``build_engine``, skills, Hands, Inspector, Voice, the fake LLM) then runs in this process, with
whatever is installed here: with lxml importable, openpyxl uses it for what the product writes.

    python scripts/probe_lxml_parity.py run values_only|round2 --out FILE
    python scripts/probe_lxml_parity.py compare A.json B.json

``compare`` matches runs by (fixture, request, speaker, session id) and reports every field that
differs: the reply, whether a file was handed back, the steps, the original's hash check, and the
cells and raw scan of the original and of the delivered copy.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HARNESS = Path(__file__).resolve().parent / "review_harness"
FIXTURE_CACHE: dict[str, Path] = {}

BUILD = """
import sys
from pathlib import Path
sys.argv = ["x", "--out", "/dev/null"]
sys.path.insert(0, {harness!r})
import round2_probe
round2_probe.FX[{name!r}](Path({target!r}))
"""


def build_without_lxml(name: str, cache: Path) -> Path:
    target = cache / f"{name}.xlsx"
    env = dict(os.environ, OPENPYXL_LXML="False")
    code = BUILD.format(harness=str(HARNESS), name=name, target=str(target))
    done = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=False
    )
    if done.returncode != 0 or not target.exists():
        raise SystemExit(f"couldn't build fixture {name}:\n{done.stderr[-800:]}")
    return target


def run(which: str, out: Path) -> None:
    import openpyxl

    print(f"openpyxl.LXML in this process: {openpyxl.LXML}", file=sys.stderr)
    cache = Path(tempfile.mkdtemp(prefix="probe-fixtures-"))
    sys.argv = ["probe", "--out", str(out)]
    sys.path.insert(0, str(HARNESS))
    import values_only_probe as v

    if which == "round2":
        import round2_probe as r2  # adds its own fixtures to v.FX

    def cached(name: str):  # noqa: ANN202
        def build(path: Path) -> None:
            if name not in FIXTURE_CACHE:
                FIXTURE_CACHE[name] = build_without_lxml(name, cache)
            shutil.copyfile(FIXTURE_CACHE[name], path)

        return build

    for name in list(v.FX):
        v.FX[name] = cached(name)
    if which == "round2":
        r2.main()
    else:
        results = []
        for fx in list(v.FX):
            for req in v.REQS:
                for speaker in ("template", "friend", "focus"):
                    sids = ["s1", "s2", "s3"] if speaker == "friend" else ["session-1"]
                    for sid in sids:
                        results.append(v.run_one(fx, req, speaker, sid))
        out.write_text(json.dumps(results, indent=1, default=str))
        changed = [r for r in results if r.get("orig_unchanged") is False]
        errors = sum("error" in r for r in results)
        print(f"{len(results)} turns -> {out}; original changed in {len(changed)}; errors {errors}")
    shutil.rmtree(cache, ignore_errors=True)


def key(record: dict) -> tuple:
    return (record["fx"], record["req"], record["speaker"], record["sid"])


def compare(a: Path, b: Path) -> int:
    first = {key(r): r for r in json.loads(a.read_text())}
    second = {key(r): r for r in json.loads(b.read_text())}
    problems = 0
    if first.keys() != second.keys():
        print("different runs:", len(first.keys() ^ second.keys()))
        problems += 1
    fields: dict[str, int] = {}
    for k in sorted(first.keys() & second.keys()):
        for field in sorted(set(first[k]) | set(second[k])):
            if first[k].get(field) != second[k].get(field):
                fields[field] = fields.get(field, 0) + 1
                if fields[field] <= 3:
                    print(f"DIFFERENT {k} {field}:")
                    print(f"  A: {first[k].get(field)!r:.300}")
                    print(f"  B: {second[k].get(field)!r:.300}")
    delivered = sum(1 for r in first.values() if r.get("delivered"))
    errors = sum(1 for r in first.values() if "error" in r)
    print(
        f"{len(first.keys() & second.keys())} runs compared (A: {delivered} delivered a file, "
        f"{errors} errors), fields that differ: {fields or 'none'}"
    )
    return 1 if fields or problems else 0


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "run" and sys.argv[3] == "--out":
        run(sys.argv[2], Path(sys.argv[4]))
    elif len(sys.argv) == 4 and sys.argv[1] == "compare":
        sys.exit(compare(Path(sys.argv[2]), Path(sys.argv[3])))
    else:
        sys.exit(__doc__)
