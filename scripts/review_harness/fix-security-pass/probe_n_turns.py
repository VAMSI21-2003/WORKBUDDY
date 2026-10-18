"""Round 3: whole turns (real pipeline, fake LLM) on out/m_*.xlsx (probe_m_round3.py writes them).

Each (file, speaker, turn kind) runs in its own child process, capped at --cap-mib of address space
and --timeout seconds, so a bomb can't take the machine and the peak RSS is that turn's. Prints
delivered or not, the reply, the original's SHA-256 before and after, peak RSS and wall time.

    OPENPYXL_LXML=False python scripts/review_harness/fix-security-pass/probe_n_turns.py M2_broken_link_bomb [...] \
        [--tree DIR] [--speakers template,friend,focus] [--kinds dedupe,report] [--timeout 600]
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

CHILD = r"""
import resource, shutil, sys, time
cap = int(sys.argv[5]) * 1024 * 1024
resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
src, speaker, kind = sys.argv[1], sys.argv[2], sys.argv[3]
sys.argv = [sys.argv[0], "--tree", sys.argv[4]]
sys.path.insert(0, sys.argv[0].rsplit("/", 1)[0])
import r5lib
A = r5lib.args()
rq = r5lib.start(A)
from rq import brain
TURNS = {
    "dedupe": dict(msg="remove the duplicate rows",
                   b=brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows"))),
    "report": dict(msg="give me a report on this file",
                   b=brain("report_on_file", ("report_on_file", "give me a report on this file"))),
    "empty": dict(msg="remove the empty rows",
                  b=brain("remove_empty_rows", ("remove_empty_rows", "remove the empty rows"))),
}
start = time.time()
spec = dict(file=lambda p: shutil.copyfile(src, p), turns=[TURNS[kind]])
rec = rq.run(spec, speaker)["turns"][0]
peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
print(f"{time.time() - start:.0f}s peak_rss={peak:.0f}MiB delivered={rec.get('delivered')} "
      f"orig_unchanged={rec.get('orig_unchanged')} steps={rec.get('steps')} flags={rec.get('flags')}")
print("   reply:", (rec.get("reply") or rec.get("error") or "").replace("\n", " | "))
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="+")
    ap.add_argument("--tree", default=str(HERE.parents[2]))
    ap.add_argument("--speakers", default="template")
    ap.add_argument("--kinds", default="dedupe,report")
    ap.add_argument("--cap-mib", type=int, default=4096)
    ap.add_argument("--timeout", type=int, default=600)
    a = ap.parse_args()
    script = HERE.parent / "feat-reply-quality" / "_n_child.py"
    child = HERE / "out" / "_n_child.py"
    child.write_text(CHILD)
    env = dict(os.environ, OPENPYXL_LXML="False",
               PYTHONPATH=str(HERE.parent / "feat-reply-quality"))
    del script
    for name in a.names:
        src = HERE / "out" / f"m_{name}.xlsx"
        for speaker in a.speakers.split(","):
            for kind in a.kinds.split(","):
                head = f"== {name} {speaker} {kind}:"
                try:
                    r = subprocess.run([sys.executable, str(child), str(src), speaker, kind, a.tree,
                                        str(a.cap_mib)], capture_output=True, text=True, env=env,
                                       timeout=a.timeout)
                    body = "\n".join(line for line in r.stdout.splitlines()
                                     if line.startswith(("   reply", )) or "peak_rss" in line)
                    print(head, body or ("CHILD FAILED: " + r.stderr.strip()[-400:]), flush=True)
                except subprocess.TimeoutExpired:
                    print(head, f"still running at {a.timeout}s (killed)", flush=True)


if __name__ == "__main__":
    main()
