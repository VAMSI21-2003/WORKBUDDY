# ruff: noqa: E501
"""match_file_name fuzz/adversarial (unit level, real buddy.source). python probe_c_names.py [--tree DIR]"""
import sys, time, random
from pathlib import Path
HERE = Path(__file__).resolve().parent
root = Path(sys.argv[sys.argv.index("--tree")+1]).resolve() if "--tree" in sys.argv else HERE.parents[2]
sys.path.insert(0, str(root))
from buddy.source import match_file_name as m

names = ["messy-data.xlsx", "other.xlsx"]
cases = {
 "exact": "clean messy-data.xlsx please",
 "typo1": "mess-data.xlsx", "typo2": "messy-data.xslx", "upper": "MESSY-DATA.XLSX", "quoted": "'messy-data.xlsx'",
 "trailing-dot": "do it on messy-data.xlsx.", "paren": "(messy-data.xlsx)", "comma": "messy-data.xlsx, then other.xlsx",
 "both names": "compare messy-data.xlsx and other.xlsx",
 "stem only": "clean messy-data", "report word": "give me a report", "other stem": "use other",
 "output name": "remove duplicates and save it as cleaned.xlsx",
 "output name2": "remove duplicates, export to results.csv",
 "ext only": "convert to .xlsx", "ext word": "is this an xlsx file?", "url": "see https://example.com/a.csv",
 "email": "send to bob@example.com", "version": "v1.2 rows 3.5", "ip": "192.168.1.1", "sentence period": "remove dups.Then stop",
 "dotted word": "e.g. the first one", "ie": "i.e. exact", "decimals": "total 12.50 rows",
 "xlsb": "messy-data.xlsb", "ods": "messy-data.ods", "xls->xlsx": "messy-data.xls", "csv-ext": "messy-data.csv",
 "space name": "my data.xlsx",
 "path": "C:\\Users\\me\\Desktop\\messy-data.xlsx", "unix path": "/home/me/messy-data.xlsx",
 "other-close": "otherr.xlsx", "nonascii": "mëssy-data.xlsx", "zero width": "messy\u200b-data.xlsx",
 "trailing nbsp": "messy-data.xlsx\u00a0please", "newline": "messy-data.xlsx\nplease", "tab": "x\tmessy-data.xlsx",
 "far": "budget.xlsx", "three digits": "report2024.xlsx",
 "long no spaces": "a"*5000+".xlsx", "many dots": "."*3000, "many tokens": " ".join(["a.b.xlsx"]*3000),
}
for k, v in cases.items():
    t = time.perf_counter(); r = m(v, names); dt = time.perf_counter()-t
    print(f"{k:16} -> idx={r.indices} unclear={r.unclear}  ({dt*1000:.0f} ms)  {v[:40]!r}")
# two close names
n2 = ["data1.xlsx", "data2.xlsx"]
for v in ["data.xlsx", "data1.xlsx", "data2.xlsx", "data3.xlsx", "data12.xlsx"]:
    r = m(v, n2); print("2close", v, r)
n3 = ["Q1 report.xlsx", "Q2 report.xlsx"]
for v in ["use Q1 report.xlsx", "Q3 report.xlsx", "Q1 reprt.xlsx", "q1report.xlsx"]:
    print("space-names", v, m(v, n3))
n4 = ["a.xlsx", "b.xlsx"]  # very short names
for v in ["c.xlsx", "a.xls", "A.xlsx", "ab.xlsx"]:
    print("short", v, m(v, n4))
# same name uploaded twice
print("dup", m("messy-data.xlsx", ["messy-data.xlsx", "messy-data.xlsx"]))
# timing worst case
random.seed(1)
big = ["f%03d_%s.xlsx" % (i, "x"*random.randint(5,60)) for i in range(200)]
t=time.perf_counter(); r=m(" ".join("w%d.xlsx"%i for i in range(250)), big); print("200 names x 250 tokens", f"{time.perf_counter()-t:.2f}s", r.unclear)
t=time.perf_counter(); r=m(("word."*400)[:2000], big); print("200 names x 2000 chars dots", f"{time.perf_counter()-t:.2f}s", r.unclear)
