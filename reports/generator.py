"""Audit artifact writer: audit.json (schema) + audit.md (human) — secrets never written."""
from __future__ import annotations

import json
from pathlib import Path

from agent.redact import redact_text


def write_audit_artifacts(audit: dict, artifacts: Path) -> list[str]:
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "audit.json").write_text(json.dumps(audit, indent=2, default=str))
    (artifacts / "audit.md").write_text(to_markdown(audit))
    return ["audit.json", "audit.md"]


def to_markdown(a: dict) -> str:
    s = a["severity_counts"]
    lines = [
        "# AI ENGINEERING AUDIT", f"Repository: {a['repository']}", f"Commit: {a['commit']}",
        f"Triggered by: {a['triggered_by']}", f"Audit ID: {a['audit_id']}",
        f"Overall: {a['overall_score']}/100", "",
        f"CODE: {sum(1 for f in a['findings'] if f.get('category')=='code')} findings",
        f"SECURITY: critical {s['CRITICAL']} high {s['HIGH']} medium {s['MEDIUM']}",
        f"TESTS: {a['tests'].get('status')}  BUILD: {a['build'].get('status')}",
        f"FRONTEND: {a['frontend'].get('status')}",
        f"AUTOMATIC FIXES: identified {a['fixes']['identified']}, approved {a['fixes'].get('approved',0)}, "
        f"fixed {a['fixes'].get('fixed',0)}",
        f"APPROVAL: {a['approval'].get('decision')}",
        "", "## Findings",
    ]
    for f in a["findings"]:
        lines.append(f"### [{f.get('severity')}] {redact_text(str(f.get('title')))} `{f.get('id')}`")
        lines.append(f"{redact_text(str(f.get('description',''))[:800])}")
        lines.append(f"File: `{f.get('file','')}` line {f.get('line')} · confidence {f.get('confidence')}")
    return "\n".join(lines)
