"""Round 2: compare two probe outputs (JSON from rq.run_all) turn by turn; print every reply that differs.

    python r2_diff.py out/a-head.json out/r2-a-head.json   # round-1 head vs round-2 head
"""
import json
import sys

a = json.load(open(sys.argv[1]))
b = json.load(open(sys.argv[2]))
key = lambda r: (r["name"], r["speaker"], r["turn"])
A = {key(r): r for r in a}
B = {key(r): r for r in b}
only_a = [k for k in A if k not in B]
only_b = [k for k in B if k not in A]
diff = [k for k in A if k in B and (A[k].get("reply"), A[k].get("delivered"), A[k].get("steps")) != (B[k].get("reply"), B[k].get("delivered"), B[k].get("steps"))]
print(f"{len(A)} vs {len(B)} turns; only in first: {len(only_a)}, only in second: {len(only_b)}, differ: {len(diff)}")
show_speakers = sys.argv[3].split(",") if len(sys.argv) > 3 else ["template"]
for k in diff:
    if k[1] not in show_speakers:
        continue
    print("\n==", k, "msg:", A[k]["msg"][:120])
    print("  delivered", A[k].get("delivered"), "->", B[k].get("delivered"), "| steps", A[k].get("steps") == B[k].get("steps"))
    print("  OLD:", A[k].get("reply"))
    print("  NEW:", B[k].get("reply"))
