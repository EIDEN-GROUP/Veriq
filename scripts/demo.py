"""Veriq end-to-end demo (no network): simulated repo -> audit -> Slack approve -> patch -> verify -> messages -> artifacts.

Run:  python scripts/demo.py
Mocks NIM (returns a deterministic safe fix) and Slack (auto-approves as mapped dev).
Proves the DETECT -> EVIDENCE -> ANALYZE -> ASK -> APPROVE -> FIX -> VERIFY -> AUDIT -> NOTIFY chain.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")  # Windows console emoji safety
except Exception:
    pass

# ---- simulated target repo: Node lint failure (fixable) ----
TARGET_FILES = {
    "package.json": json.dumps({"name": "demo", "scripts": {"lint": "eslint .", "test": "node --test"}}),
    "src/util.js": "function add(a,b){\nreturn a+b\n}\nmodule.exports={add}\n",  # missing semicolons -> lint fix
}


class FakeNim:
    model = "demo-nemotron"

    def complete_json(self, task, evidence, reasoning=True):
        if "Fixer" in task or "patches" in task or "fixer" in task.lower():
            return {"patches": [{"path": "src/util.js",
                                 "diff": "--- a/src/util.js\n+++ b/src/util.js\n@@ -1,3 +1,3 @@\n-function add(a,b){\n-return a+b\n+function add(a, b) {\n+  return a + b;\n }\n module.exports={add}\n",
                                 "rationale": "lint: spacing + semicolons"}]}
        if "Verifier" in task or "verifier" in task.lower():
            return {"finding_id": "CODE-001", "fixed": True, "reason": "lint passes after patch"}
        return {"overall_score": 82, "findings": [
            {"id": "CODE-001", "severity": "LOW", "category": "code",
             "title": "Lint: formatting in src/util.js", "description": "Missing semicolons/spacing flagged by eslint.",
             "file": "src/util.js", "line": 2, "evidence": "error: Missing semicolon (semi) at src/util.js:2",
             "recommendation": "Run eslint --fix.", "auto_fixable": True, "confidence": 0.92}]}


def main() -> int:
    from agent.agent_loop import AgentLoop
    from agent.permissions import Policy
    from agent.tool_registry import ToolRegistry
    from reports.generator import write_audit_artifacts
    from slack.formatting import approval_blocks, final_dev_text, admin_text

    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "demo-repo"
        target.mkdir()
        for rel, content in TARGET_FILES.items():
            p = target / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)

        from scanners.project_detector import detect_project, to_dict
        project = to_dict(detect_project(target))
        print(f"1. DETECT: {project['languages']} scripts={list(project['scripts'])}")

        evidence = {"static": {"status": "fail", "checks": [
            {"command": "npm run lint", "exit": 1,
             "output": "src/util.js 2:9 error Missing semicolon semi"}]},
            "security": {"status": "pass", "findings": []},
            "dependencies": {"status": "skipped"}, "tests": {"status": "pass"},
            "build": {"status": "skipped"}, "frontend": {"status": "skipped"}}
        print("2. EVIDENCE: static=fail(security/tests deterministic)")

        loop = AgentLoop(nim=FakeNim(), tools=ToolRegistry(root=target, policy=Policy()))  # type: ignore
        analysis = loop.audit(project, evidence)
        print(f"3. ANALYZE (NIM): score={analysis['overall_score']} findings={len(analysis['findings'])}")

        audit = {"audit_id": "AUDIT-DEMO-000123", "repository": "EIDEN-GROUP/demo",
                 "branch": "main", "commit": "abc123", "pr_number": 123,
                 "triggered_by": "anynonenom", "slack_user": "U09D383NDSM",
                 "overall_score": 82, "findings": analysis["findings"],
                 "severity_counts": {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 1, "INFO": 0},
                 "tests": {"status": "pass"}, "build": {"status": "skipped"},
                 "security": {"status": "pass"}, "frontend": {"status": "skipped"},
                 "fixes": {"identified": 1}, "approval": {"requested": True},
                 "timestamps": {}, "artifacts": []}
        print("4. ASK: approval blocks ->", [e["elements"][0]["text"]["text"] for e in approval_blocks(audit) if e["type"] == "actions"])

        # 5. APPROVE via gateway (real signature path)
        os.environ.update({"SLACK_SIGNING_SECRET": "demo-secret", "SLACK_ADMIN_USER_ID": "U0AQWT35TP0",
                           "SLACK_USER_MAP": json.dumps({"anynonenom": "U09D383NDSM"})})
        from fastapi.testclient import TestClient
        import gateway.app as gw
        from slack.approval import wait_for_decision
        client = TestClient(gw.app)
        client.post("/audits", json={"audit_id": audit["audit_id"], "repository": audit["repository"],
                                     "commit": audit["commit"], "pr_number": 123,
                                     "triggered_by": "anynonenom", "slack_user": "U09D383NDSM",
                                     "timeout_minutes": 30})
        import hashlib, hmac, time
        inner = {"actions": [{"action_id": f"approve:{audit['audit_id']}", "value": "EIDEN-GROUP/demo|abc123|123"}],
                 "user": {"id": "U09D383NDSM"}}
        raw = f"payload={json.dumps(inner)}"
        ts = str(int(time.time()))
        sig = hmac.new(b"demo-secret", f"v0:{ts}:{raw}".encode(), hashlib.sha256).hexdigest()
        r = client.post("/slack/actions", content=raw.encode(),
                        headers={"Content-Type": "application/x-www-form-urlencoded",
                                 "X-Slack-Signature": f"v0={sig}", "X-Slack-Request-Timestamp": ts})
        os.environ["APPROVAL_GATEWAY_URL"] = "http://testserver"
        # point wait_for_decision at the test client via monkeypatched urlopen
        import urllib.request
        real = urllib.request.urlopen

        class FakeResp:
            def __init__(self, data): self._d = data
            def read(self): return json.dumps(self._d).encode()
            def __enter__(self): return self
            def __exit__(self, *a): return False

        urllib.request.urlopen = lambda url, timeout=15: FakeResp(client.get(f"/approvals/{audit['audit_id']}").json())
        decision = wait_for_decision(audit["audit_id"], timeout_minutes=1)
        urllib.request.urlopen = real
        print(f"5. APPROVE: gateway={r.status_code} decision={decision['decision']} by {decision.get('approver_slack_id')}")

        # 6-7. FIX + VERIFY
        plan = loop.request_fix(audit["findings"], {"src/util.js": TARGET_FILES["src/util.js"]})
        print(f"6. FIX: patches={len(plan['patches'])} branch=ai-agent/fix-abc123-000123 (bot branch, never user branch)")
        ver = loop.verify(audit["findings"][0], {"tests": "pass"}, {"tests": "pass"})
        print(f"7. VERIFY: fixed={ver['fixed']} reason={ver['reason']}")
        assert ver["fixed"] is True

        # 8-9. AUDIT + NOTIFY
        out = Path(td) / "artifacts"
        write_audit_artifacts({**audit, "approval": {**audit["approval"], **decision},
                               "fixes": {"identified": 1, "approved": 1, "fixed": 1, "failed": []},
                               "verification": {"status": "pass"}}, out)
        print("8. AUDIT artifacts:", sorted(p.name for p in out.iterdir()))
        print("9a. DEV message:\n" + final_dev_text({**audit, "approval": {"decision": "approved"},
                                                     "fixes": {"fixed": 1, "failed": []}})[:400])
        print("9b. ADMIN message:\n" + admin_text({**audit, "approval": {"requested": True, "decision": "approved"},
                                                  "fixes": {"fixed": 1}})[:500])
        print("\nDEMO OK: chain complete, no user branch touched, admin has independent record.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
