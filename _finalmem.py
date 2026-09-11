"""Final live memory + NIM verification."""
import hashlib
import hmac
import json
import os
import time
import urllib.parse
import urllib.request
from urllib.error import HTTPError

GW = "https://veriq.eiden-group.com"
SEC = os.environ["GW_SEC"]
UID = "U0AQWT35TP0"


def slash(command, text=""):
    fields = {"command": command, "user_id": UID, "user_name": "marouaneakrich",
              "text": text, "channel_id": "D0"}
    raw = urllib.parse.urlencode(fields)
    ts = str(int(time.time()))
    mac = hmac.new(SEC.encode(), f"v0:{ts}:{raw}".encode(), hashlib.sha256).hexdigest()
    req = urllib.request.Request(GW + "/slack/slash", data=raw.encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded",
                                          "X-Slack-Signature": f"v0={mac}",
                                          "X-Slack-Request-Timestamp": ts})
    try:
        with urllib.request.urlopen(req, timeout=150) as r:
            return json.loads(r.read().decode())
    except HTTPError as e:
        return {"text": "HTTP %s %s" % (e.code, e.read().decode()[:200])}


d = slash("/remember", "the production database is Postgres on Neon and deploys happen every Friday at 5pm")
print("remember:", d.get("response_type"))
d = slash("/ask", "Without inventing anything: which database service do we use and what day do we deploy? Answer in one short sentence.")
txt = d.get("text", "")
print("ask:", txt[:300].replace("\n", " "))
low = txt.lower()
print("MEMORY-GROUNDING:", ("neon" in low) and ("friday" in low))
slash("/forget")
print("purged.")
