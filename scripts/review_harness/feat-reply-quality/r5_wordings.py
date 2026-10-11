"""Round 5: the wordings that broke rounds 1 to 3 (a report phrase that holds a work word, a guard word, a negation,
a purpose, a column name ...), handed to the pipeline as Brain's `report_on_file`.  Nothing in the message may change the
reply: every wording must give exactly the same reply (alone; after a dedupe).

    python r5_wordings.py [--tree DIR] [--speakers ...]
"""
import r5lib

A = r5lib.args()
rq = r5lib.start(A)
from rq import AN, DD, brain, plan  # noqa: E402

RPT = "report_on_file"
WORDINGS = [
    "give me a report", "Can you provide a report?", "I'd like a summary please", "analyze my file", "summarize it for me",
    "analyze the email column", "summarize the share data", "report on the post code column", "give me a report on the Schedule column",
    "analyze my file and tell me what needs to be fixed", "give me a report on which rows to remove", "report on the duplicates that were removed",
    "give me a summary of what you cleaned", "analyze my file before I clean it", "just analyze my file, don't delete anything",
    "give me a report but don't remove anything", "give me a summary so I can review it", "I built this file myself, can you analyze it",
    "I'm new to this myself, give me a summary of the file", "I will review it later, give me a summary now", "don't hesitate to give me a report on everything you find",
    "what is wrong with my file", "check how clean my data is", "give me an overview of the file", "a rundown please", "thanks for the summary!",
    "update the summary", "finalize the report", "remove the duplicate rows from my sales report", "Where can I find the report?",
]
S = {}
for i, w in enumerate(WORDINGS):
    S[f"W{i:02d}|alone|{w}"] = dict(file="plain", turns=[dict(msg=w, b=brain(RPT, (RPT, w)))])
    m = f"remove the duplicate rows and {w}"
    S[f"W{i:02d}|dedupe|{w}"] = dict(file="plain", turns=[dict(
        msg=m, b=brain("multi_step_request", ("remove_duplicate_rows", "remove the duplicate rows"), (RPT, w)),
        p=plan((DD, "remove the duplicate rows"), (AN, w)))])

if __name__ == "__main__":
    res = rq.run_all(S, (), tuple(A.speakers.split(",")))
    import collections
    groups = collections.defaultdict(set)
    for r in res:
        groups[(r["speaker"], r["name"].split("|")[1])].add(r["reply"])
    for k, v in sorted(groups.items()):
        print(k, "distinct replies:", len(v))
    r5lib.dump(res, A.out or str(r5lib.HERE / "out" / "r5-wordings.json"))
