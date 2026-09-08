"""Veriq secret-redaction layer. Runs BEFORE any evidence goes to NIM, Slack, logs, or artifacts."""
from __future__ import annotations

import os
import re
from pathlib import Path

REDACTED = "[REDACTED_SECRET]"

# Generic credential shapes — deliberately broad; false positives beat leaks.
_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"sk-[A-Za-z0-9\-_]{8,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9\-_]+"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"nvapi-[A-Za-z0-9\-_]{8,}"),
    re.compile(r"(?i)(api[_-]?key|secret|token|password|passwd|pwd)\s*[:=]\s*['\"]?([^'\"\s;,}]{6,})['\"]?"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9\-._~+/]+=*"),
    re.compile(r"(?i)aws_(secret_access_key|session_token)\s*[:=]\s*\S+"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
]

_ENV_FILE_NAMES = {".env", ".env.local", ".env.production", ".env.development"}
_SECRET_PATH_HINTS = ("secret", "credential", "token", ".pem", ".key", ".env")


def redact_text(text: str) -> str:
    """Redact credential-like substrings. Idempotent and never raises."""
    if not text:
        return text
    redacted = text
    for pat in _PATTERNS:
        # Pattern 5 captures `key=value`; keep the key name, redact the value.
        if pat.pattern.startswith("(?i)(api"):
            redacted = pat.sub(lambda m: f"{m.group(1)}={REDACTED}", redacted)
        else:
            redacted = pat.sub(REDACTED, redacted)
    # Redact exact values of known secret env vars if they appear verbatim.
    for name in ("NIM_API_KEY", "SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET",
                 "GITHUB_TOKEN", "GH_TOKEN", "DATABASE_URL", "AWS_SECRET_ACCESS_KEY"):
        val = os.environ.get(name)
        if val and len(val) >= 6 and val in redacted:
            redacted = redacted.replace(val, REDACTED)
    return redacted


def should_exclude_path(path: str) -> bool:
    p = path.replace("\\", "/").lower()
    if Path(p).name in _ENV_FILE_NAMES:
        return True
    return any(h in p for h in _SECRET_PATH_HINTS)


def redact_evidence_blob(blob: object) -> object:
    """Recursively redact dicts/lists/strings (evidence payloads)."""
    if isinstance(blob, str):
        return redact_text(blob)
    if isinstance(blob, dict):
        return {k: (REDACTED if _looks_like_secret_key(str(k)) else redact_evidence_blob(v))
                for k, v in blob.items()}
    if isinstance(blob, (list, tuple)):
        return [redact_evidence_blob(v) for v in blob]
    return blob


def _looks_like_secret_key(key: str) -> bool:
    k = key.lower()
    return any(s in k for s in ("api_key", "apikey", "secret", "token", "password", "private_key", "signing"))
