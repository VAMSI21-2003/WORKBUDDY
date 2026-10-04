"""Round 2: skills patched to the old single-sheet check; does the Inspector alone stop NA/error sheets?
  python scripts/review_harness/round2_skill_doesnt_refuse.py
"""
import sys
sys.argv = [sys.argv[0], "--out", "/dev/null"]
import round2_probe as R  # noqa: E402  (registers r2_ fixtures)
import values_only_probe as V  # noqa: E402
import skills.excel.dedupe as D, skills.excel.remove_empty_rows as E  # noqa: E402
from skills.excel.workbook import single_sheet_name  # noqa: E402
D.plain_table_sheet_name = single_sheet_name; E.plain_table_sheet_name = single_sheet_name
for fx in ("na_strings", "r2_formula_text_na", "r2_wrongdim_small_na", "gap_dim_a1", "r2_error_in_empty_row"):
    for req in ("dedupe", "chain"):
        for sp in ("template", "friend", "focus"):
            r = V.run_one(fx, req, sp, "s1")
            print(f"{fx:24}{req:7}{sp:9} delivered={r['delivered']} unchanged={r['orig_unchanged']} steps={[s[1][:4] for s in r['steps']]}\n    {r['reply'][:230]}")
