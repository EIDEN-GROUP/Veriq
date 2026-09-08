"""Slack client: chat.postMessage wrapper; failures return False, never raise into audit."""
from __future__ import annotations

import json
import os
import urllib.request


def post_message(channel: str, text: str, blocks: list | None = None) -> bool:
    tok = os.environ.get("SLACK_BOT_TOKEN", "")
    if not tok or not channel:
        return False
    payload = {"channel": channel, "text": text}
    if blocks:
        payload["blocks"] = blocks
    req = urllib.request.Request("https://slack.com/api/chat.postMessage",
                                 data=json.dumps(payload).encode(),
                                 headers={"Authorization": f"Bearer {tok}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode()).get("ok", False)
    except Exception:
        return False
