"""One client's requests for check-proxy.sh. Runs inside a throwaway container of the app image
(``docker exec -i <client> python - NAME COUNT [XFF]``), so its source address is that container's.

Sends COUNT sign-in attempts with a wrong password to https://NAME/api/v1/access, with the page's
Origin, and an X-Forwarded-For header if XFF is given. Prints the HTTP statuses as a JSON list.
A wrong password costs the "access" rate-limit bucket (burst 10, 20 an hour), nothing else.
"""

import http.client
import json
import os
import ssl
import sys

name, count = sys.argv[1], int(sys.argv[2])
spoof = sys.argv[3] if len(sys.argv) > 3 else None
context = ssl.create_default_context(cafile="/ca.crt" if os.path.exists("/ca.crt") else None)
statuses = []
for _ in range(count):
    connection = http.client.HTTPSConnection(name, 443, context=context, timeout=20)
    headers = {
        "Origin": f"https://{name}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    if spoof:
        headers["X-Forwarded-For"] = spoof
    connection.request("POST", "/api/v1/access", "password=not-the-password-for-the-check", headers)
    response = connection.getresponse()
    response.read()
    statuses.append(response.status)
    connection.close()
print(json.dumps(statuses))
