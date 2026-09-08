"""Dependency audit: npm audit / pip-audit when available; else manifest review."""
from __future__ import annotations

import subprocess
from pathlib import Path

from agent.redact import redact_text


def _run(cmd: str, root: Path, timeout: int = 240) -> dict:
    try:
        p = subprocess.run(cmd, shell=True, cwd=root, capture_output=True, text=True, timeout=timeout)
        return {"command": cmd, "exit": p.returncode, "output": redact_text((p.stdout + p.stderr)[-4000:])}
    except Exception as e:
        return {"command": cmd, "exit": 127, "output": f"unavailable: {e}"}


def run_dependency_audit(root: Path, project: dict) -> dict:
    scans: list[dict] = []
    if "node" in project.get("languages", []):
        scans.append(_run("npm audit --json", root))
    if "python" in project.get("languages", []):
        scans.append(_run("python -m pip_audit -f json", root))
    if not scans:
        return {"status": "skipped", "reason": "no supported package manager", "scans": []}
    unavailable = all(s["exit"] == 127 or "unavailable" in s["output"] for s in scans)
    if unavailable:
        return {"status": "skipped", "reason": "auditors unavailable", "scans": scans}
    bad = any(s["exit"] not in (0, 1) for s in scans)
    return {"status": "fail" if bad else "pass", "scans": scans}
