"""Approval state backends.

- MemoryStore: single-process (VPS / docker-compose). Zero setup.
- UpstashStore:  shared Redis over Upstash REST API (Vercel serverless / multi-replica).
  REQUIRED on serverless: Vercel instances don't share memory, so Slack's POST and the
  Action's poll would hit different dicts. Auto-selected when UPSTASH_REDIS_REST_URL +
  UPSTASH_REDIS_REST_TOKEN are set. No extra dependency (plain urllib).

Key layout:  veriq:pending:<audit_id>   (TTL = approval window)
             veriq:decision:<audit_id>  (TTL = 24h, so late polls still see the outcome)
"""
from __future__ import annotations

import json
import os
import time
import urllib.request

PENDING_PREFIX = "veriq:pending:"
DECISION_PREFIX = "veriq:decision:"
DECISION_TTL_S = 86_400


class MemoryStore:
    def __init__(self) -> None:
        self._pending: dict[str, dict] = {}
        self._decisions: dict[str, dict] = {}

    def set_pending(self, audit_id: str, rec: dict) -> None:
        self._pending[audit_id] = rec

    def get_pending(self, audit_id: str) -> dict | None:
        return self._pending.get(audit_id)

    def set_decision(self, audit_id: str, dec: dict, ttl_s: int = DECISION_TTL_S) -> None:
        self._decisions[audit_id] = dec

    def get_decision(self, audit_id: str) -> dict | None:
        return self._decisions.get(audit_id)

    def clear_decision(self, audit_id: str) -> None:
        self._decisions.pop(audit_id, None)

    def clear_all(self) -> None:
        self._pending.clear()
        self._decisions.clear()


class UpstashStore:
    """Upstash Redis via REST. Each command = one HTTPS POST, no client library needed."""

    def __init__(self, url: str, token: str) -> None:
        self._url = url.rstrip("/")
        self._token = token

    def _cmd(self, *args: object) -> object:
        req = urllib.request.Request(
            self._url, data=json.dumps(list(args)).encode(),
            headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            body = json.loads(r.read().decode())
        if "error" in body:
            raise RuntimeError(f"Upstash error: {body['error']}")
        return body.get("result")

    def set_pending(self, audit_id: str, rec: dict) -> None:
        ttl = max(int(rec.get("expires", time.time() + 1800) - time.time()), 60) + 60
        self._cmd("SET", PENDING_PREFIX + audit_id, json.dumps(rec), "EX", ttl)

    def get_pending(self, audit_id: str) -> dict | None:
        raw = self._cmd("GET", PENDING_PREFIX + audit_id)
        return json.loads(raw) if raw else None

    def set_decision(self, audit_id: str, dec: dict, ttl_s: int = DECISION_TTL_S) -> None:
        self._cmd("SET", DECISION_PREFIX + audit_id, json.dumps(dec), "EX", ttl_s)

    def get_decision(self, audit_id: str) -> dict | None:
        raw = self._cmd("GET", DECISION_PREFIX + audit_id)
        return json.loads(raw) if raw else None

    def clear_decision(self, audit_id: str) -> None:
        self._cmd("DEL", DECISION_PREFIX + audit_id)

    def clear_all(self) -> None:
        cursor = "0"
        while True:
            res = self._cmd("SCAN", cursor, "MATCH", "veriq:*", "COUNT", 100)
            cursor, keys = str(res[0]), res[1]  # type: ignore
            for k in keys:
                self._cmd("DEL", k)
            if cursor == "0":
                break


def get_store() -> MemoryStore | UpstashStore:
    url = os.environ.get("UPSTASH_REDIS_REST_URL", "")
    token = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")
    if url and token:
        return UpstashStore(url, token)
    return MemoryStore()
