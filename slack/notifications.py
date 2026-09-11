"""Fan-out notifications: the approval message (if any) is UPDATED in place with the
final outcome; admin receives the full audit as a separate DM — always, regardless of
the developer's decision (independent record). Delivery failures save payloads."""
from __future__ import annotations

import json
import os
from pathlib import Path

from slack.client import post_message, update_message
from slack.formatting import (admin_blocks, admin_text, dev_text,
                              expired_state_blocks, final_dev_blocks,
                              rejected_state_blocks)


def _final_or_state(audit: dict, run_url: str) -> tuple[str, list]:
    return dev_text(audit), final_dev_blocks(audit, run_url)


def notify_audit(audit: dict, admin_id: str = "", artifacts_dir: str | Path = "artifacts") -> dict:
    """Returns delivery receipt; failures are recorded, never raised."""
    receipt = {"dev": False, "admin": False}
    run_url = os.environ.get("GITHUB_RUN_URL", "")
    ap = audit.setdefault("approval", {})
    ref = ap.get("message_ref")

    # 1) Developer: prefer in-place update of the approval message; else fresh DM.
    text, blocks = _final_or_state(audit, run_url)
    if ref:
        receipt["dev"] = update_message(ref, text, blocks)
    elif audit.get("slack_user"):
        if ap.get("decision") == "rejected":
            text, blocks = "🔴 Veriq: declined — code untouched", \
                rejected_state_blocks(audit["audit_id"], audit.get("triggered_by", ""))
        elif ap.get("decision") == "expired":
            text, blocks = "⏱️ Veriq: approval expired — untouched", \
                expired_state_blocks(audit["audit_id"])
        receipt["dev"] = bool(post_message(audit["slack_user"], text, blocks))

    # 2) Admin: ALWAYS a separate DM (independent record)
    admin = admin_id or os.environ.get("SLACK_ADMIN_USER_ID", "")
    if admin:
        receipt["admin"] = bool(post_message(admin, admin_text(audit, run_url),
                                             admin_blocks(audit, run_url)))
    if not (receipt["dev"] or receipt["admin"]):
        try:
            Path(artifacts_dir).mkdir(parents=True, exist_ok=True)
            Path(artifacts_dir, "slack-delivery-failed.json").write_text(json.dumps(
                {"receipt": receipt, "dev_text": text, "admin_text": admin_text(audit, run_url)}))
        except OSError:
            pass
    # 3) PR comment mirrors the audit (best-effort)
    try:
        from github.comments import comment_body, upsert_bot_comment
        upsert_bot_comment(audit["repository"], audit.get("pr_number"), comment_body(audit))
    except Exception:
        pass
    # 4) Tell the gateway the audit is done (powers /status + grounded /ask chat)
    try:
        from slack.approval import report_result
        report_result(audit)
    except Exception:
        pass
    return receipt
