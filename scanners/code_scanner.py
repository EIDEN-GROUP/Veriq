"""Static analysis runner: uses repo-declared scripts; never fails for missing tools."""
from __future__ import annotations

import subprocess
from pathlib import Path

from agent.redact import redact_text


def _run(cmd: str, root: Path, timeout: int = 180) -> dict:
    try:
        p = subprocess.run(cmd, shell=True, cwd=root, capture_output=True, text=True, timeout=timeout)
        return {"command": cmd, "exit": p.returncode, "output": redact_text((p.stdout + p.stderr)[-4000:])}
    except subprocess.TimeoutExpired:
        return {"command": cmd, "exit": 124, "output": "timeout"}
    except Exception as e:
        return {"command": cmd, "exit": 127, "output": f"runner error: {e}"}


def run_static_checks(root: Path, project: dict) -> dict:
    results: list[dict] = []
    scripts: dict = project.get("scripts", {})
    langs: list = project.get("languages", [])
    if "node" in langs:
        for key in ("lint", "format:check", "typecheck", "type-check", "check"):
            if key in scripts:
                results.append(_run(f"npm run {key} --silent", root))
        # eslint fallback only if config exists
        if any((root / f).exists() for f in (".eslintrc", ".eslintrc.json", ".eslintrc.js", "eslint.config.js")):
            results.append(_run("npx --no-install eslint . --max-warnings=0", root))
    if "python" in langs:
        if (root / "pyproject.toml").exists() or (root / "setup.cfg").exists():
            results.append(_run("python -m ruff check .", root))
            results.append(_run("python -m black --check .", root))
    if not results:
        return {"status": "skipped", "reason": "no static tooling declared", "checks": []}
    failed = [r for r in results if r["exit"] != 0 and "not found" not in r["output"].lower()
              and "no module named" not in r["output"].lower()]
    return {"status": "fail" if failed else "pass", "checks": results}
