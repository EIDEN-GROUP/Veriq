"""Frontend framework detection (Next/React/Vite/Vue/Nuxt/Angular/Svelte/Astro/Remix)."""
from __future__ import annotations

from pathlib import Path


def detect_frontend(root: Path, project: dict) -> dict:
    fe = project.get("frontend", {})
    if fe.get("detected"):
        return {"detected": True, **fe}
    # Fallback: static markers even without package.json (e.g. pure HTML preview)
    markers = {"next.config": "Next.js", "nuxt.config": "Nuxt", "angular.json": "Angular",
               "astro.config": "Astro", "remix.config": "Remix", "svelte.config": "SvelteKit",
               "vite.config": "Vite"}
    for name, fw in markers.items():
        if list(root.glob(f"{name}.*")):
            return {"detected": True, "framework": fw, "build_cmd": "", "start_cmd": ""}
    return {"detected": False, "framework": ""}
