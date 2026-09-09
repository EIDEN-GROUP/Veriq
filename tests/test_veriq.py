"""Veriq test suite: deterministic units + approval/security/fixture coverage. No network."""
from __future__ import annotations

import hashlib
import hmac
import json
import sys
import time
import urllib.parse
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ---------- redaction ----------
def test_secret_redaction():
    from agent.redact import redact_text, redact_evidence_blob, should_exclude_path
    assert "[REDACTED_SECRET]" in redact_text("key sk-123456789abcdef here")
    assert "[REDACTED_SECRET]" in redact_text("xoxb-12345-token here")
    assert "[REDACTED_SECRET]" in redact_text("api_key=supersecretvalue123")
    assert "[REDACTED_SECRET]" in redact_text("-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----")
    assert should_exclude_path(".env") and should_exclude_path("config/token.pem")
    blob = redact_evidence_blob({"api_key": "abc123456", "nested": ["ghp_12345678901234567890"]})
    assert blob["api_key"] == "[REDACTED_SECRET]"
    assert "[REDACTED_SECRET]" in blob["nested"][0]


# ---------- permissions: dangerous behavior rejected ----------
def test_slack_events_route_challenge_and_ack():
    client, _ = _gw_client()
    import os
    os.environ["SLACK_SIGNING_SECRET"] = "s3cr3t"
    chal = {"type": "url_verification", "challenge": "EVENTS-CHAL", "token": "t"}
    r = client.post("/slack/events", json=chal)  # 404 before the fix
    assert r.status_code == 200 and r.text == "EVENTS-CHAL"
    body = json.dumps({"type": "event_callback", "event": {"type": "reaction_added"}})
    r = client.post("/slack/events", content=body.encode(),
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 401  # unsigned callbacks rejected
    ts = str(int(time.time()))
    mac = hmac.new(b"s3cr3t", f"v0:{ts}:{body}".encode(), hashlib.sha256).hexdigest()
    r = client.post("/slack/events", content=body.encode(),
                    headers={"Content-Type": "application/json",
                             "X-Slack-Signature": f"v0={mac}", "X-Slack-Request-Timestamp": ts})
    assert r.status_code == 200 and r.json()["ok"] is True
    # misrouted events onto /slack/actions must ack, not 400-loop retries
    r = client.post("/slack/actions", content=body.encode(),
                    headers={"Content-Type": "application/json",
                             "X-Slack-Signature": f"v0={mac}", "X-Slack-Request-Timestamp": ts})
    assert r.status_code == 200 and r.json()["ok"] is True


def test_slack_url_verification_challenge_echoed():
    # Slack sends this when saving the Interactivity Request URL (may be unsigned).
    client, _ = _gw_client()
    chal = {"type": "url_verification", "challenge": "CHALLENGE-xyz-123", "token": "t"}
    r = client.post("/slack/actions", json=chal)  # application/json shape
    assert r.status_code == 200 and r.text == "CHALLENGE-xyz-123"
    r = client.post("/slack/actions", content="payload=" + urllib.parse.quote(json.dumps(chal)),
                    headers={"Content-Type": "application/x-www-form-urlencoded"})  # form shape
    assert r.status_code == 200 and r.text == "CHALLENGE-xyz-123"
    no_type = {"token": "t", "challenge": "BARE-CHAL"}  # Slack save sometimes omits type
    r = client.post("/slack/actions", json=no_type)
    assert r.status_code == 200 and r.text == "BARE-CHAL"
    # A normal (non-challenge) POST without signature still gets 401 — echo is challenge-only.
    r = client.post("/slack/actions", content="payload=%7B%22actions%22%3A%5B%5D%7D",
                    headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code in (400, 401)


def test_dangerous_commands_and_paths_rejected():
    from agent.permissions import Policy, check_patch_allowed
    pol = Policy(deny_paths=["**/migrations/**", ".env*"], require_admin_for=["auth"])
    for cmd in ("terraform apply -auto-approve", "git reset --hard HEAD", "kubectl delete pod x", "aws s3 rm s3://x"):
        assert not pol.is_command_allowed(cmd), cmd
    assert pol.is_command_allowed("npm run build --silent")
    assert not pol.is_tool_allowed("deploy_production")
    assert pol.is_tool_allowed("run_tests")
    ok, _ = check_patch_allowed(["src/app.ts"], pol)
    assert ok
    ok, _ = check_patch_allowed(["db/migrations/001.sql"], pol)
    assert not ok
    assert pol.needs_admin("auth") and not pol.needs_admin("code")


def test_tool_registry_blocks_privileged():
    from agent.permissions import Policy
    from agent.tool_registry import ToolRegistry
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        reg = ToolRegistry(root=Path(td), policy=Policy())
        r = reg.dispatch("run_command", {"command": "git reset --hard HEAD"})
        assert not r.ok and "denied" in r.output.lower()
        r = reg.dispatch("deploy_production", {})
        assert not r.ok


# ---------- project detection fixtures ----------
def test_detect_node_lint_fail_fixture():
    from scanners.project_detector import detect_project
    root = ROOT / "tests" / "fixtures" / "node-lint-fail"
    info = detect_project(root)
    assert "node" in info.languages and info.scripts.get("lint")


def test_detect_python_security_fixture():
    from scanners.project_detector import detect_project
    root = ROOT / "tests" / "fixtures" / "py-sec-issue"
    info = detect_project(root)
    assert "python" in info.languages


def test_detect_frontend_fixture():
    from scanners.project_detector import detect_project
    from browser.detector import detect_frontend
    from scanners.project_detector import to_dict
    root = ROOT / "tests" / "fixtures" / "frontend-visual"
    proj = to_dict(detect_project(root))
    fe = detect_frontend(root, proj)
    assert fe["detected"] and fe["framework"] == "Next.js"


def test_security_scanner_flags_fixture():
    from scanners.security_scanner import run_security_scans
    root = ROOT / "tests" / "fixtures" / "py-sec-issue"
    out = run_security_scans(root, {"languages": ["python"]})
    rules = {f["rule"] for f in out["findings"]}
    assert "hardcoded-secret" in rules or "command-injection" in rules or "insecure-deser" in rules


def test_a11y_overflow_flagged():
    from browser.evidence import check_accessibility
    ev = {"pages": [{"route": "/", "viewport": "mobile", "overflow_x": True, "title": "x"}]}
    out = check_accessibility(ev)
    assert out["status"] == "issues"
    assert any(i["rule"] == "horizontal-overflow" for i in out["issues"])


# ---------- NIM malformed response handling ----------
def test_nim_malformed_then_fallback():
    from agent.nim_client import NimClient
    c = NimClient(base_url="http://x", api_key="k", model="m1", fallback_models=["m2"])
    calls = {"n": 0}

    def fake_chat(model, system, user, reasoning):
        calls["n"] += 1
        if calls["n"] == 1:
            return "this is not json at all"
        return json.dumps({"overall_score": 80, "findings": []})

    c._chat = fake_chat  # type: ignore
    out = c.complete_json("task", {"a": 1})
    assert out["overall_score"] == 80 and calls["n"] == 2


def test_nim_all_models_fail_raises():
    from agent.nim_client import NimClient
    c = NimClient(base_url="http://x", api_key="k", model="m1", fallback_models=[])
    c._chat = lambda *a, **k: "garbage!!!"  # type: ignore
    with pytest.raises(RuntimeError):
        c.complete_json("t", {})


def test_agent_loop_drops_schema_invalid_and_marks_evidence_free():
    from agent.agent_loop import AgentLoop
    from agent.permissions import Policy
    from agent.tool_registry import ToolRegistry
    import tempfile

    class FakeNim:
        model = "fake"
        def complete_json(self, task, evidence, reasoning=True):
            return {"overall_score": 90, "findings": [
                {"id": "BAD", "severity": "HIGH"},  # invalid: dropped
                {"id": "SEC-001", "severity": "HIGH", "category": "security",
                 "title": "Possible issue with proper title", "description": "detailed desc here",
                 "file": "a.py", "line": 1, "evidence": "",  # empty evidence
                 "recommendation": "fix", "auto_fixable": True, "confidence": 0.9},
            ]}

    with tempfile.TemporaryDirectory() as td:
        loop = AgentLoop(nim=FakeNim(), tools=ToolRegistry(root=Path(td), policy=Policy()))  # type: ignore
        out = loop.audit({}, {})
        assert len(out["findings"]) == 1
        assert out["findings"][0]["auto_fixable"] is False
        assert out["findings"][0]["needs_human_review"] is True


# ---------- approval gateway: accept / reject / unauthorized / timeout / replay ----------
def _gw_client():
    import os as _os
    import importlib
    from fastapi.testclient import TestClient
    _os.environ.pop("UPSTASH_REDIS_REST_URL", None)
    _os.environ.pop("UPSTASH_REDIS_REST_TOKEN", None)
    import gateway.app as g
    importlib.reload(g)  # re-pick memory backend regardless of earlier env
    g.reset_state()
    return TestClient(g.app), g


def _slack_post(client, g, audit_id, user, verb, secret="s3cr3t", ts=None, repo="o/r"):
    import os
    os.environ["SLACK_SIGNING_SECRET"] = secret
    os.environ["SLACK_ADMIN_USER_ID"] = "U0AQWT35TP0"
    os.environ["SLACK_USER_MAP"] = json.dumps({"dev": "UDEV1"})
    g.store.set_pending(audit_id, {"audit_id": audit_id, "repository": repo, "commit": "abc",
                                       "pr_number": 1, "triggered_by": "dev", "slack_user": "UDEV1",
                                       "created": time.time(), "expires": time.time() + 600})
    ts = ts or str(int(time.time()))
    inner = {"actions": [{"action_id": f"{verb}:{audit_id}", "value": f"{repo}|abc|1"}],
             "user": {"id": user}}
    raw_form = f"payload={json.dumps(inner)}"
    mac = hmac.new(secret.encode(), f"v0:{ts}:{raw_form}".encode(), hashlib.sha256).hexdigest()
    return client.post("/slack/actions", content=raw_form.encode(),
                       headers={"Content-Type": "application/x-www-form-urlencoded",
                                "X-Slack-Signature": f"v0={mac}",
                                "X-Slack-Request-Timestamp": ts})


def test_gateway_approve_and_duplicate():
    client, g = _gw_client()
    r = _slack_post(client, g, "A1", "UDEV1", "approve")
    assert r.status_code == 200
    assert client.get("/approvals/A1").json()["decision"] == "approved"
    r2 = _slack_post(client, g, "A1", "UDEV1", "approve")  # duplicate
    assert r2.status_code == 200 and "duplicate" in r2.text.lower()


def test_gateway_reject_and_unauthorized():
    client, g = _gw_client()
    r = _slack_post(client, g, "A2", "UDEV1", "reject")
    assert client.get("/approvals/A2").json()["decision"] == "rejected"
    r = _slack_post(client, g, "A3", "UEVIL", "approve")
    assert r.status_code == 403  # unauthorized slack user cannot approve


def test_gateway_replay_and_expiry():
    import os as _os
    client, g = _gw_client()
    old_ts = str(int(time.time()) - 600)
    r = _slack_post(client, g, "A4", "UDEV1", "approve", ts=old_ts)
    assert r.status_code == 401  # stale timestamp = replay rejected
    # expired audit: pending past its deadline with NO decision yet -> GET lazily expires
    _os.environ["SLACK_SIGNING_SECRET"] = "s3cr3t"
    g.store.set_pending("A5", {"audit_id": "A5", "repository": "o/r", "commit": "abc",
                               "pr_number": 1, "triggered_by": "dev", "slack_user": "UDEV1",
                               "created": time.time() - 3600, "expires": time.time() - 1})
    assert client.get("/approvals/A5").json()["decision"] == "expired"
    # ...and a late Slack action on that expired audit must NOT flip it to approved
    r = _slack_post(client, g, "A6", "UDEV1", "approve")
    assert client.get("/approvals/A6").json()["decision"] == "approved"  # sanity: fresh works
    g.store.clear_decision("A6")
    _pend = g.store.get_pending("A6")
    assert _pend is not None
    _pend["expires"] = time.time() - 1
    g.store.set_pending("A6", _pend)
    ts = str(int(time.time()))
    inner = {"actions": [{"action_id": "approve:A6", "value": "o/r|abc|1"}], "user": {"id": "UDEV1"}}
    raw_form = f"payload={json.dumps(inner)}"
    mac = hmac.new(b"s3cr3t", f"v0:{ts}:{raw_form}".encode(), hashlib.sha256).hexdigest()
    r = client.post("/slack/actions", content=raw_form.encode(),
                    headers={"Content-Type": "application/x-www-form-urlencoded",
                             "X-Slack-Signature": f"v0={mac}", "X-Slack-Request-Timestamp": ts})
    assert r.status_code == 200
    assert client.get("/approvals/A6").json()["decision"] == "expired"


def test_approval_timeout_without_gateway():
    import os
    os.environ.pop("APPROVAL_GATEWAY_URL", None)
    from slack.approval import wait_for_decision
    d = wait_for_decision("NOPE", timeout_minutes=0)
    assert d["decision"] == "expired"


# ---------- gateway registration (buttons dead-end without it) ----------
def test_register_with_gateway_posts_binding(monkeypatch):
    import os
    import urllib.request
    from slack.approval import register_with_gateway
    os.environ["APPROVAL_GATEWAY_URL"] = "http://gw"
    seen: dict = {}

    class FakeResp:
        def read(self): return json.dumps({"ok": True}).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=15):
        seen["url"] = req.full_url
        seen["payload"] = json.loads(req.data.decode())
        return FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    audit = {"audit_id": "A9", "repository": "o/r", "commit": "abc",
             "pr_number": 7, "triggered_by": "dev", "slack_user": "UDEV1"}
    assert register_with_gateway(audit, 30) is True
    assert seen["url"] == "http://gw/audits"
    assert seen["payload"]["repository"] == "o/r" and seen["payload"]["commit"] == "abc"
    monkeypatch.delenv("APPROVAL_GATEWAY_URL")
    assert register_with_gateway(audit) is False  # notify-only, no raise


def test_request_approval_registers_first(monkeypatch):
    import slack.approval as ap
    order: list[str] = []
    monkeypatch.setattr(ap, "register_with_gateway", lambda a, t=30: order.append("register") or True)
    monkeypatch.setattr(ap, "post_message", lambda *a, **k: order.append("post") or True)
    ap.request_approval({"audit_id": "AX", "repository": "o/r", "commit": "c",
                         "triggered_by": "d", "slack_user": "U1",
                         "overall_score": 80, "severity_counts": {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0},
                         "tests": {}, "build": {}, "fixes": {"identified": 1}})
    assert order[0] == "register" and "post" in order


def test_prepare_frontend_never_raises(tmp_path):
    from browser.server import prepare_frontend
    out = prepare_frontend(tmp_path, "npm", "")
    assert set(out) == {"installed", "build_exit", "steps"}
    assert out["installed"] is False and out["build_exit"] == 0  # no build declared


# ---------- approval state backends (memory vs Upstash for serverless) ----------
def test_memory_store_roundtrip():
    from gateway.store import MemoryStore
    s = MemoryStore()
    assert s.get_pending("x") is None and s.get_decision("x") is None
    s.set_pending("x", {"audit_id": "x"})
    s.set_decision("x", {"decision": "approved"})
    assert s.get_pending("x") == {"audit_id": "x"}
    assert s.get_decision("x")["decision"] == "approved"
    s.clear_decision("x")
    assert s.get_decision("x") is None
    s.clear_all()
    assert s.get_pending("x") is None


def test_backend_selection(monkeypatch):
    import os
    from gateway.store import MemoryStore, UpstashStore, get_store
    monkeypatch.delenv("UPSTASH_REDIS_REST_URL", raising=False)
    monkeypatch.delenv("UPSTASH_REDIS_REST_TOKEN", raising=False)
    assert isinstance(get_store(), MemoryStore)
    monkeypatch.setenv("UPSTASH_REDIS_REST_URL", "https://x.upstash.io")
    monkeypatch.setenv("UPSTASH_REDIS_REST_TOKEN", "tok")
    assert isinstance(get_store(), UpstashStore)


def test_upstash_store_rest_shape(monkeypatch):
    import urllib.request
    from gateway.store import UpstashStore
    calls: list = []

    class FakeResp:
        def __init__(self, payload): self._p = payload
        def read(self): return json.dumps(self._p).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=10):
        calls.append(json.loads(req.data.decode()))
        if calls[-1][0] == "GET":
            return FakeResp({"result": json.dumps({"decision": "approved"})})
        return FakeResp({"result": "OK"})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    s = UpstashStore("https://x.upstash.io", "tok")
    s.set_decision("A1", {"decision": "approved"})
    assert calls[0][0] == "SET" and calls[0][1] == "veriq:decision:A1"
    assert "EX" in calls[0]  # decisions always carry TTL
    assert s.get_decision("A1")["decision"] == "approved"
    assert calls[-1] == ["GET", "veriq:decision:A1"]


# ---------- idempotency ----------
def test_idempotency_key_stable():
    from agent.idempotency import audit_key
    import tempfile
    from agent.idempotency import already_audited, mark_audited
    assert audit_key("o/r", "abc", 1) == audit_key("o/r", "abc", 1)
    assert audit_key("o/r", "abc", 1) != audit_key("o/r", "abd", 1)
    with tempfile.TemporaryDirectory() as td:
        assert not already_audited(Path(td), "k1")
        mark_audited(Path(td), "k1", "AUDIT-1")
        assert already_audited(Path(td), "k1")
