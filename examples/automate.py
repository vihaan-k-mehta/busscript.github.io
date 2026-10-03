"""Drive the analyzer from a script over its REST API. Standard library only.

    python -m core --demo --no-browser --port 8765        # in one terminal
    python examples/automate.py                           # in another

The token is in <data dir>/token (default ~/busscript-data/token, or $BUSSCRIPT_DATA).
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ.get("CAN_URL", "http://127.0.0.1:8765")
TOKEN = Path(os.environ.get("BUSSCRIPT_DATA", Path.home() / "busscript-data"), "token").read_text().strip()


def call(method: str, path: str, body=None):
    req = urllib.request.Request(
        BASE + path, method=method, data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {path} -> {e.code}: {json.load(e).get('detail')}")


call("POST", "/api/measurement/start")
time.sleep(1.0)

# read a decoded signal by name
for row in call("GET", "/api/signal-values"):
    if f"{row['message']}.{row['signal']}" == "VehicleSpeed.Speed":
        print("Speed:", row["value"], row["unit"])

# send a signal by name, no per-signal code (needs a channel that is not listen-only)
sent = call("POST", "/api/transmit/signal",
            {"channel": "demo", "message": "VehicleSpeed", "signals": {"Speed": 42.5}})
print("sent frame", hex(sent["id"]), sent["data"])

# record to a file for a few seconds, then stop
log = str(Path(os.environ.get("BUSSCRIPT_DATA", Path.home() / "busscript-data")) / "logs" / "automation.asc")
call("POST", "/api/log/start", {"path": log, "format": "asc"})
time.sleep(2.0)
print("log:", call("POST", "/api/log/stop"))

print("statistics:", call("GET", "/api/statistics"))
call("POST", "/api/measurement/stop")
