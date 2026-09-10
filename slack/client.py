"""Slack client: post + update messages. Failures return None/False, never raise into audit."""
from __future__ import annotations

import json
import os
import urllib.request

_API = "https://slack.com/api"


def _api(method: str, payload: dict) -> dict | None:
    tok = os.environ.get("SLACK_BOT_TOKEN", "")
    if not tok:
        return None
    req = urllib.request.Request(f"{_API}/{method}", data=json.dumps(payload).encode(),
                                 headers={"Authorization": f"Bearer {tok}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode())
        return data if data.get("ok") else None
    except Exception:
        return None


def post_message(channel: str, text: str, blocks: list | None = None) -> dict | None:
    """Send a DM. Returns {'channel','ts','id'} ref usable by update_message, else None."""
    if not channel:
        return None
    payload: dict = {"channel": channel, "text": text[:3000]}
    if blocks:
        payload["blocks"] = blocks
    data = _api("chat.postMessage", payload)
    if not data:
        return None
    return {"channel": str(data.get("channel", channel)), "ts": str(data.get("ts", ""))}


def update_message(ref: dict | None, text: str, blocks: list | None = None) -> bool:
    """chat.update an existing message (in-place state change, no new notification)."""
    if not ref or not ref.get("channel") or not ref.get("ts"):
        return False
    payload: dict = {"channel": ref["channel"], "ts": ref["ts"], "text": text[:3000]}
    if blocks:
        payload["blocks"] = blocks
    return _api("chat.update", payload) is not None


def post_ephemeral_like(channel: str, text: str) -> bool:
    return bool(post_message(channel, text))
