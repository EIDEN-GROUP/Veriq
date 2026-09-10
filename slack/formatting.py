"""Slack message formatting: premium Block Kit design.

Every message has full blocks + a compact plaintext fallback (push preview).
Approval states (pending / approved / rejected / expired) are BLOCKS on the same
message so a click can rewrite the message in place instead of spamming DMs.
"""
from __future__ import annotations

import datetime

SEV_LINE = ("🔴 Critical {CRITICAL}  🟠 High {HIGH}  🟡 Medium {MEDIUM}  "
            "🔵 Low {LOW}  ⚪ Info {INFO}")
_ALL_SEVS = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "INFO": 0}


def _sevline(s: dict | None) -> str:
    return SEV_LINE.format(**{**_ALL_SEVS, **(s or {})})


def _fmt_status(value: str) -> str:
    return {"pass": "✅ pass", "fail": "❌ fail", "issues": "⚠ issues",
            "unavailable": "⛔ unavailable", "skipped": "➖ skipped",
            "pending": "⏳ pending"}.get(str(value), str(value))


def _context_line(audit: dict) -> str:
    bits = [f"*{audit['repository']}*", f"`{audit.get('branch') or '—'}`",
            f"`{(audit.get('commit') or '')[:7]}`"]
    if audit.get("pr_number"):
        bits.append(f"PR *#{audit['pr_number']}*")
    bits.append(f"by *{audit.get('triggered_by') or '?'}*")
    bits.append(f"`{audit['audit_id']}`")
    return "  ·  ".join(bits)


def approval_blocks(audit: dict, timeout_minutes: int = 30) -> list:
    s = audit["severity_counts"]
    fixable = [f for f in audit.get("findings", [])
               if f.get("auto_fixable") and not f.get("needs_human_review")][:3]
    fe = audit.get("frontend", {}) or {}
    lines = [f"## 👾  Veriq engineering audit", f"```{audit['audit_id']}``",
             f":brain:  Overall score  *{audit['overall_score']}/100*",
             _sevline(s)]
    results = (f"🧪 Tests {_fmt_status(audit.get('tests',{}).get('status'))}   "
               f"🏗️ Build {_fmt_status(audit.get('build',{}).get('status'))}   "
               f"🛡️ Security {_fmt_status(audit.get('security',{}).get('status'))}")
    if fe.get("status") not in (None, "skipped"):
        results += f"   🎨 UI {_fmt_status(fe.get('status'))}"
    lines.append(results)
    if fixable:
        listing = "\n".join(f"• `{f['id']}`  {f['title']}" for f in fixable)
        lines.append(f":wrench:  *{audit['fixes']['identified']} fix(s) the AI can apply "
                     f"automatically, pending your approval*\n{listing}")
    else:
        lines.append(":wrench:  No auto-fixable findings — this is an approval-flow test message."
                     if audit.get("_e2e") else
                     ":wrench:  Nothing the AI considers safe to auto-fix.")
    lines.append(":lock:  Nothing is touched until you click. Only you (or the admin) can authorize.")
    return [
        {"type": "header", "text": {"type": "plain_text",
         "text": "👾  Veriq — AI Engineering Audit", "emoji": True}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": _context_line(audit)}]},
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines[1:5])}},
        {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines[5:7])}},
        {"type": "actions", "block_id": f"veriq:{audit['audit_id']}", "elements": [
            {"type": "button", "action_id": f"approve:{audit['audit_id']}",
             "style": "primary",
             "text": {"type": "plain_text", "text": "🟢  Allow AI to fix", "emoji": True},
             "value": f"{audit['repository']}|{audit['commit']}|{audit.get('pr_number')}"},
            {"type": "button", "action_id": f"reject:{audit['audit_id']}",
             "style": "danger",
             "text": {"type": "plain_text", "text": "🔴  Do not allow", "emoji": True},
             "value": f"{audit['repository']}|{audit['commit']}|{audit.get('pr_number')}"}]},
        {"type": "context", "elements": [{"type": "mrkdwn",
         "text": f":stopwatch:  decision window *{timeout_minutes} min* · signed & audit-bound · "
                 "👾 edits only ever run as a result of your click"}]},
    ]


