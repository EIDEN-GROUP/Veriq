"""Slack approval: post two-button request, poll gateway for the verified decision.

The gateway (gateway/app.py) validates signatures + authz; this module only polls.
Without a gateway URL configured, approval safely resolves to 'expired' (no mutation).
"""
from __future__ import annotations

import json
import os
import time
import urllib.request

from slack.client import post_message
from slack.formatting import approval_blocks


def register_with_gateway(audit: dict, timeout_minutes: int = 30) -> bool:
    """Bind (audit_id, repository, commit, pr, dev) at the gateway BEFORE polling.

    Without this the gateway answers 404 unknown-audit and the buttons dead-end.
    Failure is fail-safe: the Slack message still goes out, the wait expires,
    and zero code is modified.
    """
    gateway = os.environ.get("APPROVAL_GATEWAY_URL", "").rstrip("/")
    if not gateway:
        return False
    payload = {"audit_id": audit["audit_id"], "repository": audit["repository"],
               "commit": audit["commit"], "pr_number": audit.get("pr_number"),
               "triggered_by": audit["triggered_by"], "slack_user": audit.get("slack_user"),
               "timeout_minutes": timeout_minutes}
    try:
        req = urllib.request.Request(f"{gateway}/audits", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            return bool(json.loads(r.read().decode()).get("ok", False))
    except Exception as e:
        print(f"gateway registration failed (approval will expire safely): {e}")
        return False


def request_approval(audit: dict, timeout_note: int = 30) -> bool:
    register_with_gateway(audit, timeout_note)
    dev = audit.get("slack_user")
    admin = os.environ.get("SLACK_ADMIN_USER_ID", "")
    target = dev or admin
    if not target:
        return False
    text = f"Veriq audit {audit['audit_id']}: {audit['fixes']['identified']} fixable issue(s). Approve?"
    ok = post_message(target, text, approval_blocks(audit))
    if not ok and dev and admin and dev != admin:
        ok = post_message(admin, text + f" (developer {audit['triggered_by']} unmapped/unreachable)",
                          approval_blocks(audit))
    return ok


def wait_for_decision(audit_id: str, timeout_minutes: int = 30, poll_s: int = 10) -> dict:
    gateway = os.environ.get("APPROVAL_GATEWAY_URL", "").rstrip("/")
    if not gateway:
        # No callback path: Reaction-poll fallback disabled by default -> safe expiry.
        # (Teams using only Actions without gateway get notify-only behavior.)
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
