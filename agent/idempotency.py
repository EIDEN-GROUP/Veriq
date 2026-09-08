"""Idempotency: repository+commit+pr+audit-type key prevents duplicate Slack approvals/comments."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def audit_key(repository: str, commit: str, pr_number: int | None, audit_type: str = "full") -> str:
    raw = f"{repository}|{commit}|{pr_number}|{audit_type}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def already_audited(state_dir: Path, key: str) -> bool:
    return (state_dir / f"{key}.json").exists()


def mark_audited(state_dir: Path, key: str, audit_id: str) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / f"{key}.json").write_text(json.dumps({"audit_id": audit_id}))