def _state_blocks(audit_id: str, emoji: str, title: str, detail: str,
                  context: str, extra: list | None = None) -> list:
    blocks = [
        {"type": "header", "text": {"type": "plain_text",
         "text": f"{emoji}  {title}", "emoji": True}},
        {"type": "section", "text": {"type": "mrkdwn", "text": detail}},
    ]
    blocks += extra or []
    blocks.append({"type": "divider"})
    blocks.append({"type": "context",
                   "elements": [{"type": "mrkdwn", "text": f"`{audit_id}`  ·  {context}"}]})
    return blocks


def approved_state_blocks(audit_id: str, approver: str, repo: str = "",
                          count: int = 0) -> list:
    """Rendered by the gateway INTO the approval message the instant 🟢 is clicked."""
    who = f"*@{approver}*" if approver else "An authorized user"
    detail = f":white_check_mark:  {who} approved the AI fixes"
    if repo:
        detail += f"  ·  <https://github.com/{repo}|`{repo}`>"
    return _state_blocks(
        audit_id, "🟢", "Approved — AI is on it",
        detail,
        f"verification (tests · build · security · browser) runs now — "
        f"this message gets the outcome")


def rejected_state_blocks(audit_id: str, approver: str) -> list:
    who = f"*@{approver}*" if approver else "An authorized user"
    return _state_blocks(audit_id, "🔴", "Fix declined",
                         f":no_entry:  {who} declined automatic fixes. "
                         f"*No code was modified.* The audit report stands on its own.",
                         "human review of the findings is recommended")


def expired_state_blocks(audit_id: str) -> list:
    return _state_blocks(audit_id, "⏱️", "Approval window closed",
                         ":hourglass_flowing_sand:  Nobody responded in time — "
                         "*no modifications were made.*",
                         "re-run the audit to ask again")


def working_blocks(audit: dict) -> list:
    """Action updates the SAME message right before the fix loop starts."""
    fix_lines = "\n".join(f"• `{f['id']}`  {f['title']}"
                          for f in audit.get("findings", [])
                          if f.get("auto_fixable") and not f.get("needs_human_review"))[:2500]
    return _state_blocks(
        audit["audit_id"], "🔧", "AI applying fixes…",
        f"*Editing on bot branch* `ai-agent/fix-{str(audit['commit'])[:7]}…`, then re-running "
        f"fmt → lint → typecheck → tests → security → build"
        + (" → Playwright" if (audit.get('frontend') or {}).get('status') not in (None,'skipped','unavailable') else "")
        + f".\n\n{fix_lines}",
        "hold on — outcome lands in this message")


def final_result_blocks(audit: dict, run_url: str = "") -> list:
    fx = audit["fixes"]
    if fx.get("fixed"):
        titles = {f["id"]: f.get("title", "") for f in audit.get("findings", [])}
        lines = [f":white_check_mark:  `{fid}`  {titles.get(fid, '')}"
                 for fid in fx.get("done", [])][:10]
        body = (":mega:  *Fix outcome — every item verified with fresh tool output*\n"
                + ("\n".join(lines) if lines else f":white_check_mark:  {fx['fixed']} fix(es) verified")
                + ("\n:x:  " + "; ".join(fx.get("failed", [])[:3]) if fx.get("failed") else ""))
    elif audit["approval"].get("decision") == "rejected":
        body = ":no_entry:  You declined — code untouched, findings for the human backlog below."
    elif audit["approval"].get("decision") == "expired":
        body = ":hourglass_flowing_sand:  Approval window closed — code untouched."
    else:
        body = ":information_source:  No approval request was needed for this audit."
    s = audit["severity_counts"]
    header = (":shield:  Veriq audit complete" if audit["approval"].get("decision") != "approved"
              else ":green_circle:  Veriq — fixes delivered")
    score_emoji = "🟩" if audit["overall_score"] >= 85 else ("🟨" if audit["overall_score"] >= 70 else "🟥")
    return _state_blocks(audit["audit_id"], header.split(" ")[0],
                         f"{header} — {score_emoji} {audit['overall_score']}/100",
                         f"{body}\n\n{_sevline(s)}\n"
                         f"🧪 `{_fmt_status(audit['tests'].get('status'))}`   "
                         f"🛡️ `{_fmt_status(audit['security'].get('status'))}`   "
                         f"🎨 `{_fmt_status((audit.get('frontend') or {}).get('status'))}`"
                         + (f"\n:link:  <{run_url}|full evidence + artifacts>" if run_url else ""),
                         "audit record kept in GitHub Actions artifacts")


