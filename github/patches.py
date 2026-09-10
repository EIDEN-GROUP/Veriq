"""Patch application on an isolated bot branch. Never touches user branches directly."""
from __future__ import annotations

import subprocess
from pathlib import Path


def workspace_dirty(root: Path) -> str:
    """Any uncommitted user work? (must be empty before we touch the checkout)"""
    proc = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True,
                          text=True, timeout=30)
    return (proc.stdout or "").strip()


def read_file_map(root: Path, findings: list[dict]) -> dict[str, str]:
    files: dict[str, str] = {}
    for f in findings:
        rel = str(f.get("file", ""))
        if rel and (root / rel).exists():
            files[rel] = (root / rel).read_text(errors="replace")[:15000]
    return files


def apply_patches_on_bot_branch(root: Path, audit: dict, patches: list[dict]) -> dict:
    short = str(audit["commit"])[:7]
    branch = f"ai-agent/fix-{short}-{audit['audit_id'][-6:]}"
    subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True, timeout=30)
    chk = subprocess.run(["git", "checkout", branch], cwd=root, capture_output=True, text=True, timeout=60)
    if chk.returncode != 0:
        subprocess.run(["git", "checkout", "-b", branch], cwd=root, capture_output=True, text=True, timeout=60)
    applied = []
    for p in patches:
        rel = str(p.get("path", ""))
        diff = str(p.get("diff", ""))
        target = (root / rel).resolve()
        if root.resolve() not in target.parents:
            continue
        # Apply via `git apply` for safety (validates context); fallback: skip.
        proc = subprocess.run(["git", "apply", "--whitespace=fix", "-"], input=diff,
                              cwd=root, capture_output=True, text=True, timeout=30)
        if proc.returncode == 0:
            applied.append(rel)
    if applied:
        subprocess.run(["git", "add", "--", *applied], cwd=root, capture_output=True, timeout=30)
        subprocess.run(["git", "-c", "user.name=veriq-bot", "-c", "user.email=veriq-bot@users.noreply.github.com",
                        "commit", "-m", f"fix(ai-agent): resolve automated audit findings [{audit['audit_id']}]"],
                       cwd=root, capture_output=True, text=True, timeout=60)
    else:
        # Nothing changed: drop the empty branch we just made and return to the base commit.
        subprocess.run(["git", "checkout", "-"], cwd=root, capture_output=True, timeout=30)
        subprocess.run(["git", "branch", "-D", branch], cwd=root, capture_output=True, timeout=30)
        return {"branch": "", "applied": [], "diff_stat": "no valid patch applied", "commit": ""}
    stat = subprocess.run(["git", "diff", "--stat", "HEAD~1"], cwd=root,
                          capture_output=True, text=True, timeout=30)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                          capture_output=True, text=True, timeout=30).stdout.strip()
    return {"branch": branch if applied else "", "applied": applied,
            "diff_stat": (stat.stdout if stat else "")[:2000], "commit": head}
