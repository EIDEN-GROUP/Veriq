"""Deterministic agent loop: bounded NIM iterations, schema validation, no infinite loops."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import jsonschema

from agent.nim_client import NimClient
from agent.tool_registry import ToolRegistry


def _load(name: str) -> str:
    return (Path(__file__).resolve().parent.parent / "prompts" / name).read_text()


@dataclass
class AgentLoop:
    nim: NimClient
    tools: ToolRegistry
    max_steps: int = 8

    def _schema(self, name: str) -> dict:
        p = Path(__file__).resolve().parent / "schemas" / name
        return json.loads(p.read_text())

    def audit(self, project: dict, evidence: dict) -> dict:
        """Single structured audit call + schema validation + hallucination guard."""
        task = _load("audit.md")
        out = self.nim.complete_json(task, {"project": project, "evidence": evidence}, reasoning=True)
        findings = out.get("findings", [])
        if not isinstance(findings, list):
            raise ValueError("NIM audit.findings must be a list")
        schema = self._schema("finding.schema.json")
        valid = []
        for f in findings:
            try:
                jsonschema.validate(f, schema)
            except jsonschema.ValidationError:
                continue  # drop schema-invalid findings rather than failing whole audit
            # Hallucination guard: evidence + file required unless INFO.
            if f.get("severity") != "INFO" and (not f.get("evidence") or not f.get("file")):
                f["needs_human_review"] = True
                f["auto_fixable"] = False
            if not f.get("evidence") or len(str(f["evidence"]).strip()) < 5:
                f["needs_human_review"] = True
                f["auto_fixable"] = False
            valid.append(f)
        out["findings"] = valid
        return out

    def request_fix(self, findings: list[dict], files: dict[str, str]) -> dict:
        task = _load("fixer.md")
        out = self.nim.complete_json(task, {"findings": findings, "files": files}, reasoning=True)
        patches = out.get("patches", [])
        if not isinstance(patches, list):
            raise ValueError("NIM fixer.patches must be a list")
        return out

    def verify(self, finding: dict, before: dict, after: dict) -> dict:
        task = _load("verifier.md")
        return self.nim.complete_json(
            task, {"finding": finding, "before": before, "after": after}, reasoning=False)
