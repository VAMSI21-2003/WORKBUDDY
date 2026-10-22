"""Round-1 probe 6: HEAD /healthz vs GET /healthz (status, headers, rate bucket, 429 body, other methods).
    python p6_healthz.py [--tree DIR]
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feat-backend-api-confirmation"))
import pr3  # noqa: E402
from pr3 import h  # noqa: E402

app = pr3.make_app(h.FakeLLM([]), "template")
c = app.client
r = [c.head("/healthz") for _ in range(12)]
print("HEAD x12:", [x.status_code for x in r])
print("429 headers:", {k: v for k, v in r[-1].headers.items()}, "body_len", len(r[-1].content))
print("GET after HEAD burst:", c.get("/healthz").status_code)
app2 = pr3.make_app(h.FakeLLM([]), "template")
c2 = app2.client
g = [c2.get("/healthz").status_code for _ in range(6)] + [c2.head("/healthz").status_code for _ in range(6)]
print("GET x6 then HEAD x6 (shared bucket of 10):", g)
app3 = pr3.make_app(h.FakeLLM([]), "template")
for m in ("options", "post", "put", "delete", "patch"):
    x = getattr(app3.client, m)("/healthz")
    print(m.upper(), x.status_code)
x = app3.client.head("/healthz", headers={"Authorization": "Bearer junk"})
print("HEAD with junk credential:", x.status_code)
x = app3.client.head("/openapi.json"); print("HEAD /openapi.json", x.status_code)
x = app3.client.head("/healthz/"); print("HEAD /healthz/", x.status_code)
print("openapi mentions head healthz:", "head" in str(app3.client.get("/openapi.json").json().get("paths", {}).get("/healthz", {})))
