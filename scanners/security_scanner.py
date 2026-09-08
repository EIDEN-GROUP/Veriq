"""Security runner: deterministic scanners first; NIM only adds context, never overrides."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from agent.redact import redact_text

# Fast built-in secret/injection heuristics (always run, no binary needed).
_SECRET_RX = re.compile(r"(?i)(sk-[A-Za-z0-9\-_]{8,}|xox[baprs]-|gh[pousr]_|-----BEGIN .*PRIVATE KEY-----|api[_-]?key\s*[:=])")
_RISK_RX = [
    ("sql-injection", re.compile(r"(f['\"].*SELECT|format\(.*SELECT|%\s*.*WHERE|execute\s*\(\s*f['\"])")),
    ("xss", re.compile(r"(dangerouslySetInnerHTML|innerHTML\s*=|document\.write)")),
    ("command-injection", re.compile(r"(os\.system|subprocess\.(run|call|Popen)\s*\(.*shell\s*=\s*True|child_process\.exec)")),
    ("ssrf", re.compile(r"(requests\.(get|post)\s*\(.*\+|fetch\s*\(.*\+)")),
    ("insecure-deser", re.compile(r"(pickle\.loads|yaml\.load\s*\([^,)]*\)|eval\s*\()")),
]


def _run(cmd: str, root: Path, timeout: int = 180) -> dict:
    try:
        p = subprocess.run(cmd, shell=True, cwd=root, capture_output=True, text=True, timeout=timeout)
        return {"command": cmd, "exit": p.returncode, "output": redact_text((p.stdout + p.stderr)[-4000:])}
    except Exception as e:
        return {"command": cmd, "exit": 127, "output": f"unavailable: {e}"}


def run_security_scans(root: Path, project: dict) -> dict:
    out: dict = {"findings": [], "scanners": []}
    # 1. Built-in heuristics (no deps)
    for path in list(root.rglob("*.py")) + list(root.rglob("*.ts")) + list(root.rglob("*.js")):
        if "node_modules" in path.parts or ".git" in path.parts or path.stat().st_size > 300_000:
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        if _SECRET_RX.search(text):
            out["findings"].append({"rule": "hardcoded-secret", "file": str(path.relative_to(root)), "severity": "HIGH"})
        for name, rx in _RISK_RX:
            m = rx.search(text)
            if m:
                out["findings"].append({"rule": name, "file": str(path.relative_to(root)),
                                        "severity": "MEDIUM", "match": redact_text(m.group(0)[:120])})
                break
    # 2. Native scanners when present (never fatal if missing)
    if "python" in project.get("languages", []):
        out["scanners"].append(_run("python -m bandit -q -r . -f txt", root))
    out["scanners"].append(_run("gitleaks detect --no-git -v", root))
    crit = sum(1 for f in out["findings"] if f["severity"] == "HIGH" and f["rule"] == "hardcoded-secret")
    failed_scanner = any(s["exit"] not in (0, 1, 127) for s in out["scanners"])
    out["status"] = "fail" if crit or failed_scanner else "pass"
    return out
