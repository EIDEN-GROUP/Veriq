"""E2E Slack approval test: posts a REAL approval message (same code path as production)
to a user, then polls the gateway until they click 🟢/🔴 or the window expires.

Run by .github/workflows/e2e-slack-test.yml (needs SLACK_BOT_TOKEN + APPROVAL_GATEWAY_URL env).
DRY=1 renders everything without network (dev check).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def build_audit() -> dict:
    aid = f"AUDIT-E2E-{time.strftime('%Y-%m-%d-%H%M')}-{os.getpid() % 0xFFFF:04X}"
    return {
        "audit_id": aid, "repository": os.environ.get("TEST_REPO", "EIDEN-GROUP/Veriq"),
        "branch": "main", "commit": os.environ.get("TEST_COMMIT", "e2e0001"),
        "pr_number": None, "triggered_by": os.environ.get("GITHUB_ACTOR", "marouaneakrich"),
        "slack_user": os.environ.get("TEST_USER", "U0AQWT35TP0"), "overall_score": 82,
        "findings": [{"id": "CODE-001", "severity": "LOW", "category": "code",
                      "title": "E2E test finding (click a button to see the flow)",
                      "description": "Synthetic finding to test the approval round-trip.",
                      "file": "example.py", "line": 1, "evidence": "E2E probe",
                      "recommendation": "none", "auto_fixable": True, "confidence": 0.95}],
        "severity_counts": {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 1, "INFO": 0},
        "tests": {"status": "pass"}, "build": {"status": "pass"},
        "security": {"status": "pass"}, "frontend": {"status": "skipped"},
        "fixes": {"identified": 1, "approved": 0, "fixed": 0, "failed": []},
        "verification": {}, "approval": {"requested": True, "decision": "pending"},
        "timestamps": {}, "artifacts": [],
    }


def main() -> int:
    from slack.approval import register_with_gateway, wait_for_decision
    from slack.client import post_message
    from slack.formatting import admin_text, approval_blocks, final_dev_text

    user = os.environ.get("TEST_USER", "U0AQWT35TP0")
    timeout_min = int(os.environ.get("TEST_TIMEOUT_MINUTES", "5"))
    audit = build_audit()
    blocks = approval_blocks(audit)

    if os.environ.get("DRY") == "1":
        print(json.dumps({"audit_id": audit["audit_id"], "blocks": blocks}, indent=2))
        return 0

    if not register_with_gateway(audit, timeout_min):
        print("::error::gateway registration failed (is APPROVAL_GATEWAY_URL + /audits reachable?)")
        return 2
    text = final_dev_text({**audit, "approval": {"decision": "pending"}})
    if not post_message(user, text, blocks):
        print("::error::Slack post failed (SLACK_BOT_TOKEN missing, or bot can't DM "
              f"{user} — check app scopes/installed members)")
        return 3
    # Second DM in admin format so both production renderers are exercised
    post_message(user, admin_text(audit, os.environ.get("GITHUB_RUN_URL", "")))

    started = time.time()
    decision = wait_for_decision(audit["audit_id"], timeout_minutes=timeout_min)
    secs = int(time.time() - started)
    print(f"DECISION={decision.get('decision')} after {secs}s -> {json.dumps(decision)}")
    recap = (f"Veriq E2E `{audit['audit_id']}`: gateway recorded **{decision.get('decision')}**"
             f" (approver: {decision.get('approver_slack_id', '—')}). "
             "Production chain verified." if decision.get("decision") in ("approved", "rejected")
             else f"Veriq E2E `{audit['audit_id']}`: no click within {timeout_min}min -> "
                  "'expired'. Buttons + polling path work; nobody voted this time.")
    post_message(user, recap)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"## E2E {audit['audit_id']}\n\n```\n{json.dumps(decision, indent=2)}\n```\n")
    return 0 if decision.get("decision") in ("approved", "rejected") else 1


if __name__ == "__main__":
    sys.exit(main())
