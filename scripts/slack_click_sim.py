"""Debug: sign a real-Shaped block_actions payload and hit a live gateway."""
import hashlib
import hmac
import json
import os
import time
import urllib.parse
import urllib.request

GATEWAY = os.environ["GATEWAY"]
SEC = os.environ["GW_SEC"]
AUDIT = "AUDIT-" + str(int(time.time()))
REPO = "EIDEN-GROUP/Veriq"

urllib.request.urlopen(urllib.request.Request(f"{GATEWAY}/audits",
    data=json.dumps({"audit_id": AUDIT, "repository": REPO, "commit": "dead001",
                     "pr_number": None, "triggered_by": "marouaneakrich",
                     "slack_user": "U0AQWT35TP0", "timeout_minutes": 5}).encode(),
    headers={"Content-Type": "application/json"}, method="POST"), timeout=20)

action = {"type": "button", "action_id": f"approve:{AUDIT}",
          "block_id": f"veriq:{AUDIT}", "style": "primary",
          "text": {"type": "plain_text", "text": "Allow AI to fix"}, "value": f"{REPO}|dead001|"}
payload = {"type": "block_actions", "trigger_id": "T1", "api_app_id": "A1",
           "team": {"id": "T1", "domain": "eiden"},
           "user": {"id": "U0AQWT35TP0", "username": "marouaneakrich", "team_id": "T1"},
           "action": action, "actions": [action],
           "message": {"type": "message", "user": "UBOT", "bot_id": "BOT", "ts": "1700", "text": "audit"},
           "container": {"type": "message", "message_ts": "1700", "channel_id": "D1", "is_ephemeral": False},
           "channel": {"id": "D1", "name": "dm"},
           "response_url": "https://hooks.slack.com/actions/T/X/Y", "state": {"values": {}}}
raw = "payload=" + urllib.parse.quote(json.dumps(payload))
ts = str(int(time.time()))
sig = "v0=" + hmac.new(SEC.encode(), f"v0:{ts}:{raw}".encode(), hashlib.sha256).hexdigest()
req = urllib.request.Request(f"{GATEWAY}/slack/actions", data=raw.encode(),
                             headers={"Content-Type": "application/x-www-form-urlencoded",
                                      "X-Slack-Signature": sig,
                                      "X-Slack-Request-Timestamp": ts}, method="POST")
try:
    with urllib.request.urlopen(req, timeout=20) as r:
        print("HTTP", r.status, r.read().decode()[:200])
except urllib.error.HTTPError as e:
    print("HTTPError", e.code, e.read().decode()[:300])

try:
    with urllib.request.urlopen(f"{GATEWAY}/approvals/{AUDIT}", timeout=20) as r:
        print("POLLED:", r.read().decode()[:300])
except urllib.error.HTTPError as e:
    print("Error", e.read().decode()[:300])
