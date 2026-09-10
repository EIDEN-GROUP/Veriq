"""Slack approval: register at gateway -> post two-button request -> poll decision.

In-place UX: after a click is recorded, the SAME message is updated
(pending -> 🔧 working -> ✅ final outcome); developers get zero duplicate DMs.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request

from slack.client import post_message, update_message
from slack.formatting import approval_blocks, working_blocks


def register_with_gateway(audit: dict, timeout_minutes: int = 30) -> bool:
    gateway = os.environ.get("APPROVAL_GATEWAY_URL", "").rstrip("/")
    if not gateway:
        return False
    payload = {"audit_id": audit["audit_id"], "repository": audit["repository"],
               "commit": audit["commit"], "pr_number": audit.get("pr_number"),
               "triggered_by": audit["triggered_by"], "slack_user": audit.get("slack_user"),
               "timeout_minutes": timeout_minutes,
               "fixable": [f["id"] for f in audit.get("findings", [])
                           if f.get("auto_fixable") and not f.get("needs_human_review")][:10]}
    headers = {"Content-Type": "application/json"}
    reg = os.environ.get("GATEWAY_REGISTRATION_TOKEN", "")
    if reg:
        headers["X-Veriq-Token"] = reg
    try:
        req = urllib.request.Request(f"{gateway}/audits", data=json.dumps(payload).encode(),
                                     headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            return bool(json.loads(r.read().decode()).get("ok", False))
    except Exception as e:
        print(f"gateway registration failed (approval will expire safely): {e}")
        return False


def request_approval(audit: dict, timeout_note: int = 30) -> bool:
    """Post the approval DM; stores chat ref under audit['approval']['message_ref']."""
    register_with_gateway(audit, timeout_note)
    dev = audit.get("slack_user")
    admin = os.environ.get("SLACK_ADMIN_USER_ID", "")
    target = dev or admin
    if not target:
        return False
    blocks = approval_blocks(audit, timeout_note)
    text = (f"Veriq audit {audit['audit_id']}: {audit['fixes']['identified']} "
            f"fixable issue(s). Approve?")
    ref = post_message(target, text, blocks)
    if not ref and dev and admin and dev != admin:
        ref = post_message(admin, text + f" (developer {audit['triggered_by']} unmapped)", blocks)
    if ref:
        audit.setdefault("approval", {})["message_ref"] = ref
    return bool(ref)


def mark_working(audit: dict) -> bool:
    """Right before the fix loop: rewrite the approval message to an in-progress banner."""
    return update_message(audit.get("approval", {}).get("message_ref"),
                          "🔧 Veriq is applying approved fixes…", working_blocks(audit))


def wait_for_decision(audit_id: str, timeout_minutes: int = 30, poll_s: int = 10) -> dict:
    gateway = os.environ.get("APPROVAL_GATEWAY_URL", "").rstrip("/")
    if not gateway:
        # No callback path configured: fail safe to expiry (notify-only behaviour).
        time.sleep(2)
        return {"decision": "expired", "reason": "no approval gateway configured; defaulting to no-change"}
    deadline = time.time() + timeout_minutes * 60
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{gateway}/approvals/{audit_id}", timeout=15) as r:
                data = json.loads(r.read().decode())
            if data.get("decision") in ("approved", "rejected", "expired"):
                return data
        except Exception:
            pass
        time.sleep(poll_s)
    return {"decision": "expired", "reason": f"no response in {timeout_minutes}min"}
