"""Test + build runners: detect declared scripts/commands; report, never invent."""
from __future__ import annotations

import subprocess
from pathlib import Path

from agent.redact import redact_text


def _run(cmd: str, root: Path, timeout: int) -> dict:
    try:
        p = subprocess.run(cmd, shell=True, cwd=root, capture_output=True, text=True, timeout=timeout)
        tail = redact_text((p.stdout + p.stderr)[-6000:])
        return {"command": cmd, "exit": p.returncode, "output": tail,
                "passed": p.returncode == 0}
    except subprocess.TimeoutExpired:
        return {"command": cmd, "exit": 124, "output": "timeout", "passed": False}


def run_tests(root: Path, project: dict) -> dict:
    scripts: dict = project.get("scripts", {})
    cmd = None
    if "node" in project.get("languages", []):
        for k in ("test:ci", "test", "pytest", "vitest"):
            if k in scripts:
                cmd = f"npm run {k} --silent"
                break
    if cmd is None and "python" in project.get("languages", []):
        cmd = "python -m pytest -q" if (root / "tests").exists() or (root / "test").exists() else None
    if cmd is None:
        return {"status": "skipped", "reason": "no test command declared", "passed": 0, "failed": 0}
    r = _run(cmd, root, 600)
    return {"status": "pass" if r["passed"] else "fail", **r, "passed_count": None}


def run_build(root: Path, project: dict) -> dict:
    scripts: dict = project.get("scripts", {})
    if "node" in project.get("languages", []) and "build" in scripts:
        r = _run("npm run build --silent", root, 600)
        return {"status": "pass" if r["passed"] else "fail", **r}
    return {"status": "skipped", "reason": "no build declared"}
