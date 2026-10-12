import json, sys
d = json.load(open(sys.argv[1]))
pat = sys.argv[2] if len(sys.argv) > 2 else ""
for k, turns in d.items():
    if pat not in k: continue
    print("=====", k)
    for t in turns:
        if "EXC" in t: print("EXC", t); continue
        print(f"-- {t['label']!r} -> {t['outcome']} files={t['delivered_names']} desc={t['final_describe']} next={ (t['next_request_file'] or {}).get('kind')}:{(t['next_request_file'] or {}).get('name')} unchanged={t['uploads_unchanged']} voice={t['voice_calls']}")
        print("   ", t['reply'])
