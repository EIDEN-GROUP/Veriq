"""Veriq orchestrator: DETECT -> EVIDENCE -> ANALYZE -> ASK -> APPROVE -> FIX -> VERIFY -> AUDIT -> NOTIFY.

Entry: `python -m agent.orchestrator` inside GitHub Actions (or locally with .env).
Deterministic tools are the source of truth; NIM reasons; Slack gates every mutation.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # type: ignore
from agent.agent_loop import AgentLoop
from agent.idempotency import already_audited, audit_key, mark_audited
from agent.nim_client import NimClient
from agent.permissions import Policy
from agent.redact import redact_text
from agent.tool_registry import ToolRegistry


def log_event(event: str, **fields: object) -> None:
    rec = {"event": event, "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(), **fields}
    print(json.dumps(rec), flush=True)


SEV_RANK = {"INFO": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}


def partition_fixable(findings: list[dict], min_severity: str,
                      ) -> tuple[list[dict], list[dict]]:
    """(fixable, advisory): auto-fixable & not human-review-only items at/above
    min_severity become approval candidates; everything lower is advisory-only and
    reaches the admin audit without nagging the developer."""
    rank = SEV_RANK.get(min_severity.upper(), 2)
    fixable = [f for f in findings
               if f.get("auto_fixable") and not f.get("needs_human_review")]
    above = [f for f in fixable if SEV_RANK.get(str(f.get("severity", "INFO")).upper(), 0) >= rank]
    below = [f for f in fixable if f not in above]
    return above, below


def load_config(repo_cfg: Path) -> dict:
    defaults = yaml.safe_load((ROOT / "config" / "defaults.yml").read_text())
    if repo_cfg.exists():
        try:
            override = yaml.safe_load(repo_cfg.read_text()) or {}
            _deep_merge(defaults, override)
        except Exception as e:
            log_event("CONFIG_OVERRIDE_INVALID", error=str(e))
    # Env wins for operational knobs
    defaults["agent"]["max_repair_attempts"] = int(os.environ.get("AI_AGENT_MAX_REPAIR_ATTEMPTS", defaults["agent"]["max_repair_attempts"]))
    defaults["repair"]["approval_timeout_minutes"] = int(os.environ.get("AI_AGENT_APPROVAL_TIMEOUT", defaults["repair"]["approval_timeout_minutes"]))
    if os.environ.get("AI_AGENT_ENABLED", "true").lower() == "false":
        defaults["agent"]["enabled"] = False
    return defaults


def _deep_merge(base: dict, override: dict) -> None:
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


def _gh_context() -> dict:
    return {
        "repository": os.environ.get("GITHUB_REPOSITORY", "local/veriq-dev"),
        "branch": os.environ.get("GITHUB_REF_NAME", "local"),
        "commit": os.environ.get("GITHUB_SHA", _local_sha()),
        "pr_number": _env_int("PR_NUMBER") or _env_int("GITHUB_PR_NUMBER"),
        "actor": os.environ.get("GITHUB_ACTOR", os.environ.get("USER", "local-dev")),
        "is_fork": os.environ.get("GITHUB_IS_FORK", "false").lower() == "true",
    }


def _env_int(name: str) -> int | None:
    try:
        return int(os.environ[name]) if os.environ.get(name) else None
    except ValueError:
        return None


def _local_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10).stdout.strip() or "local"
    except Exception:
        return "local"


def resolve_slack_user(github_user: str) -> str | None:
    try:
        mapping = json.loads(os.environ.get("SLACK_USER_MAP", "{}"))
    except json.JSONDecodeError:
        log_event("USERMAP_INVALID_JSON")
        return None
    return mapping.get(github_user)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default=".")
    ap.add_argument("--config", default=".ai-agent.yml")
    ap.add_argument("--artifacts", default="artifacts")
    args = ap.parse_args()

    target = (Path.cwd() / args.target).resolve()
    artifacts = Path(args.artifacts)
    artifacts.mkdir(parents=True, exist_ok=True)

    cfg = load_config(target / args.config)
    if not cfg["agent"]["enabled"]:
        print("Veriq disabled by configuration.")
        return 0

    ctx = _gh_context()
    audit_id = f"AUDIT-{datetime.date.today():%Y-%m-%d}-{secrets.token_hex(3).upper()}"
    key = audit_key(ctx["repository"], ctx["commit"], ctx["pr_number"])
    state_dir = artifacts / "_state"
    if already_audited(state_dir, key):
        log_event("AUDIT_DUPLICATE_SKIP", audit_id=audit_id, key=key)
        return 0

    log_event("AUDIT_STARTED", audit_id=audit_id, **ctx)

    # ---- DETECT ----
    from scanners.project_detector import detect_project, to_dict
    project = to_dict(detect_project(target))
    log_event("PROJECT_DETECTED", languages=project["languages"], frontend=project["frontend"])

    # ---- EVIDENCE (deterministic) ----
    from scanners.code_scanner import run_static_checks
    from scanners.security_scanner import run_security_scans
    from scanners.dependency_scanner import run_dependency_audit
    from scanners.test_runner import run_tests
    from scanners.build_runner import run_build
    log_event("STATIC_SCAN_STARTED", audit_id=audit_id)
    static = run_static_checks(target, project)
    security = run_security_scans(target, project)
    deps = run_dependency_audit(target, project)
    tests = run_tests(target, project)
    build = run_build(target, project)
    log_event("STATIC_SCAN_COMPLETED", static=static["status"], security=security["status"],
              tests=tests["status"], build=build["status"])

    frontend_evidence: dict = {"status": "skipped"}
    if cfg["frontend"].get("enabled", "auto") != "disabled" and project["frontend"].get("detected"):
        from browser.crawler import crawl, discover_routes
        from browser.server import prepare_frontend, start_app
        pms = project.get("package_managers", [])
        prep = prepare_frontend(target, pms[0] if pms else "npm",
                                project["frontend"].get("build_cmd", ""))
        frontend_evidence["prepare"] = {k: v for k, v in prep.items() if k != "steps"}
        if prep["installed"] and prep["build_exit"] == 0:
            srv = start_app(target, project["frontend"].get("start_cmd", ""))
        else:
            from browser.server import AppServer as _AppServer
            srv = _AppServer(process=None, url="",
                             logs=f"prepare failed: install={prep['installed']} build_exit={prep['build_exit']}")
        if srv.url:
            routes = discover_routes(target, srv.url, cfg["frontend"].get("max_routes", 15))
            frontend_evidence = {**frontend_evidence,
                                 **crawl(srv.url, routes, artifacts / "screenshots")}
            srv.stop()
        elif "status" not in frontend_evidence:
            frontend_evidence = {**frontend_evidence, "status": "unavailable",
                                 "reason": (srv.logs[-500:] if srv.logs else "no start command")}

    evidence = {"static": static, "security": security, "dependencies": deps,
                "tests": tests, "build": build, "frontend": frontend_evidence}

    # ---- ANALYZE (NIM, fail-safe to deterministic-only) ----
    policy = Policy(deny_paths=cfg["repair"].get("deny_paths", []),
                    require_admin_for=cfg["repair"].get("require_admin_for", []),
                    repair_enabled=cfg["repair"]["enabled"])
    nim = NimClient()
    loop = AgentLoop(nim=nim, tools=ToolRegistry(root=target, policy=policy))
    log_event("AI_ANALYSIS_STARTED", model=nim.model)
    try:
        analysis = loop.audit(project, evidence)
        findings, score = analysis.get("findings", []), int(analysis.get("overall_score", 70))
    except Exception as e:
        log_event("AI_ANALYSIS_FAILED", error=redact_text(str(e)))
        findings, score = _deterministic_fallback_findings(evidence), 65
    log_event("AI_ANALYSIS_COMPLETED", findings=len(findings), score=score)

    sev_counts = {s: 0 for s in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO")}
    for f in findings:
        sev_counts[f.get("severity", "INFO")] = sev_counts.get(f.get("severity", "INFO"), 0) + 1

    slack_user = resolve_slack_user(ctx["actor"])
    admin_id = os.environ.get("SLACK_ADMIN_USER_ID", "")

    audit: dict = {
        "audit_id": audit_id, "repository": ctx["repository"], "branch": ctx["branch"],
        "commit": ctx["commit"], "pr_number": ctx["pr_number"], "triggered_by": ctx["actor"],
        "slack_user": slack_user, "overall_score": score, "findings": findings,
        "severity_counts": sev_counts, "tests": tests, "build": build, "security": security,
        "frontend":         frontend_evidence, "fixes": {"identified": 0, "approved": 0, "fixed": 0, "failed": [],
                                    "advisory": []},
        "verification": {}, "approval": {"requested": False, "decision": "none"},
        "timestamps": {"started": datetime.datetime.now(datetime.timezone.utc).isoformat()},
        "artifacts": [],
    }

    # ---- ASK / APPROVE (Slack-gated; forks + unmapped devs handled safely) ----
    from slack.notifications import notify_audit
    min_sev = str(cfg["repair"].get("approval_min_severity", "MEDIUM")).upper()
    fixable, advisory = partition_fixable(findings, min_sev)
    audit["fixes"]["identified"] = len(fixable)
    audit["fixes"]["advisory"] = [f["id"] for f in advisory]
    can_repair = (cfg["repair"]["enabled"] and cfg["repair"]["require_slack_approval"]
                  and os.environ.get("AI_AGENT_ENABLE_REPAIR", "true").lower() != "false"
                  and not ctx["is_fork"] and bool(fixable))
    if can_repair:
        from slack.approval import request_approval, wait_for_decision
        audit["approval"]["requested"] = True
        log_event("APPROVAL_REQUESTED", audit_id=audit_id, fixable=len(fixable))
        if request_approval(audit, timeout_note=cfg["repair"]["approval_timeout_minutes"]):
            decision = wait_for_decision(audit_id, timeout_minutes=cfg["repair"]["approval_timeout_minutes"])
        else:
            decision = {"decision": "unavailable", "reason": "slack post failed; payload saved to artifacts"}
            (artifacts / "slack-payload-failed.json").write_text(json.dumps(audit["approval"], default=str))
        audit["approval"].update(decision)
        log_event("APPROVAL_DECIDED", decision=audit["approval"].get("decision"))
        if audit["approval"].get("decision") == "approved":
            from slack.approval import mark_working
            mark_working(audit)  # in-banner: buttons -> "🔧 AI applying fixes…"
            _run_fix_flow(audit, loop, policy, target, cfg, artifacts)
    elif ctx["is_fork"]:
        audit["approval"]["decision"] = "skipped-fork-readonly"
    elif fixable and not cfg["repair"]["enabled"]:
        audit["approval"]["decision"] = "repair-disabled"
    elif advisory and not fixable:
        # Below-threshold suggestions (LOW/INFO): never bother the developer for
        # approval; report them to the admin in the audit instead.
        audit["approval"]["decision"] = "advisory-only"
        log_event("APPROVAL_SKIPPED_BELOW_THRESHOLD", audit_id=audit_id,
                  advisory=len(advisory), min_severity=min_sev)

    # ---- AUDIT artifacts + NOTIFY (admin ALWAYS, dev when mapped) ----
    from reports.generator import write_audit_artifacts
    write_audit_artifacts(audit, artifacts)
    mark_audited(state_dir, key, audit_id)
    notify_audit(audit, admin_id=admin_id, artifacts_dir=artifacts)  # dev + admin fan-out inside; failures saved, never fatal
    try:
        from github.checks import set_check
        statuses = [str(x.get("status")) for x in (tests, build, security)]
        crit = sev_counts.get("CRITICAL", 0)
        state = ("failure" if ("fail" in statuses or (crit and cfg["security"]["block_on_critical"]))
                 else "success" if not crit else "neutral")
        set_check(audit["repository"], audit["commit"], state,
                  f"Veriq {audit['overall_score']}/100 · {audit['approval'].get('decision')} · {audit_id}")
    except Exception:
        pass
    log_event("AUDIT_COMPLETED", audit_id=audit_id, score=score,
              fixes=audit["fixes"], approval=audit["approval"].get("decision"))
    if security.get("status") == "fail" and cfg["security"]["block_on_critical"]:
        return 1 if sev_counts.get("CRITICAL", 0) else 0
    return 0


def _deterministic_fallback_findings(evidence: dict) -> list[dict]:
    """NIM unavailable: convert tool failures 1:1 into findings. Never claim AI analysis."""
    findings, i = [], 1
    for f in evidence.get("security", {}).get("findings", []):
        findings.append({"id": f"SEC-{i:03d}", "severity": f.get("severity", "HIGH"),
                         "category": "security", "title": f"Deterministic: {f.get('rule')}",
                         "description": "Scanner hit (AI unavailable).", "file": f.get("file", ""),
                         "line": None, "evidence": str(f), "recommendation": "Human review required.",
                         "auto_fixable": False, "confidence": 0.6, "needs_human_review": True})
        i += 1
    for name, cat in (("tests", "tests"), ("build", "build"), ("static", "code")):
        block = evidence.get(name, {})
        if block.get("status") == "fail":
            findings.append({"id": f"{cat[:3].upper()}-{i:03d}", "severity": "MEDIUM", "category": cat,
                             "title": f"Deterministic: {name} failing", "description": str(block.get("output", block))[:500],
                             "file": "", "line": None, "evidence": str(block)[:1000],
                             "recommendation": "Fix the failing check; AI analysis unavailable.",
                             "auto_fixable": False, "confidence": 0.7, "needs_human_review": True})
            i += 1
    return findings


def _run_fix_flow(audit: dict, loop: AgentLoop, policy: Policy, target: Path, cfg: dict, artifacts: Path) -> None:
    from github.patches import apply_patches_on_bot_branch, read_file_map, workspace_dirty
    from agent.permissions import check_patch_allowed
    approved = audit["approval"].get("decision") == "approved"
    if not approved:
        return
    dirty = workspace_dirty(target)
    if dirty:
        # Git safety: never build on top of someone's uncommitted work.
        audit["fixes"]["failed"].append("workspace not clean — refusing to auto-fix")
        log_event("FIX_REFUSED_DIRTY_WORKSPACE", files=len(dirty.splitlines()))
        return
    fixable = [f for f in audit["findings"] if f.get("auto_fixable") and not f.get("needs_human_review")]
    max_attempts: int = cfg["agent"]["max_repair_attempts"]
    audit["fixes"]["approved"] = len(fixable)
    log_event("FIX_STARTED", count=len(fixable))
    for attempt in range(1, max_attempts + 1):
        try:
            wanted = [f for f in fixable if f["id"] not in audit["fixes"].get("done", [])]
            if not wanted:
                break
            files = read_file_map(target, wanted)
            plan = loop.request_fix(wanted, files)
            patches = plan.get("patches", [])
            paths = [p.get("path", "") for p in patches]
            ok, reason = check_patch_allowed(paths, policy)
            if not ok:
                audit["fixes"]["failed"].append(f"attempt {attempt}: {reason}")
                continue
            before = {"tests": audit["tests"].get("status"), "build": audit["build"].get("status")}
            res = apply_patches_on_bot_branch(target, audit, patches)
            if not res.get("branch"):
                audit["fixes"]["failed"].append(f"attempt {attempt}: no patch applied")
                continue
            audit["fixes"]["branch"] = res["branch"]
            audit["fixes"]["fix_commit"] = res.get("commit", "")
            log_event("FIX_APPLIED", attempt=attempt, files=len(paths), branch=res["branch"])
            # Re-verify with deterministic tools against the REAL detected project
            from scanners.project_detector import detect_project as _detect, to_dict as _to_dict
            from scanners.test_runner import run_tests
            from scanners.build_runner import run_build
            real_project = _to_dict(_detect(target))
            after_tests = run_tests(target, real_project)
            after_build = run_build(target, real_project)
            after = {"tests": after_tests.get("status"), "build": after_build.get("status")}
            ver = loop.verify(wanted[0], before, after) if wanted else {"fixed": False}
            if ver.get("fixed") and after_tests.get("status") != "fail":
                audit["fixes"]["done"] = audit["fixes"].get("done", []) + [w["id"] for w in wanted[:len(patches)]]
                audit["fixes"]["fixed"] = len(audit["fixes"]["done"])
            else:
                audit["fixes"]["failed"].append(f"attempt {attempt}: verification failed")
        except Exception as e:
            audit["fixes"]["failed"].append(f"attempt {attempt}: {redact_text(str(e))}")
    if audit["fixes"]["fixed"] < audit["fixes"]["approved"]:
        audit["fixes"]["failed"].append("AI FIX FAILED — human review required." if audit["fixes"]["failed"] else "")
    audit["verification"]["status"] = "pass" if audit["fixes"]["fixed"] else "open"
    log_event("VERIFICATION_COMPLETED", fixed=audit["fixes"]["fixed"])


if __name__ == "__main__":
    raise SystemExit(main())
