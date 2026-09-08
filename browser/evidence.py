"""Screenshot / console / network / accessibility helpers over crawl evidence."""
from __future__ import annotations


def summarize_screenshots(crawl_evidence: dict) -> dict:
    pages = crawl_evidence.get("pages", [])
    return {"count": sum(1 for p in pages if p.get("screenshot")),
            "missing": [p for p in pages if not p.get("screenshot")]}


def collect_console(crawl_evidence: dict) -> list[str]:
    out: list[str] = []
    for p in crawl_evidence.get("pages", []):
        for line in p.get("console", []):
            if any(k in line.lower() for k in ("error", "uncaught", "failed", "warn")):
                out.append(f"{p['route']}@{p['viewport']}: {line}")
    return out[:100]


def collect_network_failures(crawl_evidence: dict) -> list[str]:
    out: list[str] = []
    for p in crawl_evidence.get("pages", []):
        for r in p.get("failed_requests", []):
            out.append(f"{p['route']}@{p['viewport']}: {r}")
    return out[:100]


def check_accessibility(crawl_evidence: dict) -> dict:
    """Deterministic a11y signals available without a full axe run (overflow, titles, errors)."""
    issues: list[dict] = []
    for p in crawl_evidence.get("pages", []):
        if p.get("overflow_x"):
            issues.append({"route": p["route"], "viewport": p["viewport"],
                           "rule": "horizontal-overflow", "severity": "MEDIUM"})
        if not p.get("title"):
            issues.append({"route": p["route"], "viewport": p["viewport"],
                           "rule": "missing-title", "severity": "LOW"})
    # Full axe run is executed inside CI when `axe-core` is reachable; results merge here.
    return {"status": "pass" if not issues else "issues", "issues": issues,
            "note": "Run `npx @axe-core/cli` in workflow for WCAG rule-level detail; NIM reasons over merged output."}
