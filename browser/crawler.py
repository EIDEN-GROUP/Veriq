"""Playwright crawler: discover routes, visit each viewport, collect console/network/a11y/DOM."""
from __future__ import annotations

import json
from pathlib import Path

VIEWPORTS = {"desktop": {"width": 1440, "height": 900},
             "tablet": {"width": 768, "height": 1024},
             "mobile": {"width": 375, "height": 812}}


def discover_routes(root: Path, base_url: str, max_routes: int = 15) -> list[str]:
    routes = ["/"]
    # Next.js App Router discovery
    app = root / "app"
    if app.exists():
        for p in app.rglob("page.*"):
            rel = p.parent.relative_to(app).as_posix()
            routes.append("/" + rel if rel != "." else "/")
    # pages/ directory discovery
    pages = root / "pages"
    if pages.exists():
        for p in pages.rglob("*.{js,jsx,ts,tsx}"):
            name = p.stem
            if name.startswith("_") or name == "api":
                continue
            routes.append("/" + ("" if name == "index" else name))
    # sitemap fallback
    sm = root / "public" / "sitemap.xml"
    if sm.exists():
        try:
            import re
            for m in re.findall(r"<loc>(.*?)</loc>", sm.read_text())[:max_routes]:
                routes.append("/" + m.split("/", 3)[-1])
        except OSError:
            pass
    seen, uniq = set(), []
    for r in routes:
        if r not in seen:
            seen.add(r)
            uniq.append(r)
    return uniq[:max_routes]


def crawl(base_url: str, routes: list[str], out_dir: Path) -> dict:
    """Visit routes x viewports with Playwright sync API. Playwright failure -> unavailable, never fake PASS."""
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"status": "unavailable", "reason": "playwright not installed"}
    evidence: dict = {"status": "pass", "pages": []}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for route in routes:
                for vp_name, vp in VIEWPORTS.items():
                    page_rec: dict = {"route": route, "viewport": vp_name}
                    try:
                        ctx = browser.new_context(viewport=vp)
                        page = ctx.new_page()
                        console, failed = [], []
                        page.on("console", lambda m, _c=console: _c.append(f"{m.type}: {m.text[:300]}"))
                        page.on("response", lambda r, _f=failed: _f.append(f"{r.status} {r.url[:200]}") if r.status >= 400 else None)
                        page.goto(base_url + route, wait_until="networkidle", timeout=30000)
                        shot = out_dir / f"{route.strip('/').replace('/', '_') or 'index'}-{vp_name}.png"
                        page.screenshot(path=str(shot), full_page=False)
                        page_rec.update({
                            "screenshot": str(shot), "console": console[:50],
                            "failed_requests": failed[:50],
                            "title": page.title()[:200],
                            "overflow_x": page.evaluate(
                                "() => document.documentElement.scrollWidth > window.innerWidth"),
                        })
                        # axe accessibility (best-effort)
                        try:
                            from axe_selenium_python import Axe  # type: ignore
                            page_rec["a11y_note"] = "axe via playwright: see accessibility.py"
                        except ImportError:
                            page_rec["a11y_note"] = "axe unavailable"
                        ctx.close()
                    except Exception as e:
                        page_rec["error"] = str(e)[:500]
                        evidence["status"] = "issues"
                    evidence["pages"].append(page_rec)
            browser.close()
    except Exception as e:
        return {"status": "unavailable", "reason": f"playwright error: {e}"}
    if any(p.get("console") or p.get("failed_requests") or p.get("error") for p in evidence["pages"]):
        evidence["status"] = "issues"
    return evidence
