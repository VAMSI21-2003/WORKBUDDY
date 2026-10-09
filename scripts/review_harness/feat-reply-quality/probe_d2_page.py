"""Round-1 probe D2: the web page's transcript shows the friendly download names (pr3 handles --tree)."""
import json, re, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.argv.append("--out") if False else None
exec(open(HERE / "probe_d_api.py").read().split("res = {}")[0])
app, llm, sid, orig = session("friend")
turn(app, llm, sid, orig, "remove the duplicate rows", brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows")))
turn(app, llm, sid, orig, "remove the duplicate rows", brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows")))
turn(app, llm, sid, orig, "remove duplicates then empty rows", brain("multi_step_request", ("remove_duplicate_rows", "remove duplicates"), ("remove_empty_rows", "empty rows")), p=plan((DD, "remove duplicates"), (ER, "empty rows")))
r = app.client.get(f"/ui/s/{sid}/transcript")
print(r.status_code)
html_text = r.text
print(re.findall(r'(?:download|href)="([^"]*download[^"]*)"', html_text)[:6])
print(sorted(set(re.findall(r"[A-Za-z0-9_ ()\-]+\.xlsx", html_text))))
print("internal ids in page:", bool(re.search(r"[0-9a-f]{32}-task-", html_text)))
