"""Approval + chat state backends.

- MemoryStore: single-process (VPS / docker-compose). Zero setup.
- UpstashStore: shared Redis over the Upstash REST API (Vercel serverless / multi-replica).
  REQUIRED on serverless: Vercel instances don't share memory. Auto-selected when
  UPSTASH_REDIS_REST_URL + UPSTASH_REDIS_REST_TOKEN are set. No extra dependency.

Key layout:  veriq:pending:<audit_id>    (TTL = approval window)
             veriq:decision:<audit_id>   (TTL = 24h)
             veriq:mem:<user_id>         (rollin transcript + long-term summary +
                                          facts — 👾 persistent memory, TTL 90 days)
             veriq:result:<repo>         (latest audit summary, TTL 90 days)
             veriq:hist:<repo>           (audit history, last 30, TTL 90 days)
             veriq:evt:<event_id>        (Slack events-API dedupe, TTL 1h)
"""
from __future__ import annotations

import json
import os
import time
import urllib.request

PENDING_PREFIX = "veriq:pending:"
DECISION_PREFIX = "veriq:decision:"
MEM_PREFIX = "veriq:mem:"
RESULT_PREFIX = "veriq:result:"
HIST_PREFIX = "veriq:hist:"
EVENT_PREFIX = "veriq:evt:"
DECISION_TTL_S = 86_400
MEM_TTL_S = 90 * 86_400
RESULT_TTL_S = 90 * 86_400
EVENT_TTL_S = 3_600
HIST_MAX = 30
_TRANSCRIPT_CAP = 60


def _empty_mem() -> dict:
    return {"chat": [], "summary": "", "facts": []}


class MemoryStore:
    def __init__(self) -> None:
        self._pending: dict[str, dict] = {}
        self._decisions: dict[str, dict] = {}
        self._mem: dict[str, tuple[dict, float]] = {}
        self._results: list[dict] = []
        self._latest: dict[str, dict] = {}
        self._hist: dict[str, list] = {}
        self._seen_events: dict[str, float] = {}

    # ---- approvals ----
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

    # ---- results reported by CI ----
    def add_result(self, record: dict) -> None:
        self._results.insert(0, record)
        del self._results[100:]
        self._latest[record["repository"]] = record
        hist = self._hist.setdefault(record["repository"], [])
        hist.insert(0, record)
        del hist[HIST_MAX:]

    def recent_results(self, limit: int = 10) -> list[dict]:
        return list(self._results[:limit])

    def latest_result(self, repo: str) -> dict | None:
        rec = self._latest.get(repo)
        return rec if rec and time.time() - rec.get("reported_at", 0) < RESULT_TTL_S else None

    def repo_history(self, repo: str, n: int = 5) -> list[dict]:
        return self._hist.get(repo, [])[:n]

    # ---- persistent chat memory ----
    def memory(self, user_id: str) -> dict:
        item = self._mem.get(user_id)
        if not item or time.time() > item[1]:
            return _empty_mem()
        return item[0]

    def set_memory(self, user_id: str, mem: dict) -> None:
        mem["chat"] = mem.get("chat", [])[_TRANSCRIPT_CAP * -1:]
        self._mem[user_id] = (mem, time.time() + MEM_TTL_S)

    def chat(self, user_id: str) -> list[dict]:
        return list(self.memory(user_id).get("chat", []))

    def remember(self, user_id: str, messages: list[dict]) -> None:
        mem = self.memory(user_id)
        mem["chat"] = (mem.get("chat", []) + messages)[_TRANSCRIPT_CAP * -1:]
        self.set_memory(user_id, mem)

    def forget(self, user_id: str) -> None:
        self._mem.pop(user_id, None)

    # ---- Slack events dedupe ----
    def event_seen(self, event_id: str) -> bool:
        if not event_id:
            return False
        now = time.time()
        exp = self._seen_events.get(event_id)
        if exp and now < exp:
            return True
        self._seen_events[event_id] = now + EVENT_TTL_S
        return False

    def clear_all(self) -> None:
        self._pending.clear()
        self._decisions.clear()
        self._mem.clear()
        self._results.clear()
        self._latest.clear()
        self._hist.clear()
        self._seen_events.clear()


class UpstashStore:
    """Upstash Redis via REST. Each command = one HTTPS POST, no client library."""

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

    # ---- approvals ----
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

    # ---- results ----
    def add_result(self, record: dict) -> None:
        repo = record["repository"].replace("/", "+")
        self._cmd("SET", RESULT_PREFIX + repo, json.dumps(record), "EX", RESULT_TTL_S)
        self._cmd("LPUSH", f"{HIST_PREFIX}{repo}", json.dumps(record))
        self._cmd("LTRIM", f"{HIST_PREFIX}{repo}", 0, HIST_MAX - 1)
        self._cmd("EXPIRE", f"{HIST_PREFIX}{repo}", RESULT_TTL_S)

    def recent_results(self, limit: int = 10) -> list[dict]:
        repos = self._cmd("KEYS", RESULT_PREFIX + "*") or []
        out = []
        for k in repos:
            raw = self._cmd("GET", k)
            if raw:
                try:
                    out.append(json.loads(raw))
                except ValueError:
                    pass
        out.sort(key=lambda r: r.get("reported_at", 0), reverse=True)
        return out[:limit]

    def latest_result(self, repo: str) -> dict | None:
        raw = self._cmd("GET", RESULT_PREFIX + repo.replace("/", "+"))
        return json.loads(raw) if raw else None

    def repo_history(self, repo: str, n: int = 5) -> list[dict]:
        raw = self._cmd("LRANGE", f"{HIST_PREFIX}{repo.replace('/', '+')}", 0, n - 1) or []
        out = []
        for item in raw:
            try:
                out.append(json.loads(item))
            except ValueError:
                continue
        return out

    # ---- persistent chat memory ----
    def memory(self, user_id: str) -> dict:
        raw = self._cmd("GET", MEM_PREFIX + user_id)
        if not raw:
            return _empty_mem()
        try:
            mem = json.loads(raw)
            return {**_empty_mem(), **mem}
        except ValueError:
            return _empty_mem()

    def set_memory(self, user_id: str, mem: dict) -> None:
        mem["chat"] = mem.get("chat", [])[_TRANSCRIPT_CAP * -1:]
        self._cmd("SET", MEM_PREFIX + user_id, json.dumps(mem), "EX", MEM_TTL_S)

    def chat(self, user_id: str) -> list[dict]:
        return list(self.memory(user_id).get("chat", []))

    def remember(self, user_id: str, messages: list[dict]) -> None:
        mem = self.memory(user_id)
        mem["chat"] = (mem.get("chat", []) + messages)[_TRANSCRIPT_CAP * -1:]
        self.set_memory(user_id, mem)

    def forget(self, user_id: str) -> None:
        self._cmd("DEL", MEM_PREFIX + user_id)

    # ---- events dedupe (atomic SET NX EX) ----
    def event_seen(self, event_id: str) -> bool:
        if not event_id:
            return False
        first = self._cmd("SET", EVENT_PREFIX + event_id, "1", "EX", EVENT_TTL_S, "NX")
        return first != "OK"  # None/0 => key existed => duplicate

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
