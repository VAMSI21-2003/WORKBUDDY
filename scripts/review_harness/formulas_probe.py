"""Round 5: a single-sheet workbook with formulas, formatting, a table and a data validation, run through
dedupe via the real engine. Shows what the delivered file keeps and what the reply says. Run from repo root."""
import os, sys, tempfile, hashlib
from pathlib import Path
sys.argv = [sys.argv[0]] + (["--tree", os.environ["TREE"]] if os.environ.get("TREE") else [])
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, os.environ.get("TREE") or str(Path(__file__).resolve().parents[2]))
import harness as H
import openpyxl
from openpyxl.styles import Font, PatternFill
d = Path(tempfile.mkdtemp()); os.chdir(d); (d/"uploads").mkdir()
p = d/"uploads/report.xlsx"
wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Sheet1"
ws.append(["name", "amount", "double"])
for i, (n, a) in enumerate([("Alice", 10), ("Bob", 20), ("Alice", 10), ("Carol", 30)], start=2):
    ws.append([n, a, f"=B{i}*2"])
ws["A1"].font = Font(bold=True, color="FF0000"); ws["B2"].fill = PatternFill("solid", fgColor="FFFF00")
ws.column_dimensions["A"].width = 40
ws["E1"] = "note: totals below"; ws.merge_cells("E1:F1")
wb.save(p)
# cache values by round trip via LibreOffice is unavailable: formulas have no cached value.
h0 = hashlib.sha256(p.read_bytes()).hexdigest()
st = H.BuddyState.new(session_id="s", workspace_id="w"); st.user_message = "remove the duplicate rows"
st.context.add_uploaded_file("uploads/report.xlsx"); st.context.switch_active_file("uploads/report.xlsx")
pr = H.P(H.brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows")), None)
H.build_engine(llm_provider=pr).run(st)
print("REPLY:", st.reply)
out = st.execution_result.outputs[0]
w2 = openpyxl.load_workbook(out); s2 = w2.active
print("rows:", [[c.value for c in r] for r in s2.iter_rows()])
print("A1 bold/red:", s2["A1"].font.b, s2["A1"].font.color and s2["A1"].font.color.rgb, "| col A width:", s2.column_dimensions["A"].width, "| merged:", s2.merged_cells.ranges)
print("original unchanged:", hashlib.sha256(p.read_bytes()).hexdigest() == h0)
