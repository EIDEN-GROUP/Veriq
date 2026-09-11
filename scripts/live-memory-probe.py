"""Live memory probe: signs slash requests like Slack, fires /remember + /ask.
The /ask reply is delivered ASYNC (3s ack rule) to this user's DM — check Slack.
Usage: set env GW_SEC (gateway signing secret), optionally pass UID + GW URL."""
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from urllib.error import HTTPError

GW = sys.argv[2] if len(sys.argv) > 2 else "https://veriq.eiden-group.com"
SEC = os.environ["GW_SEC"]
UID = sys.argv[1] if len(sys.argv) > 1 else "U0AQWT35TP0"


def slash(command, text=""):
    fields = {"command": command, "user_id": UID, "user_name": "marouaneakrich",
              "text": text, "channel_id": UID}
    raw = urllib.parse.urlencode(fields)
    ts = str(int(time.time()))
    mac = hmac.new(SEC.encode(), f"v0:{ts}:{raw}".encode(), hashlib.sha256).hexdigest()
    req = urllib.request.Request(GW + "/slack/slash", data=raw.encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded",
                                          "X-Slack-Signature": f"v0={mac}",
                                          "X-Slack-Request-Timestamp": ts})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except HTTPError as e:
        return {"text": "HTTP %s %s" % (e.code, e.read().decode()[:200])}


print("=== /remember ===")
d = slash("/remember", "the production database is Postgres on Neon and deploys happen every Friday at 5pm")
print(d.get("response_type"), str(d.get("text", ""))[:90].replace("\n", " "))

print("=== /memory (proof stored) ===")
d = slash("/memory")
t = d.get("text", "")
print("stored-ok:", "Neon" in t and "Friday" in t)

print("=== /ask (ack-first; answer lands in your Slack DM within ~1min) ===")
d = slash("/ask", "Without inventing anything: which database service do we use and what weekday do we deploy? One short sentence.")
print(str(d.get("text", ""))[:80].replace("\n", " "))
print("NOW LOOK AT SLACK: the 👾 answer to your DM should read like it knows Neon + Friday.")

print("=== /status sanity ===")
print(str(slash("/status").get("text", ""))[:90].replace("\n", " "))
