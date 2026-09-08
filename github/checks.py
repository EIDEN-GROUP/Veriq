"""Commit-status checks reporter (best-effort; audit artifacts are authoritative)."""
from __future__ import annotations

import os
import requests


def set_check(repo: str, sha: str, state: str, description: str) -> None:
    tok = os.environ.get("GITHUB_TOKEN")
    if not tok or not repo or not sha:
        return
    try:
        requests.post(f"https://api.github.com/repos/{repo}/statuses/{sha}",
                      headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"},
                      json={"state": state, "context": "veriq/audit", "description": description[:140]},
                      timeout=30)
    except Exception:
        pass
