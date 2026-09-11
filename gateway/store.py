"""Approval state backends.

- MemoryStore: single-process (VPS / docker-compose). Zero setup.
- UpstashStore:  shared Redis over Upstash REST API (Vercel serverless / multi-replica).
  REQUIRED on serverless: Vercel instances don't share memory, so Slack's POST and the
  Action's poll would hit different dicts. Auto-selected when UPSTASH_REDIS_REST_URL +
  UPSTASH_REDIS_REST_TOKEN are set. No extra dependency (plain urllib).

Key layout:  veriq:pending:<audit_id>   (TTL = approval window)
             veriq:decision:<audit_id>  (TTL = 24h, so late polls still see the outcome)
             veriq:chat:<user_id>       (rolling chat context, TTL 2h)
             veriq:result:<repo>        + veriq:results (recent audits reported by CI, TTL 7d)
"""
from __future__ import annotations

import json
import os
import time
import urllib.request

PENDING_PREFIX = "veriq:pending:"
DECISION_PREFIX = "veriq:decision:"
CHAT_PREFIX = "veriq:chat:"
RESULT_PREFIX = "veriq:result:"
RESULTS_LIST = "veriq:results"
DECISION_TTL_S = 86_400
CHAT_TTL_S = 7_200
RESULT_TTL_S = 7 * 86_400


class MemoryStore:
    def __init__(self) -> None:
        self._pending: dict[str, dict] = {}
        self._decisions: dict[str, dict] = {}
        self._chat: dict[str, tuple[list, float]] = {}
        self._results: list[dict] = []
        self._latest: dict[str, dict] = {}

    # ---- chat context (rolling per-user, TTL) ----
    def chat(self, user_id: str) -> list[dict]:
        item = self._chat.get(user_id)
        if not item or time.time() > item[1]:
            return []
        return item[0]

    def remember(self, user_id: str, messages: list[dict]) -> None:
        self._chat[user_id] = (messages[-24:], time.time() + CHAT_TTL_S)

    def forget(self, user_id: str) -> None:
        self._chat.pop(user_id, None)

    # ---- audit results reported by CI ----
    def add_result(self, record: dict) -> None:
        self._results.insert(0, record)
        del self._results[100:]
        self._latest[record["repository"]] = record

    def recent_results(self, limit: int = 10) -> list[dict]:
        return list(self._results[:limit])

    def latest_result(self, repo: str) -> dict | None:
        rec = self._latest.get(repo)
        return rec if rec and time.time() - rec.get("reported_at", 0) < RESULT_TTL_S else None

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
        self._chat.clear()
        self._results.clear()
        self._latest.clear()


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

    # ---- chat / results over Redis (same interface as MemoryStore) ----
    def chat(self, user_id: str) -> list[dict]:
        raw = self._cmd("GET", CHAT_PREFIX + user_id)
        if not raw:
            return []
        try:
            return json.loads(raw)
        except ValueError:
            return []

    def remember(self, user_id: str, messages: list[dict]) -> None:
        self._cmd("SET", CHAT_PREFIX + user_id,
                  json.dumps(messages[-24:]), "EX", CHAT_TTL_S)

    def forget(self, user_id: str) -> None:
        self._cmd("DEL", CHAT_PREFIX + user_id)

    def add_result(self, record: dict) -> None:
        key = RESULT_PREFIX + record["repository"].replace("/", "+")
        self._cmd("SET", key, json.dumps(record), "EX", RESULT_TTL_S)
        self._cmd("LPUSH", RESULTS_LIST, json.dumps(record))
        self._cmd("LTRIM", RESULTS_LIST, 0, 99)
        self._cmd("EXPIRE", RESULTS_LIST, RESULT_TTL_S)

    def recent_results(self, limit: int = 10) -> list[dict]:
        raw = self._cmd("LRANGE", RESULTS_LIST, 0, limit - 1) or []
        out = []
        for item in raw:
            try:
                out.append(json.loads(item))
            except ValueError:
                continue
        return out

    def latest_result(self, repo: str) -> dict | None:
        raw = self._cmd("GET", RESULT_PREFIX + repo.replace("/", "+"))
        return json.loads(raw) if raw else None

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
