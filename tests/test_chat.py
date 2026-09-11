"""Chat-layer tests: privacy (ephemeral), memory, gating, results pipeline, routing."""
from __future__ import annotations

import os
import time

import pytest

from conftest import sign_form


@pytest.fixture()
def fresh_store():
    os.environ.pop("UPSTASH_REDIS_REST_URL", None)
    os.environ.pop("UPSTASH_REDIS_REST_TOKEN", None)
    import importlib
    import gateway.store as st
    importlib.reload(st)
    return st.MemoryStore()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    from gateway import chat as gchat
    gchat._RATE.clear()
    gchat._CURRENT_USER[0] = ""
    for var in ("VERIQ_ALLOWED_REPOS", "GITHUB_API_TOKEN", "GATEWAY_REGISTRATION_TOKEN",
                "SLACK_ADMIN_USER_ID", "APPROVAL_GATEWAY_URL"):
        monkeypatch.delenv(var, raising=False)
    yield


def _cmd(store, command, text="", user_id="U1", user_name="marouaneakrich", chan="D1"):
    from gateway import chat as gchat
    return gchat.handle_command(store, {"command": command, "text": text,
                                        "user_id": user_id, "user_name": user_name,
                                        "channel_id": chan})


def test_slash_endpoint_ephemeral_and_signature(gateway):
    client, g = gateway
    r = sign_form(client, "/slack/slash", {"command": "/help", "user_id": "U1",
                                           "user_name": "m", "text": ""})
    assert r.status_code == 200 and r.json()["response_type"] == "ephemeral"
    assert "/scan" in r.json()["text"] and "owner/repo" in r.json()["text"]
    bad = client.post("/slack/slash", content=b"command=%2Fhelp&user_id=U1",
                      headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert bad.status_code == 401


def test_slash_router_pure_ask_and_clear(fresh_store, monkeypatch):
    from gateway import chat as gchat
    calls: list = []
    monkeypatch.setattr(gchat.llm, "chat",
                        lambda msgs, **k: calls.append(msgs) or "mock answer: tests look fine")
    store = fresh_store
    rep = _cmd(store, "/ask", "are tests flaky?")
    assert rep["response_type"] == "ephemeral" and "mock answer" in rep["text"]
    assert calls and calls[0][0]["role"] == "system"            # persona enforced
    assert store.chat("U1")                                     # turn remembered
    _cmd(store, "/ask", "and coverage?")
    assert len(calls) == 2 and any(m["role"] == "assistant" for m in calls[-1])
    rep_clear = _cmd(store, "/clear")
    assert rep_clear["response_type"] == "ephemeral"
    assert store.chat("U1") == []                               # /clear actually wipes
    _cmd(store, "/ask", "hello")
    assert not any("flaky" in str(m) for m in calls[-1] if m["role"] == "user")


def test_slash_ask_degrades_without_nim(fresh_store, monkeypatch):
    from gateway import chat as gchat
    monkeypatch.setattr(gchat.llm, "chat", lambda msgs, **k: None)
    rep = _cmd(fresh_store, "/ask", "hello")
    assert rep["response_type"] == "ephemeral"
    assert "unreachable" in rep["text"].lower()


def test_scan_requires_token_honestly(fresh_store):
    rep = _cmd(fresh_store, "/scan", "eiden-group/web")
    assert rep["response_type"] == "ephemeral"
    assert "configur" in rep["text"].lower()                    # guidance, not a crash


def test_scan_dispatch_flow(fresh_store, monkeypatch):
    from gateway import chat as gchat
    calls: dict = {"disp": []}

    def fake_api(method, url, body=None):
        if "/actions/workflows?per_page" in url:
            return 200, {"workflows": [{"name": "AI Engineering Agent", "state": "active",
                                        "path": ".github/workflows/ai-agent.yml"}]}
        if url.endswith("/repos/eiden-group/web"):
            return 200, {"default_branch": "main"}
        calls["disp"].append((method, url, body))
        return 204, {}

    monkeypatch.setattr(gchat, "_gh_api", fake_api)
    os.environ["GITHUB_API_TOKEN"] = "ghp_test_fake123456"
    rep = _cmd(fresh_store, "/scan", "eiden-group/web")
    assert "started" in rep["text"].lower()
    assert calls["disp"] and calls["disp"][0][2] == {"ref": "main"}
    assert "ai-agent.yml" in calls["disp"][0][1]
    # repo not matched by finder -> honest error
    def no_wf(method, url, body=None):
        return 200, {"workflows": []} if url.endswith("per_page=100") else fake_api(method, url, body)
    monkeypatch.setattr(gchat, "_gh_api", no_wf)
    rep2 = _cmd(fresh_store, "/scan", "eiden-group/apps")
    assert "No Veriq workflow" in rep2["text"]


def test_scan_allowlist_enforced(fresh_store, monkeypatch):
    from gateway import chat as gchat
    os.environ["GITHUB_API_TOKEN"] = "ghp_test_fake123456"
    os.environ["VERIQ_ALLOWED_REPOS"] = "eiden-group/allowed"
    monkeypatch.setattr(gchat, "_find_workflow", lambda r, h: "ai-agent.yml")
    monkeypatch.setattr(gchat, "_default_branch", lambda r: "main")
    posts: dict = {}
    real = gchat._gh_api

    def spy(method, url, body=None):
        posts.setdefault(url, []).append(body)
        return real(method, url, body)
    monkeypatch.setattr(gchat, "_gh_api", spy)
    rep = _cmd(fresh_store, "/scan", "evilcorp/other")          # not whitelisted
    assert "no_entry" in rep["text"]
    assert not any("evilcorp" in u for u in posts)               # never dispatched


def test_results_endpoint_and_gating(gateway):
    client, g = gateway
    os.environ.pop("GATEWAY_REGISTRATION_TOKEN", None)
    rec = {"audit_id": "AUDIT-X", "repository": "eiden-group/web", "commit": "dead0001",
           "pr_number": 7, "triggered_by": "marouaneakrich", "slack_user": "UADM_PRIV",
           "overall_score": 81, "severity_counts": {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2},
           "tests": {"status": "pass"}, "build": {"status": "pass"},
           "security": {"status": "pass"},
           "approval": {"decision": "approved"}, "fixes": {"fixed": 1},
           "run_url": "https://github.com/x/actions/runs/1"}
    assert client.post("/results", json=rec).status_code == 200
    os.environ["GATEWAY_REGISTRATION_TOKEN"] = "sekret"
    assert client.post("/results", json=rec).status_code == 401
    assert client.post("/results", json=rec,
                       headers={"X-Veriq-Token": "sekret"}).status_code == 200
    os.environ.pop("GATEWAY_REGISTRATION_TOKEN", None)


def test_status_privacy_and_grounding(fresh_store, monkeypatch):
    from gateway import chat as gchat
    store = fresh_store
    store.add_result({"repository": "eiden-group/web", "commit": "dead0001",
                      "triggered_by": "marouaneakrich", "slack_user": "U1",
                      "score": 88, "cr": 0, "hi": 0, "me": 1, "lo": 0,
                      "decision": "advisory-only", "fixed": 0,
                      "run_url": "https://run", "reported_at": time.time()})
    rep = _cmd(store, "/status", user_id="U1", user_name="marouaneakrich")
    assert "eiden-group/web" in rep["text"] and "88/100" in rep["text"]
    assert "eiden-group/web" not in _cmd(store, "/status", user_id="U9",
                                         user_name="whoever")["text"]     # not yours to see
    os.environ["SLACK_ADMIN_USER_ID"] = "UADM"
    assert "eiden-group/web" in _cmd(store, "/status", user_id="UADM",
                                     user_name="admin")["text"]          # admin sees all

    cap: list = []
    monkeypatch.setattr(gchat.llm, "chat",
                        lambda msgs, **k: cap.append(msgs) or "based on evidence")
    _cmd(store, "/ask", "how is eiden-group/web doing?")
    assert any("LAST VERIQ AUDIT" in m["content"] for m in cap[-1])       # grounded, not hallucinated


def test_events_routing_dm_mention_and_bot_loop(fresh_store, monkeypatch):
    from gateway import chat as gchat
    posted: list = []
    monkeypatch.setattr(gchat, "_post_slack",
                        lambda ch, tx, ts="": posted.append((ch, tx, ts)))
    monkeypatch.setattr(gchat, "bot_user_id", lambda: "UBOT")
    monkeypatch.setattr(gchat.llm, "chat", lambda msgs, **k: "quiet answer")

    class S:
        def chat(self, u): return []
        def remember(self, u, h): pass
        def latest_result(self, r): return None
        def recent_results(self, n=25): return []
        def forget(self, u): pass

    assert gchat.handle_message_event(S(), {"channel": "C1", "text": "lunch?",
                                            "user": "U5", "ts": "1.1"}) is False
    assert posted == []                                                   # channel noise ignored
    assert gchat.handle_message_event(S(), {"channel": "C2", "text": "<@UBOT> help",
                                            "user": "U5", "ts": "2.1"}) is True
    assert posted[-1][0] == "C2" and posted[-1][2] == "2.1"              # threaded reply
    assert gchat.handle_message_event(S(), {"channel": "D9", "text": "hi",
                                            "user": "U5", "ts": "3.1"}) is True
    assert gchat.handle_message_event(S(), {"channel": "D9", "text": "yo",
                                            "bot_id": "B1", "user": "U5", "ts": "4.1"}) is False
    assert gchat.handle_message_event(S(), {"channel": "D9", "text": "",
                                            "user": "U5", "ts": "5.1"}) is False
    assert all(ts for _, _, ts in posted)                                 # never main-channel noise


def test_rate_limit():
    from gateway import chat as gchat
    gchat._RATE.clear()
    assert all(not gchat._throttle("U99") for _ in range(20))
    assert gchat._throttle("U99")                                        # 21st blocked


def test_report_result_ci_side(monkeypatch):
    import json as _json
    import urllib.request
    import slack.approval as sa
    seen: dict = {}
    os.environ["APPROVAL_GATEWAY_URL"] = "http://gw.test"
    os.environ["GATEWAY_REGISTRATION_TOKEN"] = "rtok"

    class Resp:
        def read(self): return _json.dumps({"ok": True}).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake(req, timeout=15):
        seen["url"] = req.full_url
        seen["tok"] = req.get_header("X-veriq-token")
        seen["body"] = _json.loads(req.data.decode())
        return Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    audit = {"audit_id": "A-9", "repository": "o/r", "commit": "c1", "pr_number": None,
             "triggered_by": "dev", "slack_user": None, "overall_score": 77,
             "severity_counts": {"HIGH": 1}, "tests": {"status": "pass"},
             "build": {"status": "skipped"}, "security": {"status": "pass"},
             "approval": {"requested": False, "decision": "advisory-only"},
             "fixes": {"identified": 0, "fixed": 0, "advisory": []}}
    assert sa.report_result(audit)
    assert seen["url"] == "http://gw.test/results" and seen["tok"] == "rtok"
    assert seen["body"]["severity_counts"] == {"HIGH": 1}
    assert "findings" not in _json.dumps(seen["body"])                   # counts only, no payload leak
    for k in ("APPROVAL_GATEWAY_URL", "GATEWAY_REGISTRATION_TOKEN"):
        os.environ.pop(k, None)
