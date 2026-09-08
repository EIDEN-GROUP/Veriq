"""GitHub API helpers: PR comments (create-or-update single bot comment), commit status."""
from __future__ import annotations

import os
import requests
from agent.redact import redact_text

BOT_MARKER = "<!-- veriq-bot -->"


def _headers() -> dict:
    tok = os.environ.get("GITHUB_TOKEN", "")
    return {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"} if tok else {}


def upsert_bot_comment(repo: str, pr_number: int | None, body: str) -> bool:
    if not pr_number or not os.environ.get("GITHUB_TOKEN"):
        return False
    base = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"
    try:
        existing = requests.get(base, headers=_headers(), timeout=30).json()
        mine = [c for c in existing if isinstance(c, dict) and BOT_MARKER in str(c.get("body", ""))]
        payload = {"body": f"{BOT_MARKER}\n{body}"}
        if mine:
            requests.patch(mine[0]["url"], headers=_headers(), json=payload, timeout=30)
        else:
            requests.post(base, headers=_headers(), json=payload, timeout=30)
        return True
    except Exception:
        return False


def comment_body(audit: dict) -> str:
    s = audit["severity_counts"]
    return (f"## Veriq audit `{audit['audit_id']}` — {audit['overall_score']}/100\n"
            f"Critical {s['CRITICAL']} · High {s['HIGH']} · Medium {s['MEDIUM']} · Low {s['LOW']}\n"
            f"Fixes: {audit['fixes']['fixed']}/{audit['fixes']['approved']} verified · "
            f"Approval: {redact_text(str(audit['approval'].get('decision')))}\n")
