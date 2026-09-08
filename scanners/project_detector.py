"""Polyglot project detection. Phase 1: full Node/Python/Docker/frontend.
Phase 2 languages return detected=false with a clear reason (interface-stable)."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class ProjectInfo:
    languages: list[str] = field(default_factory=list)
    package_managers: list[str] = field(default_factory=list)
    frontend: dict = field(default_factory=dict)  # {detected, framework, build_cmd, start_cmd}
    scripts: dict = field(default_factory=dict)   # available repo scripts/commands
    build_system: str = ""
    notes: list[str] = field(default_factory=list)


def detect_project(root: Path) -> ProjectInfo:
    info = ProjectInfo()
    pkg = root / "package.json"
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text())
        except json.JSONDecodeError:
            data = {}
        info.languages.append("node")
        deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
        if (root / "pnpm-lock.yaml").exists():
            info.package_managers.append("pnpm")
        elif (root / "yarn.lock").exists():
            info.package_managers.append("yarn")
        elif (root / "bun.lockb").exists():
            info.package_managers.append("bun")
        else:
            info.package_managers.append("npm")
        info.scripts = data.get("scripts", {})
        info.frontend = _detect_frontend(deps, data)
    if (root / "pyproject.toml").exists() or (root / "requirements.txt").exists() or (root / "setup.py").exists():
        info.languages.append("python")
        text = ((root / "pyproject.toml").read_text() if (root / "pyproject.toml").exists() else "")
        if "poetry" in text:
            info.package_managers.append("poetry")
        elif (root / "uv.lock").exists():
            info.package_managers.append("uv")
        else:
            info.package_managers.append("pip")
    if (root / "Dockerfile").exists() or (root / "docker-compose.yml").exists():
        info.notes.append("containerized")
    for marker, lang in [("go.mod", "go"), ("Cargo.toml", "rust"), ("pom.xml", "java-maven"),
                         ("build.gradle", "java-gradle"), ("Gemfile", "ruby"),
                         ("composer.json", "php")]:
        if (root / marker).exists():
            info.notes.append(f"{lang}: marker present, phase-2 support (interface-stable)")
    if list(root.glob("*.csproj")) or list(root.glob("*.sln")):
        info.notes.append("dotnet: marker present, phase-2 support (interface-stable)")
    return info


def _detect_frontend(deps: dict, pkg: dict) -> dict:
    out = {"detected": False, "framework": "", "build_cmd": "", "start_cmd": ""}
    table = [("next", "Next.js"), ("nuxt", "Nuxt"), ("@angular/core", "Angular"),
             ("svelte", "Svelte"), ("astro", "Astro"), ("@remix-run/react", "Remix"),
             ("vite", "Vite"), ("vue", "Vue"), ("react", "React")]
    for dep, name in table:
        if dep in deps:
            out.update(detected=True, framework=name)
            break
    scripts = pkg.get("scripts", {})
    if out["detected"]:
        out["build_cmd"] = scripts.get("build", "")
        out["start_cmd"] = scripts.get("start", scripts.get("preview", scripts.get("dev", "")))
    return out


def to_dict(info: ProjectInfo) -> dict:
    return asdict(info)
