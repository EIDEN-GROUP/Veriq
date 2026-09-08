"""Fan-out notifications: developer gets their audit; admin ALWAYS gets the full audit."""
from __future__ import annotations

import json
import os
from pathlib import Path

from slack.client import post_message
from slack.formatting import admin_text, final_dev_text


def notify_audit(audit: dict, admin_id: str = "", artifacts_dir: str | Path = "artifacts") -> dict:
    """Returns delivery receipt; failures are recorded, never raised."""
    receipt = {"dev": False, "admin": False}
    run_url = os.environ.get("GITHUB_RUN_URL", "")
    dev = audit.get("slack_user")
    if dev:
        receipt["dev"] = post_message(dev, final_dev_text(audit))
    admin = admin_id or os.environ.get("SLACK_ADMIN_USER_ID", "")
    if admin:
        receipt["admin"] = post_message(admin, admin_text(audit, run_url))
    if not receipt["dev"] or not receipt["admin"]:
        try:
            Path(artifacts_dir).mkdir(parents=True, exist_ok=True)
            Path(artifacts_dir, "slack-delivery-failed.json").write_text(json.dumps(
                {"receipt": receipt, "dev_text": final_dev_text(audit),
                 "admin_text": admin_text(audit, run_url)}))
        except OSError:
            pass
    # PR comment mirrors the audit (best-effort)
    try:
        from github.comments import comment_body, upsert_bot_comment
        upsert_bot_comment(audit["repository"], audit.get("pr_number"), comment_body(audit))
    except Exception:
        pass
    return receipt
