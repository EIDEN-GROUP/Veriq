"""Slack message formatting: dev audit, approval request (two-button), admin audit."""
from __future__ import annotations


def approval_blocks(audit: dict) -> list:
    s = audit["severity_counts"]
    t = audit["tests"]
    fixable = audit["fixes"]["identified"]
    return [
        {"type": "section", "text": {"type": "mrkdwn",
         "text": f"🤖 *AI Engineering Audit*\n*Repo:* {audit['repository']}  *PR:* #{audit.get('pr_number') or '—'}  "
                 f"*By:* {audit['triggered_by']}\n*Score:* {audit['overall_score']}/100  *Audit:* `{audit['audit_id']}`\n"
                 f"🔴 Critical: {s['CRITICAL']}  🟠 High: {s['HIGH']}  🟡 Medium: {s['MEDIUM']}  🔵 Low: {s['LOW']}\n"
                 f"Tests: {t.get('status','?')}  Build: {audit['build'].get('status','?')}\n"
                 f"The AI identified *{fixable}* issue(s) it believes can be safely fixed automatically.\n"
                 f"Do you want the AI agent to fix them?"}},
        {"type": "actions", "block_id": f"veriq:{audit['audit_id']}", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "🟢 Allow AI to fix"},
             "style": "primary", "action_id": f"approve:{audit['audit_id']}",
             "value": f"{audit['repository']}|{audit['commit']}|{audit.get('pr_number')}"},
            {"type": "button", "text": {"type": "plain_text", "text": "🔴 Do not allow AI to fix"},
             "style": "danger", "action_id": f"reject:{audit['audit_id']}",
             "value": f"{audit['repository']}|{audit['commit']}|{audit.get('pr_number')}"}]},
    ]


def final_dev_text(audit: dict) -> str:
    ap = audit["approval"].get("decision")
    if ap == "approved":
        head = "🟢 AI fixes approved\n"
    elif ap == "rejected":
        head = "🔴 AI fixes rejected — no code was modified.\n"
    elif ap == "expired":
        head = "⏱️ Approval expired. No modifications were made.\n"
    else:
        head = ""
    lines = [head + f"🤖 Veriq final audit `{audit['audit_id']}` — {audit['overall_score']}/100"]
    for f in audit["findings"][:15]:
        lines.append(f"• [{f.get('severity')}] {f.get('title')} ({f.get('file','')})")
    if audit["fixes"]["fixed"]:
        lines.append(f"✅ Fixed & verified: {audit['fixes']['fixed']}")
    if audit["fixes"]["failed"]:
        lines.append("❌ " + "; ".join(audit["fixes"]["failed"][:3]))
    return "\n".join(lines)


def admin_text(audit: dict, run_url: str = "") -> str:
    s = audit["severity_counts"]
    return ("\n".join([
        "🤖 AI Engineering Agent — ADMIN AUDIT", f"Repository: {audit['repository']}",
        f"Developer: {audit['triggered_by']} (slack: {audit.get('slack_user') or 'unmapped'})",
        f"Audit: {audit['audit_id']}  Overall: {audit['overall_score']}/100",
        f"Findings: Critical {s['CRITICAL']} High {s['HIGH']} Medium {s['MEDIUM']} Low {s['LOW']}",
        f"Tests: {audit['tests'].get('status')}  Build: {audit['build'].get('status')}  "
        f"Security: {audit['security'].get('status')}  Frontend: {audit['frontend'].get('status')}",
        f"Fix requested: {'YES' if audit['approval'].get('requested') else 'NO'}  "
        f"Decision: {audit['approval'].get('decision')}",
        f"Changes: {audit['fixes']['fixed']} verified fix(es)",
        f"Advisory suggestions (not fixed, below approval bar): "
        f"{len(audit['fixes'].get('advisory', []))}",
        f"Full audit: {run_url or 'GitHub Actions artifacts'}",
    ]))