def dev_text(audit: dict) -> str:
    ap = audit["approval"].get("decision")
    head = {"approved": "🟢 approved · fixes applied", "rejected": "🔴 rejected · untouched",
            "expired": "⏱️ expired · untouched", "advisory-only": "ℹ️ advisory only",
            }.get(ap, "")
    return (f"👾 Veriq {audit['audit_id']} — {audit['overall_score']}/100"
            + (f"\n{head}" if head else "") + "\n" + _sevline(audit["severity_counts"]))


def admin_text(audit: dict, run_url: str = "") -> str:
    f = audit["fixes"]
    s = audit["severity_counts"]
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%H:%M UTC")
    return "\n".join([
        f":shield:  *Veriq admin audit*  ·  `{audit['audit_id']}`  ·  {ts}",
        f"*{audit['repository']}* `{(audit.get('commit') or '')[:7]}` ({audit.get('branch')}) "
        f"by *{audit['triggered_by']}* → {audit.get('slack_user') or ':warning: unmapped — no dev DM'}",
        f"Score *{audit['overall_score']}/100* — {_sevline(s)}",
        f"🧪 `{_fmt_status(audit['tests'].get('status'))}`  🏗️ `{_fmt_status(audit['build'].get('status'))}`  "
        f"🛡️ `{_fmt_status(audit['security'].get('status'))}`  "
        f"🎨 `{_fmt_status((audit.get('frontend') or {}).get('status'))}`",
        f"🔧 approval: `{audit['approval'].get('decision')}`  ·  fixed & verified: "
        f"*{f.get('fixed',0)}*  ·  advisory-only (below bar): {len(f.get('advisory', []))}",
        f":link: <{run_url}|run & full evidence>" if run_url else ":link: see GitHub Actions artifacts",
    ])


def admin_blocks(audit: dict, run_url: str = "") -> list:
    f = audit["fixes"]
    s = audit["severity_counts"]
    score_emoji = "🟩" if audit["overall_score"] >= 85 else ("🟨" if audit["overall_score"] >= 70 else "🟥")
    dec = audit["approval"].get("decision")
    dec_emoji = {"approved": "🟢", "rejected": "🔴", "expired": "⏱️",
                 "advisory-only": "ℹ️", "none": "➖", "none-requested": "➖"}.get(dec, "❓")
    return [
        {"type": "header", "text": {"type": "plain_text",
         "text": f":shield:  Veriq ADMIN AUDIT  ·  {score_emoji} {audit['overall_score']}/100",
         "emoji": True}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": _context_line(audit)}]},
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text":
            f"{_sevline(s)}\n"
            f"🧪 {_fmt_status(audit['tests'].get('status'))}   "
            f"🏗️ {_fmt_status(audit['build'].get('status'))}   "
            f"🛡️ {_fmt_status(audit['security'].get('status'))}   "
            f"🎨 {_fmt_status((audit.get('frontend') or {}).get('status'))}   "
            f"♿ {_fmt_status((audit.get('frontend') or {}).get('a11y_status', 'skipped'))}\n"
            f"🤝 decision: {dec_emoji} `{dec}`   ·   ✅ verified fixes: *{f.get('fixed',0)}*   ·   "
            f"📎 advisory: {len(f.get('advisory', []))}"}},
        {"type": "actions", "elements": [
            {"type": "button", "action_id": "noop:ack",
             "text": {"type": "plain_text", "text": "📂  Open run & artifacts"},
             "url": run_url or f"https://github.com/{audit['repository']}/actions"}]},
    ]


def final_dev_blocks(audit: dict, run_url: str = "") -> list:
    return final_result_blocks(audit, run_url)


# back-compat aliases used by orchestrator/notifications/e2e
final_dev_text = dev_text
