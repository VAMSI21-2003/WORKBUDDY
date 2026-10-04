"""Round 5: does any crafted sheet name make the skill's output sheet list differ from the input's
(which would put file-derived text into the Inspector finding)? Run from the repo root."""
import os, sys, tempfile, zipfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import pandas as pd
from skills.excel.dedupe import ExcelDeduplicateSkill
from skills.excel.workbook import sheet_names, WorkbookRefused
from skills.base import SkillTask, PermissionTier

names = ["'quoted'", "‮RTL", "😀emoji", "a\tb", "a\nb", "  x  ", "x ", "=SUM(A1)", "History",
         "a" * 31, "A" * 32, "A" * 80, "ａｂｃ", "Ünï", "a;b", "a,b", "a'b", "a&b", "a<b>", "a\"b", "..", "a b", "A\u0000B"]
d = Path(tempfile.mkdtemp()); os.chdir(d)
for i, nm in enumerate(names):
    p = d / f"in{i}.xlsx"
    pd.DataFrame({"a": [1, 1, 2]}).to_excel(p, index=False, sheet_name="Sheet1")
    tmp = d / "t.xlsx"
    with zipfile.ZipFile(p) as zi, zipfile.ZipFile(tmp, "w") as zo:
        for it in zi.infolist():
            data = zi.read(it.filename)
            if it.filename == "xl/workbook.xml":
                esc = nm.replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;")
                data = data.replace(b'name="Sheet1"', f'name="{esc}"'.encode("utf-8", "replace"))
            zo.writestr(it, data)
    os.replace(tmp, p)
    try:
        before = sheet_names(str(p))
    except WorkbookRefused as e:
        print(repr(nm), "input refused:", e); continue
    except Exception as e:
        print(repr(nm), "input EXC", type(e).__name__); continue
    t = SkillTask(task_id="t", session_id="s", workspace_id="w", description="d",
                  input_references=[str(p)], permission=PermissionTier.WRITE)
    r = ExcelDeduplicateSkill().execute(t)
    if r.status.value != "success":
        print(repr(nm), "skill:", r.status.value, r.errors); continue
    after = sheet_names(r.outputs[0]) if hasattr(r, "outputs") and r.outputs else None
    print(repr(nm), "same" if before == after else f"MISMATCH {before!r} -> {after!r}")
